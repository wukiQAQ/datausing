"""实时旁路：跟随「正在被追加写」的 pcap 文件，按时间窗口增量检测。

为什么是「跟随文件」而不是直接抓网卡：

- 抓网卡需要 Npcap/libpcap + 管理员权限，且要引入 C 绑定；服务端保持纯 Python/标准库，
  换机部署零成本。真正的抓包交给外部工具（Wireshark 的持续写入、`dumpcap -w`、
  或仓库里的 Go 采集端），服务端只负责"看到新正文就检测"。
- 旁路口径：不串在业务链路上，跟随失败只记录错误，不影响已落库的结论。

实现要点：
- ``PcapFollower`` 增量解析：记住文件偏移，只解析"凑齐的整条记录"，半条记录留在缓冲区等下次；
- 每 ``interval_ms`` 轮询一次，把最近 ``MAX_RECENT_FRAMES`` 帧拼回会话（跨窗口的迟到包也能拼上），
  用 ``(会话标签, 正文哈希)`` 去重，只把"新正文"送进识别链路；
- 每 ``window_s`` 秒把窗口内的新正文合成一个任务（复用 ``tasks.create_task``），
  于是检测结果自动进入任务页/看板/审计链，和文件导入完全同构。
"""

from __future__ import annotations

import hashlib
import struct
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..core.db import Database
from ..identify import DetectOptions, DetectionPipeline
from ..parsers.base import Document, Segment
from ..tasks.service import create_task
from .frames import Frame, parse_ethernet
from .pcap import PCAP_MAGIC_NSEC, PCAP_MAGIC_USEC, PcapError, RawPacket
from .streams import collect_sessions, split_messages

#: 最近保留的帧数：用来把跨窗口的迟到包拼回同一会话（按 1500B 帧算约 7.5MB 内存）
MAX_RECENT_FRAMES = 5000
#: 单个窗口最多提交的正文条数，防止一个窗口把识别链路打满
MAX_SEGMENTS_PER_WINDOW = 60
#: 单帧上限（jumbo frame 也够）
MAX_FRAME_BYTES = 1 << 16


class LiveError(ValueError):
    """实时跟随不可用（文件格式不支持、路径不合法等）。"""


@dataclass(frozen=True, slots=True)
class LiveStats:
    """一次实时跟随的可观测状态（接口直接回给前端）。"""

    live_id: str
    pcap_path: str
    source: str
    running: bool
    started_at: float
    stopped_at: float | None
    interval_ms: int
    window_s: float
    packets: int
    segments: int
    tasks: int
    last_task_id: str | None
    last_error: str

    def to_dict(self) -> dict[str, object]:
        return {
            "live_id": self.live_id,
            "pcap_path": self.pcap_path,
            "source": self.source,
            "running": self.running,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "interval_ms": self.interval_ms,
            "window_s": self.window_s,
            "packets": self.packets,
            "segments": self.segments,
            "tasks": self.tasks,
            "last_task_id": self.last_task_id,
            "last_error": self.last_error,
        }


