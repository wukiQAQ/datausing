"""流量导入链路测试：pcap 解析 → HTTP 正文 → 任务/落库。

这些用例全部用「代码构造帧」而不是依赖二进制样例，好处是意图可读：
哪一帧带身份证、哪一帧只有 SYN，测试里一眼能看出来。
"""

from __future__ import annotations

import struct
import time

from app.api.schemas import TrafficFormatsOut, TrafficImportOut
from app.traffic import (
    build_document,
    collect_sessions,
    detect_format,
    iter_packets,
    parse_ethernet,
    split_messages,
    write_pcap,
)

CLIENT = "10.0.0.5"
SERVER = "203.0.113.10"
ID_CARD = "11010519491231002X"
PHONE = "13800138000"


def _ipv4(src: str, dst: str, proto: int, payload: bytes) -> bytes:
    header = struct.pack(
        ">BBHHHBBH4s4s",
        0x45,
        0,
        20 + len(payload),
        1,
        0x4000,
        64,
        proto,
        0,
        bytes(int(part) for part in src.split(".")),
        bytes(int(part) for part in dst.split(".")),
    )
    return header + payload


def _tcp(src_port: int, dst_port: int, payload: bytes, seq: int = 1000, flags: int = 0x18) -> bytes:
    header = struct.pack(">HHIIBBHHH", src_port, dst_port, seq, 0, 5 << 4, flags, 65535, 0, 0)
    return header + payload


def _ethernet(payload: bytes, vlan: int | None = None) -> bytes:
    mac = b"\x02\x00\x00\x00\x00\x01" + b"\x02\x00\x00\x00\x00\x02"
    if vlan is None:
        return mac + struct.pack(">H", 0x0800) + payload
    return mac + struct.pack(">HHH", 0x8100, vlan, 0x0800) + payload


def tcp_frame(src_port: int, payload: bytes, *, seq: int = 1000, vlan: int | None = None) -> bytes:
    return _ethernet(_ipv4(CLIENT, SERVER, 6, _tcp(src_port, 80, payload, seq=seq)), vlan=vlan)


def syn_frame(src_port: int, seq: int = 1) -> bytes:
    return _ethernet(_ipv4(CLIENT, SERVER, 6, _tcp(src_port, 80, b"", seq=seq, flags=0x02)))


def http_request(method: str, path: str, *, host: str, content_type: str, body: str) -> bytes:
    raw = body.encode("utf-8")
    head = (
        f"{method} {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(raw)}\r\n\r\n"
    )
    return head.encode("utf-8") + raw


JSON_BODY = f'{{"name":"张三","phone":"{PHONE}","id_card":"{ID_CARD}"}}'
FORM_BODY = "username=zhangsan&email=zhang.san@example.com&address=福建省福州市鼓楼区五四路100号"


def demo_pcap() -> bytes:
    """3 条会话：JSON(含身份证)、表单(邮箱+地址)、纯文本。"""
    packets = [
        (1.0, syn_frame(51001)),
        (1.05, tcp_frame(51001, http_request("POST", "/api/order", host="shop.example.com", content_type="application/json", body=JSON_BODY))),
        (2.0, syn_frame(51002)),
        (2.05, tcp_frame(51002, http_request("POST", "/register", host="user.example.com", content_type="application/x-www-form-urlencoded", body=FORM_BODY))),
        (3.0, syn_frame(51003)),
        (3.05, tcp_frame(51003, "客户张伟，电话 13700137000，需要改签。".encode("utf-8"))),
    ]
    return write_pcap(packets)


