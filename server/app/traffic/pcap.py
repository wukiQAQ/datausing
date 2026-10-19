"""pcap / pcapng 读取（纯标准库，无第三方抓包依赖）。

只做两件事：把文件拆成「时间戳 + 原始链路层帧」，以及判断文件格式。
上层 `frames.py` 负责以太网/IP/TCP 解码，`importer.py` 负责跑识别链路。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterator

PCAP_MAGIC_USEC = 0xA1B2C3D4
PCAP_MAGIC_NSEC = 0xA1B23C4D
PCAPNG_MAGIC = 0x0A0D0D0A


class PcapError(ValueError):
    """pcap 内容不合法（格式不对、被截断等）。"""


@dataclass(frozen=True)
class RawPacket:
    ts: float
    linktype: int
    frame: bytes


def detect_format(data: bytes) -> str:
    """返回 ``pcap`` / ``pcapng``，不认识则抛 PcapError。"""
    if len(data) < 4:
        raise PcapError("文件太短，不像是 pcap")
    magic_le = int.from_bytes(data[:4], "little")
    magic_be = int.from_bytes(data[:4], "big")
    if magic_le in (PCAP_MAGIC_USEC, PCAP_MAGIC_NSEC) or magic_be in (PCAP_MAGIC_USEC, PCAP_MAGIC_NSEC):
        return "pcap"
    if magic_le == PCAPNG_MAGIC:
        return "pcapng"
    raise PcapError("无法识别的抓包格式（支持 pcap / pcapng）")


def iter_packets(data: bytes) -> Iterator[RawPacket]:
    """按时间顺序产出报文；两种格式都走同一个入口。"""
    fmt = detect_format(data)
    if fmt == "pcap":
        yield from _iter_pcap(data)
    else:
        yield from _iter_pcapng(data)


def _iter_pcap(data: bytes) -> Iterator[RawPacket]:
    magic = int.from_bytes(data[:4], "little")
    if magic in (PCAP_MAGIC_USEC, PCAP_MAGIC_NSEC):
        endian = "<"
        nano = magic == PCAP_MAGIC_NSEC
    else:
        endian = ">"
        nano = int.from_bytes(data[:4], "big") == PCAP_MAGIC_NSEC

    if len(data) < 24:
        raise PcapError("pcap 全局头不完整")
    _, _, _, _, _, _, linktype = struct.unpack(endian + "IHHiIII", data[:24])
    offset = 24
    scale = 1_000_000_000 if nano else 1_000_000
    while offset + 16 <= len(data):
        ts_sec, ts_frac, incl_len, _orig_len = struct.unpack(endian + "IIII", data[offset : offset + 16])
        offset += 16
        if incl_len > len(data) - offset:
            raise PcapError("pcap 记录被截断（文件可能不完整）")
        frame = data[offset : offset + incl_len]
        offset += incl_len
        yield RawPacket(ts=ts_sec + ts_frac / scale, linktype=linktype, frame=frame)


def _iter_pcapng(data: bytes) -> Iterator[RawPacket]:
    """最小 pcapng 解析：SHB / IDB / EPB，够读常见工具导出的文件。"""
    offset = 0
    endian = "<"
    interfaces: list[dict[str, int]] = []
    while offset + 12 <= len(data):
        block_type_raw = data[offset : offset + 4]
        if block_type_raw == b"\x0a\x0d\x0d\x0a":  # Section Header Block
            bom_raw = data[offset + 8 : offset + 12]
            endian = "<" if int.from_bytes(bom_raw, "little") == 0x1A2B3C4D else ">"
            block_len = struct.unpack(endian + "I", data[offset + 4 : offset + 8])[0]
            offset += block_len
            interfaces = []
            continue
        block_len = struct.unpack(endian + "I", data[offset + 4 : offset + 8])[0]
        if block_len < 12 or offset + block_len > len(data):
            raise PcapError("pcapng 块长度非法（文件可能不完整）")
        block_type = struct.unpack(endian + "I", data[offset : offset + 4])[0]
        body = data[offset + 8 : offset + block_len - 4]
        if block_type == 0x00000001 and len(body) >= 8:  # Interface Description Block
            linktype = struct.unpack(endian + "H", body[:2])[0]
            interfaces.append({"linktype": linktype})
        elif block_type == 0x00000006 and len(body) >= 20 and interfaces:  # Enhanced Packet Block
            iface_id, ts_high, ts_low, cap_len, _orig_len = struct.unpack(endian + "IIIII", body[:20])
            frame = body[20 : 20 + cap_len]
            linktype = interfaces[iface_id]["linktype"] if iface_id < len(interfaces) else 1
            ts = ((ts_high << 32) | ts_low) / 1_000_000.0
            yield RawPacket(ts=ts, linktype=linktype, frame=frame)
        offset += block_len


def write_pcap(packets: list[tuple[float, bytes]], linktype: int = 1) -> bytes:
    """把报文写成 pcap 字节串（测试与演示数据生成用）。"""
    header = struct.pack("<IHHiIII", PCAP_MAGIC_USEC, 2, 4, 0, 0, 65535, linktype)
    chunks = [header]
    for ts, frame in packets:
        sec = int(ts)
        usec = int(round((ts - sec) * 1_000_000))
        chunks.append(struct.pack("<IIII", sec, usec, len(frame), len(frame)))
        chunks.append(frame)
    return b"".join(chunks)