class PcapFollower:
    """增量读取持续写入的 pcap 文件。

    只支持经典 pcap：pcapng 的块是变长的、跟随实现容易踩坑，遇到时明确报错让用户改用文件导入。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._buffer = b""
        self._offset = 0
        self._header_ready = False
        self._endian = "<"
        self._nano = False
        self._linktype = 1

    @property
    def linktype(self) -> int:
        return self._linktype

    def poll(self) -> list[RawPacket]:
        """读取自上次以来新增的完整报文；文件还不存在时返回空列表（等待抓包工具落盘）。"""
        if not self.path.exists():
            return []
        with self.path.open("rb") as handle:
            handle.seek(self._offset)
            chunk = handle.read()
            self._offset += len(chunk)
        if chunk:
            self._buffer += chunk

        if not self._header_ready:
            if len(self._buffer) < 24:
                return []
            self._parse_header()

        packets: list[RawPacket] = []
        while len(self._buffer) >= 16:
            ts_sec, ts_frac, incl_len, _orig_len = struct.unpack(
                self._endian + "IIII", self._buffer[:16]
            )
            if incl_len > MAX_FRAME_BYTES:
                raise LiveError(f"帧长度 {incl_len} 超过上限，可能不是 pcap 文件")
            if len(self._buffer) < 16 + incl_len:
                break  # 半条记录：等下一次追加
            frame = self._buffer[16 : 16 + incl_len]
            self._buffer = self._buffer[16 + incl_len :]
            scale = 1_000_000_000 if self._nano else 1_000_000
            packets.append(
                RawPacket(ts=ts_sec + ts_frac / scale, linktype=self._linktype, frame=frame)
            )
        return packets

    def _parse_header(self) -> None:
        magic_le = int.from_bytes(self._buffer[:4], "little")
        magic_be = int.from_bytes(self._buffer[:4], "big")
        if magic_le in (PCAP_MAGIC_USEC, PCAP_MAGIC_NSEC):
            self._endian = "<"
            self._nano = magic_le == PCAP_MAGIC_NSEC
        elif magic_be in (PCAP_MAGIC_USEC, PCAP_MAGIC_NSEC):
            self._endian = ">"
            self._nano = magic_be == PCAP_MAGIC_NSEC
        else:
            raise LiveError("实时跟随只支持经典 pcap；pcapng 请用「文件导入」")
        try:
            _, _, _, _, _, _, linktype = struct.unpack(self._endian + "IHHiIII", self._buffer[:24])
        except struct.error as exc:  # pragma: no cover - 24 字节已校验，理论上到不了
            raise PcapError(f"pcap 全局头不完整: {exc}") from exc
        self._linktype = linktype
        self._buffer = self._buffer[24:]
        self._header_ready = True


@dataclass
class _LiveSession:
    """一个正在跑的跟随线程及其状态。"""

    live_id: str
    pcap_path: Path
    source: str
    interval_ms: int
    window_s: float
    options: DetectOptions
    db: Database
    pipeline: DetectionPipeline
    thread: threading.Thread | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    started_at: float = field(default_factory=time.time)
    stopped_at: float | None = None
    packets: int = 0
    segments: int = 0
    tasks: int = 0
    last_task_id: str | None = None
    last_error: str = ""
    _recent: list[Frame] = field(default_factory=list)
    _seen: set[str] = field(default_factory=set)
    _pending: list[Segment] = field(default_factory=list)
    _window_started: float = field(default_factory=time.time)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def snapshot(self) -> LiveStats:
        with self._lock:
            return LiveStats(
                live_id=self.live_id,
                pcap_path=str(self.pcap_path),
                source=self.source,
                running=self.thread is not None and self.thread.is_alive(),
                started_at=self.started_at,
                stopped_at=self.stopped_at,
                interval_ms=self.interval_ms,
                window_s=self.window_s,
                packets=self.packets,
                segments=self.segments,
                tasks=self.tasks,
                last_task_id=self.last_task_id,
                last_error=self.last_error,
            )


class LiveManager:
    """进程内单例：管理若干实时跟随会话。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, _LiveSession] = {}

    def start(
        self,
        *,
        db: Database,
        pipeline: DetectionPipeline,
        pcap_path: Path,
        interval_ms: int,
        window_s: float,
        options: DetectOptions,
        source: str = "",
    ) -> LiveStats:
        resolved = pcap_path.resolve()
        with self._lock:
            for session in self._sessions.values():
                if session.pcap_path == resolved and session.thread is not None and session.thread.is_alive():
                    raise LiveError(f"该文件已在跟随中：{session.live_id}")
        live_id = uuid.uuid4().hex[:12]
        session = _LiveSession(
            live_id=live_id,
            pcap_path=resolved,
            source=source or f"live:{resolved.name}",
            interval_ms=interval_ms,
            window_s=window_s,
            options=options,
            db=db,
            pipeline=pipeline,
            _window_started=time.time(),
        )
        session.thread = threading.Thread(
            target=_run_follow, args=(session,), name=f"live-{live_id}", daemon=True
        )
        with self._lock:
            self._sessions[live_id] = session
        session.thread.start()
        return session.snapshot()

    def stop(self, live_id: str) -> LiveStats | None:
        with self._lock:
            session = self._sessions.get(live_id)
        if session is None:
            return None
        session.stop_event.set()
        if session.thread is not None:
            session.thread.join(timeout=5.0)
        _flush_window(session)  # 收尾：把窗口里剩的新正文也落库
        with session._lock:
            session.stopped_at = time.time()
        return session.snapshot()

    def stop_all(self) -> None:
        with self._lock:
            ids = list(self._sessions)
        for live_id in ids:
            try:
                self.stop(live_id)
            except Exception:  # pragma: no cover - 退出路径尽量不抛
                continue

    def list(self) -> list[LiveStats]:
        with self._lock:
            sessions = list(self._sessions.values())
        return [session.snapshot() for session in sessions]

    def clear_stopped(self) -> int:
        """清掉已停止的会话记录（前端"清空"按钮用）。"""
        with self._lock:
            stopped = [
                live_id
                for live_id, session in self._sessions.items()
                if session.thread is None or not session.thread.is_alive()
            ]
            for live_id in stopped:
                del self._sessions[live_id]
        return len(stopped)


