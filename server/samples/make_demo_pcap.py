"""生成演示用 pcap：`python -m samples.make_demo_pcap`（也可直接 ``python samples/make_demo_pcap.py``）。

三条会话覆盖三级分级：
- 51001 → JSON 下单，含姓名/手机号/身份证 → L3
- 51002 → 注册表单，邮箱 + 地址共现 → L2
- 51003 → 明文工单，手机号 → L2
外加一条只有握手的会话，用来验证「跳过无载荷帧」。
"""

from __future__ import annotations

import struct
from pathlib import Path

CLIENT = "10.0.0.5"
SERVER = "203.0.113.10"


def _ipv4(src: str, dst: str, proto: int, payload: bytes) -> bytes:
    return (
        struct.pack(
            ">BBHHHBBH4s4s",
            0x45,
            0,
            20 + len(payload),
            1,
            0x4000,
            64,
            proto,
            0,
            bytes(int(p) for p in src.split(".")),
            bytes(int(p) for p in dst.split(".")),
        )
        + payload
    )


def _tcp(sport: int, dport: int, payload: bytes, seq: int = 1000, flags: int = 0x18) -> bytes:
    return struct.pack(">HHIIBBHHH", sport, dport, seq, 0, 5 << 4, flags, 65535, 0, 0) + payload


def _ether(payload: bytes) -> bytes:
    return b"\x02\x00\x00\x00\x00\x01" + b"\x02\x00\x00\x00\x00\x02" + struct.pack(">H", 0x0800) + payload


def frame(sport: int, payload: bytes, *, seq: int = 1000, flags: int = 0x18) -> bytes:
    return _ether(_ipv4(CLIENT, SERVER, 6, _tcp(sport, 80, payload, seq=seq, flags=flags)))


def http_request(method: str, path: str, host: str, content_type: str, body: str) -> bytes:
    raw = body.encode("utf-8")
    head = (
        f"{method} {path} HTTP/1.1\r\nHost: {host}\r\nContent-Type: {content_type}\r\n"
        f"Content-Length: {len(raw)}\r\n\r\n"
    )
    return head.encode("utf-8") + raw


def write_pcap(path: Path, packets: list[tuple[float, bytes]]) -> None:
    chunks = [struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)]
    for ts, raw in packets:
        sec = int(ts)
        usec = int(round((ts - sec) * 1_000_000))
        chunks.append(struct.pack("<IIII", sec, usec, len(raw), len(raw)))
        chunks.append(raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(chunks))


def main() -> None:
    packets = [
        # 1) JSON 下单：姓名 + 手机号 + 身份证（L3）
        (1.000, frame(51001, b"", seq=1, flags=0x02)),
        (
            1.050,
            frame(
                51001,
                http_request(
                    "POST",
                    "/api/order",
                    "shop.example.com",
                    "application/json",
                    '{"name":"张三","phone":"13800138000","id_card":"11010519491231002X"}',
                ),
            ),
        ),
        # 2) 注册表单：邮箱 + 地址（L2）
        (2.000, frame(51002, b"", seq=1, flags=0x02)),
        (
            2.050,
            frame(
                51002,
                http_request(
                    "POST",
                    "/api/register",
                    "user.example.com",
                    "application/x-www-form-urlencoded",
                    "username=zhangsan&email=zhang.san@example.com&address=福建省福州市鼓楼区五四路100号",
                ),
            ),
        ),
        # 3) 明文工单：手机号（L2）
        (3.000, frame(51003, b"", seq=1, flags=0x02)),
        (3.050, frame(51003, "客户张伟，电话 13700137000，需要改签。".encode("utf-8"))),
        # 4) 只有握手：用于验证无载荷帧被跳过
        (4.000, frame(51004, b"", seq=1, flags=0x02)),
    ]
    out = Path(__file__).with_name("demo-traffic.pcap")
    write_pcap(out, packets)
    print(f"已生成 {out}（{out.stat().st_size} 字节）")


if __name__ == "__main__":
    main()
