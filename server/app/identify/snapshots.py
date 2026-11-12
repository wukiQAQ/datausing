"""单进程、短寿命、按浏览器会话隔离的检测快照；不写原文到磁盘。"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field, fields, is_dataclass
import hashlib
import secrets
import sys
import threading
import time

from .. import security


def fingerprint(value: str | bytes) -> str:
    return hashlib.sha256(value.encode('utf-8') if isinstance(value, str) else value).hexdigest()


def memory_size(value, limit: int) -> int:
    total, seen, pending = 0, set(), [value]
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        total += sys.getsizeof(item)
        if total > limit:
            return total
        if is_dataclass(item):
            pending.extend(getattr(item, f.name) for f in fields(item))
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
    return total


@dataclass
class Snapshot:
    result_id: str
    owner: str
    source_fingerprint: str
    ruleset_revision: str
    policy_version: str
    key_revision: str
    expires_at: float
    document: object
    report: object
    options: dict
    size: int
    rendered: dict = field(default_factory=dict)


class SnapshotStore:
    def __init__(self, max_items=32, max_bytes=32 << 20, ttl=300, clock=time.monotonic, reap_interval=1):
        self.max_items, self.max_bytes, self.ttl, self.clock = max_items, max_bytes, ttl, clock
        self.total_bytes = 0
        self._items = OrderedDict()
        self._lock = threading.RLock()
        self.reap_interval = reap_interval
        self._stop = threading.Event()
        self._reaper = None
        self._users = 0

    def start(self):
        with self._lock:
            self._users += 1
            if self._reaper is None:
                self._stop = threading.Event()
                self._reaper = threading.Thread(target=self._reap, args=(self._stop,),
                                                name='flowmask-snapshot-reaper', daemon=True)
                self._reaper.start()

    def _reap(self, stop):
        while not stop.wait(self.reap_interval):
            with self._lock:
                self._expire()

    def close(self):
        with self._lock:
            self._users = max(0, self._users - 1)
            if self._users:
                return
            thread = self._reaper
            self._reaper = None
            self._stop.set()
        if thread is not None:
            thread.join(timeout=2)
        self.clear()

    def _remove(self, key):
        self.total_bytes -= self._items.pop(key).size

    def _expire(self):
        for key in list(self._items):
            if self._items[key].expires_at <= self.clock():
                self._remove(key)

    def clear(self):
        with self._lock:
            self._items.clear()
            self.total_bytes = 0

    def put(self, document, report, options, source_hash, revision, owner, secret, result_id=None):
        size = memory_size((document, report, options), self.max_bytes)
        with self._lock:
            self._expire()
            if size > self.max_bytes or self.max_items < 1:
                raise security.SecurityError('SNAPSHOT_CAPACITY', '检测快照超过内存预算，请缩小输入', 503)
            # 同 result_id 覆盖（如任务重试）先释放旧条目的记账，避免预算虚高提前淘汰。
            if result_id and result_id in self._items:
                self._remove(result_id)
            while self._items and (len(self._items) >= self.max_items or self.total_bytes + size > self.max_bytes):
                self._remove(next(iter(self._items)))
            entry = Snapshot(result_id or secrets.token_urlsafe(32), owner, source_hash, revision,
                             security.POLICY_VERSION, fingerprint(secret), self.clock() + self.ttl,
                             deepcopy(document), deepcopy(report), deepcopy(options), size)
            self._items[entry.result_id] = entry
            self.total_bytes += size
            return deepcopy(entry)

    def _get(self, result_id, owner, secret, policy_version=None):
        self._expire()
        entry = self._items.get(result_id)
        if entry is None:
            raise security.SecurityError('SNAPSHOT_EXPIRED', '检测快照已过期或重启失效，请重新检测', 410)
        if not owner or not secrets.compare_digest(entry.owner, owner):
            raise security.SecurityError('SNAPSHOT_FORBIDDEN', '当前会话不能访问该检测快照', 403)
        if (entry.policy_version != security.POLICY_VERSION or entry.key_revision != fingerprint(secret)
                or policy_version is not None and policy_version != entry.policy_version):
            raise security.SecurityError('SNAPSHOT_POLICY_CHANGED', '脱敏策略版本已变化，请重新检测', 409)
        return entry

    def get(self, result_id, owner, secret, policy_version=None):
        with self._lock:
            return deepcopy(self._get(result_id, owner, secret, policy_version))

    def render(self, result_id, owner, secret, strategy, policy_version=None):
        from ..tasks.document_export import _masked_texts
        with self._lock:
            entry = self._get(result_id, owner, secret, policy_version)
            if strategy not in entry.rendered:
                try:
                    masked = _masked_texts(entry.document, entry.report, strategy, secret)
                except security.SecurityError:
                    raise
                except Exception:
                    raise security.SecurityError('MASKING_FAILED', '快照脱敏失败，未返回原文') from None
                size = memory_size(masked, self.max_bytes)
                if entry.size + size > self.max_bytes:
                    raise security.SecurityError('SNAPSHOT_CAPACITY', '脱敏预览超过内存预算', 503)
                while self.total_bytes + size > self.max_bytes:
                    other = next(key for key in self._items if key != result_id)
                    self._remove(other)
                entry.rendered[strategy] = masked
                entry.size += size
                self.total_bytes += size
            return deepcopy(entry), deepcopy(entry.rendered[strategy])


_store = SnapshotStore()


def get_snapshot_store() -> SnapshotStore:
    return _store