_manager = LiveManager()


def get_live_manager() -> LiveManager:
    return _manager


def reset_live_manager_for_tests() -> LiveManager:
    """测试隔离用：停掉所有线程并换一个新 manager。"""
    global _manager
    _manager.stop_all()
    _manager = LiveManager()
    return _manager


def _run_follow(session: _LiveSession) -> None:
    follower = PcapFollower(session.pcap_path)
    while not session.stop_event.is_set():
        session.stop_event.wait(session.interval_ms / 1000.0)
        if session.stop_event.is_set():
            break
        try:
            packets = follower.poll()
            if packets:
                _ingest_packets(session, packets)
        except (LiveError, PcapError, OSError) as exc:
            with session._lock:
                session.last_error = f"{type(exc).__name__}: {exc}"
            session.stop_event.wait(1.0)  # 出错退避，避免刷屏
            continue
        if time.time() - session._window_started >= session.window_s:
            _flush_window(session)
    _flush_window(session)


def _ingest_packets(session: _LiveSession, packets: list[RawPacket]) -> None:
    frames: list[Frame] = []
    for raw in packets:
        frame = parse_ethernet(raw.ts, raw.frame)
        if frame is not None:
            frames.append(frame)
    with session._lock:
        session.packets += len(packets)
        session._recent.extend(frames)
        if len(session._recent) > MAX_RECENT_FRAMES:
            del session._recent[: len(session._recent) - MAX_RECENT_FRAMES]

    # 用"最近帧"整体重组：跨窗口的迟到包也能拼进同一会话
    with session._lock:
        recent = list(session._recent)
    new_segments: list[Segment] = []
    for flow in collect_sessions(recent):
        for message in split_messages(flow):
            body = message.body.strip()
            if not body:
                continue
            key = hashlib.sha1(f"{message.label}\x00{body}".encode("utf-8", "replace")).hexdigest()
            with session._lock:
                if key in session._seen:
                    continue
                session._seen.add(key)
            new_segments.append(Segment(path=message.label, text=message.body))
    if not new_segments:
        return
    with session._lock:
        session._pending.extend(new_segments)


def _flush_window(session: _LiveSession) -> None:
    with session._lock:
        pending = session._pending[:MAX_SEGMENTS_PER_WINDOW]
        session._pending = session._pending[MAX_SEGMENTS_PER_WINDOW:]
        session._window_started = time.time()
    if not pending:
        return
    document = Document(
        source=session.source,
        content_type="pcap-live",
        segments=pending,
        raw="",
        meta={
            "format": "pcap-live",
            "live_id": session.live_id,
            "pcap_path": str(session.pcap_path),
            "window_seconds": session.window_s,
        },
    )
    try:
        task_id = create_task(
            session.db,
            source=session.source,
            content_type="pcap-live",
            document=document,
            options=session.options,
            pipeline=session.pipeline,
        )
    except Exception as exc:  # 旁路失败不影响跟随本身
        with session._lock:
            session.last_error = f"create_task: {exc}"
        return
    with session._lock:
        session.tasks += 1
        session.segments += len(pending)
        session.last_task_id = task_id
