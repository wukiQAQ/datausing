"""模拟「持续增长的抓包文件」，用于演示服务端实时旁路。

用法（在 `server/` 目录下，另开一个终端）：

    python samples/simulate_live_traffic.py                 # 每 1.5s 追加一条会话，共 6 轮
    python samples/simulate_live_traffic.py --interval 0.8 --rounds 20
    python samples/simulate_live_traffic.py --out data/live.pcap --burst 3
    python samples/simulate_live_traffic.py --format pcapng --out data/live.pcapng

默认写经典 pcap；加 `--format pcapng` 写 dumpcap/Wireshark 同款 pcapng（实时跟随两者都支持）。

会先写 pcap 全局头（并清掉旧文件），之后按 `--interval` 秒往同一个文件追加
「SYN + 一条 HTTP 请求」两帧——服务端 `PcapFollower` 靠文件增长触发增量解析。

为什么用「写文件」而不是直接发原始以太网帧：服务端保持纯标准库，
真正抓包交给 dumpcap / Wireshark / 自研采集器；本脚本只负责产生可复现的流量。
"""

from __future__ import annotations

import argparse
import struct
import time
from pathlib import Path

from make_demo_pcap import frame, http_request

SCENARIOS = [
    (
        51001,
        http_request(
            "POST",
            "/api/order",
            "shop.example.com",
            "application/json",
            '{"name":"张三","phone":"13800138000","id_card":"11010519491231002X"}',
        ),
    ),
    (
        51002,
        http_request(
            "POST",
            "/api/register",
            "user.example.com",
            "application/x-www-form-urlencoded",
            "username=zhangsan&email=zhang.san@example.com&address=福建省福州市鼓楼区五四路100号",
        ),
    ),
    (
        51003,
        http_request(
            "POST",
            "/api/ticket/remark",
            "ops.example.com",
            "text/plain",
            "客户张伟，电话 13700137000，需要改签。",
        ),
    ),
    (
        51004,
        http_request(
            "GET",
            "/api/health",
            "ops.example.com",
            "text/plain",
            "ok",
        ),
    ),
]

PCAP_HEADER = struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)


def record(ts: float, raw: bytes) -> bytes:
    sec = int(ts)
    usec = int(round((ts - sec) * 1_000_000))
    return struct.pack("<IIII", sec, usec, len(raw), len(raw)) + raw


def _pcapng_shb() -> bytes:
    body = struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1)
    total = 12 + len(body)
    return struct.pack("<II", 0x0A0D0D0A, total) + body + struct.pack("<I", total)


def _pcapng_idb(linktype: int = 1, snaplen: int = 65535) -> bytes:
    body = struct.pack("<HHI", linktype, 0, snaplen)
    total = 12 + len(body)
    return struct.pack("<II", 1, total) + body + struct.pack("<I", total)


def pcapng_record(ts: float, raw: bytes) -> bytes:
    ts_us = int(round(ts * 1_000_000))
    body = struct.pack("<IIIII", 0, ts_us >> 32, ts_us & 0xFFFFFFFF, len(raw), len(raw)) + raw
    body += b"\x00" * ((-len(raw)) % 4)
    total = 12 + len(body)
    return struct.pack("<II", 6, total) + body + struct.pack("<I", total)


def main() -> None:
    parser = argparse.ArgumentParser(description="持续往 pcap 文件追加演示流量")
    parser.add_argument("--out", default="data/live.pcap", help="目标 pcap 路径（相对 server/）")
    parser.add_argument("--interval", type=float, default=1.5, help="每轮间隔秒数")
    parser.add_argument("--rounds", type=int, default=6, help="追加多少轮会话")
    parser.add_argument("--burst", type=int, default=1, help="每轮写几个会话（可用 --burst 4 一次写满）")
    parser.add_argument("--keep", action="store_true", help="保留旧文件内容（默认先清空重写头）")
    parser.add_argument(
        "--format",
        choices=("pcap", "pcapng"),
        default=None,
        help="输出容器格式；缺省按 --out 后缀判断（.pcapng 则写 pcapng），否则 pcap",
    )
    args = parser.parse_args()

    fmt = args.format or ("pcapng" if args.out.lower().endswith(".pcapng") else "pcap")
    write_record = pcapng_record if fmt == "pcapng" else record

    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not args.keep:
        path.write_bytes((_pcapng_shb() + _pcapng_idb()) if fmt == "pcapng" else PCAP_HEADER)
    print(f"目标文件 {path.resolve()}（{fmt}，每 {args.interval}s 追加 {args.burst} 个会话，共 {args.rounds} 轮）")

    seq = 0
    for _ in range(args.rounds):
        with path.open("ab") as handle:
            for _ in range(max(1, args.burst)):
                port, body = SCENARIOS[seq % len(SCENARIOS)]
                seq += 1
                now = time.time()
                handle.write(write_record(now, frame(port, b"", seq=1, flags=0x02)))
                handle.write(write_record(now + 0.001, frame(port, body)))
        time.sleep(args.interval)

    print(f"追加完成：{path.stat().st_size} 字节。页面「流量导入 → 实时旁路」应看到报文/片段/窗口任务在增长。")


if __name__ == "__main__":
    main()