def _wait_done(client, task_id: str, timeout: float = 8.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/v1/tasks/{task_id}").json()
        if body["status"] in {"done", "failed"}:
            return body
        time.sleep(0.05)
    raise AssertionError("任务超时未完成")


# ---------- 解析层 ----------


def test_pcap_roundtrip_and_format_detection() -> None:
    data = demo_pcap()
    assert detect_format(data) == "pcap"
    packets = list(iter_packets(data))
    assert len(packets) == 6
    assert packets[0].linktype == 1
    # 时间戳按微秒精度回读
    assert abs(packets[1].ts - 1.05) < 1e-6


def test_parse_ethernet_with_vlan_and_ipv6_skip() -> None:
    frame = parse_ethernet(1.0, tcp_frame(51001, b"hello", vlan=100))
    assert frame is not None
    assert (frame.src_ip, frame.dst_ip, frame.proto) == (CLIENT, SERVER, "tcp")
    assert frame.payload == b"hello"
    # ARP 之类非 IP 帧应被跳过
    arp = b"\x02" * 6 + b"\x04" * 6 + struct.pack(">H", 0x0806) + b"\x00" * 28
    assert parse_ethernet(1.0, arp) is None


def test_collect_sessions_and_http_split() -> None:
    packets = list(iter_packets(demo_pcap()))
    frames = [parse_ethernet(p.ts, p.frame) for p in packets]
    frames = [f for f in frames if f is not None]
    sessions = collect_sessions(frames)
    assert len(sessions) == 3
    json_session = next(s for s in sessions if s.src_port == 51001)
    message = split_messages(json_session)[0]
    assert (message.method, message.path, message.host) == ("POST", "/api/order", "shop.example.com")
    assert message.content_type == "json"
    assert ID_CARD in message.body
    assert "POST /api/order" in message.label and "51001" in message.label


def test_tcp_reassembly_joins_segments() -> None:
    body = JSON_BODY.encode("utf-8")
    # 首部里必须写真实 Content-Length，否则解析出来是空正文
    head = (
        f"POST /api/order HTTP/1.1\r\nHost: h\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    ).encode("utf-8")
    # 首部+前 20 字节正文在第一段，其余在第二段，第三条是重传
    cut = len(head) + 20
    full = head + body
    packets = [
        (1.0, tcp_frame(51001, full[:cut], seq=1000)),
        (1.05, tcp_frame(51001, full[cut:], seq=1000 + cut)),
        (1.06, tcp_frame(51001, full[cut:cut + 5], seq=1000 + cut)),  # 重传，应被丢弃
    ]
    frames = [parse_ethernet(ts, frame) for ts, frame in packets]
    session = collect_sessions([f for f in frames if f is not None])[0]
    assert session.payload == full
    assert ID_CARD in split_messages(session)[0].body


def test_build_document_counts_and_segments() -> None:
    document, summary = build_document(demo_pcap(), source_name="demo.pcap")
    assert summary.packets == 6 and summary.sessions == 3 and summary.segments == 3
    assert summary.messages == 3
    assert document.content_type == "pcap"
    assert document.source == "pcap:demo.pcap"
    assert document.meta["sessions"] == 3
    labels = [segment.path for segment in document.segments]
    assert all("tcp" in label and "→" in label for label in labels)
    # 每条正文都保留原文（脱敏发生在识别之后，库里只存副本）
    assert any(ID_CARD in segment.text for segment in document.segments)


def test_build_document_rejects_empty_or_handshake_only() -> None:
    import pytest

    from app.traffic import TrafficParseError

    with pytest.raises(TrafficParseError, match="没有任何报文"):
        build_document(write_pcap([]), source_name="empty.pcap")
    handshake_only = write_pcap([(1.0, syn_frame(51001))])
    with pytest.raises(TrafficParseError, match="没有可解析的载荷"):
        build_document(handshake_only, source_name="syn.pcap")


# ---------- 接口层 ----------


def test_traffic_formats_endpoint(client) -> None:
    body = client.get("/api/v1/traffic/formats").json()
    model = TrafficFormatsOut(**body)
    assert model.formats == ["pcap", "pcapng"]
    assert model.max_upload_mb > 0


def test_traffic_import_creates_task_with_findings(client) -> None:
    response = client.post(
        "/api/v1/traffic/import",
        files={"pcap": ("demo.pcap", demo_pcap(), "application/vnd.tcpdump.pcap")},
        headers={"X-Operator-Id": "alice"},
    )
    assert response.status_code == 201, response.text
    body = TrafficImportOut(**response.json())
    assert body.packets == 6 and body.sessions == 3 and body.segments == 3
    assert body.operator_id == "alice"

    task = _wait_done(client, body.task_id)
    assert task["status"] == "done"
    assert task["content_type"] == "pcap"
    assert task["total_findings"] >= 2

    findings = client.get(f"/api/v1/tasks/{body.task_id}/findings").json()["items"]
    levels = {item["level"] for item in findings}
    assert "L3" in levels  # JSON 里的身份证/手机号直达高危
    joined = " ".join(item["masked"] for item in findings)
    assert ID_CARD not in joined and PHONE not in joined
    assert "已屏蔽" in joined
    # 五元组信息保留在 path 里，便于前端展示"哪条流"
    assert any("51001" in item["path"] for item in findings)


def test_traffic_import_rejects_non_pcap(client) -> None:
    response = client.post(
        "/api/v1/traffic/import",
        files={"pcap": ("note.txt", b"this is not a pcap", "text/plain")},
    )
    assert response.status_code == 415
    assert "pcap" in response.json()["detail"]


def test_traffic_import_rejects_empty_and_handshake_only(client) -> None:
    empty = client.post("/api/v1/traffic/import", files={"pcap": ("e.pcap", b"", "application/octet-stream")})
    assert empty.status_code == 422
    syn = client.post(
        "/api/v1/traffic/import",
        files={"pcap": ("s.pcap", write_pcap([(1.0, syn_frame(51001))]), "application/octet-stream")},
    )
    assert syn.status_code == 422
    assert "载荷" in syn.json()["detail"]


def test_traffic_sample_pcap_is_importable(client) -> None:
    """自带示例抓包必须能被导入，避免示例文件与解析实现漂移。"""
    sample = client.get("/api/v1/traffic/sample")
    assert sample.status_code == 200
    assert sample.content[:4] == b"\xd4\xc3\xb2\xa1"
    assert "demo-traffic.pcap" in sample.headers["content-disposition"]

    imported = client.post(
        "/api/v1/traffic/import",
        files={"pcap": ("demo-traffic.pcap", sample.content, "application/vnd.tcpdump.pcap")},
    )
    assert imported.status_code == 201, imported.text
    body = imported.json()
    assert body["sessions"] == 3
    assert body["segments"] == 3
