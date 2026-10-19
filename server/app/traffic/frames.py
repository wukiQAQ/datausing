"""链路层帧解码：以太网 / VLAN / IPv4 / IPv6 / TCP / UDP → 五元组 + 载荷。

只覆盖演示与常见明文场景需要的部分：不处理 IP 分片重组、不做 TCP 选项解析之外的
特殊处理。解不出来的帧直接跳过（记进统计），不影响整份 pcap 的导入。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

LINKTYPE_ETHERNET = 1

ETH_IPV4 = 0x0800
ETH_IPV6 = 0x86DD
ETH_VLAN = 0x8100
ETH_QINQ = 0x88A8

PROTO_NAMES = {1: "icmp", 6: "tcp", 17: "udp", 58: "icmpv6"}


@dataclass(frozen=True)
class Frame:
    """一帧解码结果；payload 是传输层载荷（可能为空）。"""

    ts: float
    src_ip: str
    dst_ip: str
    src_port: int
    dst_port: int
    proto: str
    payload: bytes
    seq: int = 0
    ack: int = 0
    flags: int = 0


def parse_ethernet(ts: float, frame: bytes) -> Frame | None:
    """解一帧；不是 IPv4/IPv6 或载荷不可用时返回 None。"""
    if len(frame) < 14:
        return None
    ethertype = struct.unpack(">H", frame[12:14])[0]
    offset = 14
    # 最多跳过两层 VLAN 标签
    for _ in range(2):
        if ethertype in (ETH_VLAN, ETH_QINQ) and len(frame) >= offset + 4:
            ethertype = struct.unpack(">H", frame[offset + 2 : offset + 4])[0]
            offset += 4
        else:
            break
    if ethertype == ETH_IPV4:
        return _parse_ipv4(ts, frame[offset:])
    if ethertype == ETH_IPV6:
        return _parse_ipv6(ts, frame[offset:])
    return None


def _parse_ipv4(ts: float, packet: bytes) -> Frame | None:
    if len(packet) < 20 or packet[0] >> 4 != 4:
        return None
    ihl = (packet[0] & 0x0F) * 4
    if ihl < 20 or len(packet) < ihl:
        return None
    total_len = struct.unpack(">H", packet[2:4])[0]
    protocol = packet[9]
    src = ".".join(str(b) for b in packet[12:16])
    dst = ".".join(str(b) for b in packet[16:20])
    # 按总长度截掉以太网填充（尾部 0x00 常被误当载荷）
    payload_end = min(len(packet), total_len) if total_len >= ihl else len(packet)
    l4 = packet[ihl:payload_end]
    return _parse_l4(ts, src, dst, protocol, l4)


def _parse_ipv6(ts: float, packet: bytes) -> Frame | None:
    if len(packet) < 40 or packet[0] >> 4 != 6:
        return None
    src = _format_ipv6(packet[8:24])
    dst = _format_ipv6(packet[24:40])
    next_header = packet[6]
    payload_len = struct.unpack(">H", packet[4:6])[0]
    offset = 40
    end = min(len(packet), 40 + payload_len) if payload_len else len(packet)
    # 跳过扩展头（最多 4 个）
    for _ in range(4):
        if next_header not in (0, 43, 60) or offset + 8 > end:
            break
        next_header = packet[offset]
        hdr_len = (packet[offset + 1] + 1) * 8
        offset += hdr_len
    if offset >= end:
        return None
    return _parse_l4(ts, src, dst, next_header, packet[offset:end])


def _parse_l4(ts: float, src: str, dst: str, protocol: int, l4: bytes) -> Frame | None:
    proto = PROTO_NAMES.get(protocol, f"proto-{protocol}")
    if protocol == 6:
        if len(l4) < 20:
            return None
        sport, dport = struct.unpack(">HH", l4[:4])
        doff = (l4[12] >> 4) * 4
        if doff < 20 or len(l4) < doff:
            return None
        seq, ack = struct.unpack(">II", l4[4:12])
        return Frame(
            ts=ts,
            src_ip=src,
            dst_ip=dst,
            src_port=sport,
            dst_port=dport,
            proto="tcp",
            payload=l4[doff:],
            seq=seq,
            ack=ack,
            flags=l4[13],
        )
    if protocol == 17:
        if len(l4) < 8:
            return None
        sport, dport = struct.unpack(">HH", l4[:4])
        udp_len = struct.unpack(">H", l4[4:6])[0]
        body = l4[8:udp_len] if 8 <= udp_len <= len(l4) else l4[8:]
        return Frame(
            ts=ts, src_ip=src, dst_ip=dst, src_port=sport, dst_port=dport, proto="udp", payload=body
        )
    return None


def _format_ipv6(raw: bytes) -> str:
    groups = [struct.unpack(">H", raw[i : i + 2])[0] for i in range(0, 16, 2)]
    return ":".join(f"{g:x}" for g in groups)
