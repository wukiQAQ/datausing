"""把一份 pcap 变成「可交给识别链路的文档」。

流程：字节 → 报文 → 链路层帧 → 五元组会话 → HTTP 报文 → ``Document(segments=...)``。
之后复用 ``tasks.create_task`` 的既有链路（落库 + findings + 审计链），
所以流量检测结果自动出现在任务列表、统计看板与审计页里，不需要第二套存储。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..parsers.base import Document, Segment
from .frames import parse_ethernet
from .pcap import iter_packets
from .streams import collect_sessions, split_messages


class TrafficParseError(ValueError):
    """pcap 里没有可用的载荷（或全部解不出来）。"""


@dataclass(frozen=True, slots=True)
class PcapSummary:
    """导入摘要，接口直接回给前端。"""

    packets: int
    decoded: int
    sessions: int
    messages: int
    segments: int
    error: str = ""


def build_document(data: bytes, *, source_name: str = "upload.pcap") -> tuple[Document, PcapSummary]:
    """解析 pcap 并组装 Document；解析不出任何正文时抛 TrafficParseError。"""
    # PcapError 原样抛出（接口层据此回 415），TrafficParseError 表示"格式对但没内容"（422）
    packets = list(iter_packets(data))
    if not packets:
        raise TrafficParseError("pcap 里没有任何报文")

    frames = []
    for raw in packets:
        frame = parse_ethernet(raw.ts, raw.frame)
        if frame is not None:
            frames.append(frame)

    sessions = collect_sessions(frames)
    segments: list[Segment] = []
    messages = 0
    for session in sessions:
        for message in split_messages(session):
            body = message.body.strip()
            if not body:
                continue
            messages += 1
            segments.append(Segment(path=message.label, text=message.body))

    if not segments:
        raise TrafficParseError("pcap 里没有可解析的载荷（可能只有握手帧或加密流量）")

    document = Document(
        source=f"pcap:{source_name}",
        content_type="pcap",
        segments=segments,
        raw="",
        meta={
            "format": "pcap",
            "packets": len(packets),
            "decoded_frames": len(frames),
            "sessions": len(sessions),
            "messages": messages,
        },
    )
    summary = PcapSummary(
        packets=len(packets),
        decoded=len(frames),
        sessions=len(sessions),
        messages=messages,
        segments=len(segments),
    )
    return document, summary
