"""实时旁路：跟随「正在被追加写」的 pcap 文件，按时间窗口增量检测。

为什么是「跟随文件」而不是直接抓网卡：

- 抓网卡需要 Npcap/libpcap + 管理员权限，且要引入 C 绑定；服务端保持纯 Python/标准库，
  换机部署零成本。真正的抓包交给外部工具（Wireshark 的持续写入、`dumpcap -w`、
  或仓库里的 Go 采集端），服务端只负责"看到新正文就检测"。
- 旁路口径：不串在业务链路上，跟随失败只记录错误，不影响已落库的结论。

实现要点：
- ``PcapFollower`` 增量解析：同时支持经典 pcap 与 pcapng（``dumpcap``/Wireshark 默认格式），
  记住文件偏移，只解析"凑齐的整条记录/整块"，半条留在缓冲区等下次；
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

#: pcapng 常量：Section Header Block 的 magic 是字节序无关的回文，
#: 字节序标记（Byte-Order Magic）放在 SHB 偏移 8 处，用来判断后续块的大小端。
PCAPNG_SHB_MAGIC = b"\x0a\x0d\x0d\x0a"
PCAPNG_BOM = 0x1A2B3C4D
PCAPNG_IDB = 0x00000001
PCAPNG_SPB = 0x00000003
PCAPNG_EPB = 0x00000006
#: 单个 pcapng 块上限，防止损坏文件把内存吃满
MAX_PCAPNG_BLOCK = 16 << 20


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
    """增量读取持续写入的 pcap / pcapng 文件，只产出「凑齐」的报文。

    两种容器都支持：

    - **经典 pcap**：24 字节全局头 + 每帧 16 字节记录头；
    - **pcapng**：块结构（SHB / IDB / EPB / SPB），首块声明字节序，接口描述块声明链路类型。
      ``dumpcap``、Wireshark 默认导出的就是 pcapng，所以实时跟随必须能吃它。

    半条记录/半个块一律留在缓冲区等下一次追加，绝不把不完整的字节当成报文。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._buffer = b""
        self._offset = 0
        self._mode: str | None = None
        # 经典 pcap 状态
        self._header_ready = False
        self._endian = "<"
        self._nano = False
        self._linktype = 1
        # pcapng 状态
        self._ng_known = False
        self._ng_endian = "<"
        self._ng_interfaces: list[int] = []
        self._last_ts = 0.0

    @property
    def linktype(self) -> int:
        return self._linktype

    @property
    def mode(self) -> str:
        """已识别出的容器格式：``pcap`` / ``pcapng``；还没读到文件头时为 ``unknown``。"""
        return self._mode or "unknown"

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

        if self._mode is None:
            if len(self._buffer) < 4:
                return []
            self._mode = "pcapng" if self._buffer[:4] == PCAPNG_SHB_MAGIC else "pcap"
        if self._mode == "pcapng":
            return self._poll_pcapng()
        return self._poll_pcap()

    # ------------------------------------------------------------ 经典 pcap

    def _poll_pcap(self) -> list[RawPacket]:
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
            self._last_ts = ts_sec + ts_frac / scale
            packets.append(
                RawPacket(ts=self._last_ts, linktype=self._linktype, frame=frame)
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
            raise LiveError("无法识别的抓包格式（实时跟随支持 pcap / pcapng）")
        try:
            _, _, _, _, _, _, linktype = struct.unpack(self._endian + "IHHiIII", self._buffer[:24])
        except struct.error as exc:  # pragma: no cover - 24 字节已校验，理论上到不了
            raise PcapError(f"pcap 全局头不完整: {exc}") from exc
        self._linktype = linktype
        self._buffer = self._buffer[24:]
        self._header_ready = True

    # ------------------------------------------------------------ pcapng

    def _poll_pcapng(self) -> list[RawPacket]:
        packets: list[RawPacket] = []

        while True:
            if not self._ng_known:
                if len(self._buffer) < 12:
                    break
                if self._buffer[:4] != PCAPNG_SHB_MAGIC:
                    raise LiveError("pcapng 文件缺少 Section Header Block")
                self._ng_endian = self._detect_pcapng_endian(self._buffer[8:12])
                self._ng_known = True
                self._ng_interfaces = []

            if len(self._buffer) < 12:
                break

            if self._buffer[:4] == PCAPNG_SHB_MAGIC:  # 新 section：可能换字节序
                self._ng_endian = self._detect_pcapng_endian(self._buffer[8:12])
                block_len = struct.unpack(self._ng_endian + "I", self._buffer[4:8])[0]
                if block_len < 28 or block_len > MAX_PCAPNG_BLOCK:
                    raise LiveError(f"pcapng Section Header 块长度非法：{block_len}")
                if len(self._buffer) < block_len:
                    break
                self._ng_interfaces = []
                self._buffer = self._buffer[block_len:]
                continue

            block_len = struct.unpack(self._ng_endian + "I", self._buffer[4:8])[0]
            if block_len < 12 or block_len > MAX_PCAPNG_BLOCK:
                raise LiveError(f"pcapng 块长度非法：{block_len}")
            if len(self._buffer) < block_len:
                break  # 半个块：等下一次追加
            block_type = struct.unpack(self._ng_endian + "I", self._buffer[:4])[0]
            body = self._buffer[8 : block_len - 4]
            packet = self._pcapng_packet(block_type, body)
            if packet is not None:
                packets.append(packet)
            self._buffer = self._buffer[block_len :]

        return packets

    def _pcapng_packet(self, block_type: int, body: bytes) -> RawPacket | None:
        if block_type == PCAPNG_IDB and len(body) >= 8:
            linktype = struct.unpack(self._ng_endian + "H", body[:2])[0]
            self._ng_interfaces.append(linktype)
            if len(self._ng_interfaces) == 1:  # 对外暴露首个接口的链路类型
                self._linktype = linktype
            return None
        if block_type == PCAPNG_EPB and len(body) >= 20:
            iface_id, ts_high, ts_low, cap_len, _orig_len = struct.unpack(
                self._ng_endian + "IIIII", body[:20]
            )
            if cap_len > MAX_FRAME_BYTES:
                raise LiveError(f"帧长度 {cap_len} 超过上限，可能不是 pcapng 文件")
            if len(body) < 20 + cap_len:
                raise LiveError("pcapng EPB 声明长度与实际数据不一致")
            linktype = self._ng_interfaces[iface_id] if iface_id < len(self._ng_interfaces) else 1
            if linktype != 1:
                return None  # 只解以太网，其它链路类型跳过
            ts = ((ts_high << 32) | ts_low) / 1_000_000.0
            self._last_ts = ts
            return RawPacket(ts=ts, linktype=linktype, frame=body[20 : 20 + cap_len])
        if block_type == PCAPNG_SPB and len(body) >= 4:
            data = body[4:]
            if not data:
                return None
            linktype = self._ng_interfaces[0] if self._ng_interfaces else 1
            if linktype != 1:
                return None
            self._last_ts = time.time()  # 简单包块没有时间戳，用当前时间兜底
            return RawPacket(ts=self._last_ts, linktype=linktype, frame=data)
        return None  # NRB / ISB / 自定义块：跳过

    @staticmethod
    def _detect_pcapng_endian(raw: bytes) -> str:
        if int.from_bytes(raw, "little") == PCAPNG_BOM:
            return "<"
        if int.from_bytes(raw, "big") == PCAPNG_BOM:
            return ">"
        raise LiveError("pcapng 字节序标记非法（文件可能损坏）")

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
