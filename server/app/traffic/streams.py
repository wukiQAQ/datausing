"""TCP 流重组 + HTTP 报文解析（纯标准库）。

服务端引入 pcap 的目的不是做完整协议栈，而是把「流量里的正文」交给已有识别链路：
按五元组分方向聚合载荷 → 按 TCP 序号拼接 → 解析 HTTP 首部/正文 → 得到待检测文本。
演示与常见明文 HTTP/CSV/JSON 场景够用；HTTPS、分块传输、gzip 压缩体暂不处理。
"""

from __future__ import annotations

from dataclasses import dataclass

from .frames import Frame

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
_MAX_TEXT_BODY = 1 << 20  # 单条正文上限 1MB，防止异常 pcap 把内存吃爆


@dataclass(frozen=True, slots=True)
class Session:
    """一个方向上的五元组会话（src→dst）。"""

    src_ip: str
    src_port: int
    dst_ip: str
    dst_port: int
    proto: str
    first_ts: float
    last_ts: float
    packets: int
    payload: bytes

    @property
    def label(self) -> str:
        return f"{self.proto} {self.src_ip}:{self.src_port}→{self.dst_ip}:{self.dst_port}"


@dataclass(frozen=True, slots=True)
class HttpMessage:
    """会话里解析出的一条报文（请求/响应/裸文本）。"""

    session: Session
    method: str
    path: str
    host: str
    content_type: str
    body: str

    @property
    def label(self) -> str:
        """给 findings 的 ``path`` 字段：五元组 + 请求行 + 类型，便于前端直接看懂。"""
        head = self.path or "<data>"
        if self.method:
            head = f"{self.method} {head}"
        return f"{self.session.label} {head} [{self.content_type}]"


def collect_sessions(frames: list[Frame]) -> list[Session]:
    """按五元组聚合，TCP 按序号拼接、UDP 直接顺序拼接。"""
    groups: dict[tuple[str, int, str, int, str], list[Frame]] = {}
    for frame in frames:
        if not frame.payload:
            continue
        key = (frame.src_ip, frame.src_port, frame.dst_ip, frame.dst_port, frame.proto)
        groups.setdefault(key, []).append(frame)

    sessions: list[Session] = []
    for (src, sport, dst, dport, proto), segs in groups.items():
        segs.sort(key=lambda f: (f.seq, f.ts))
        payload = _reassemble(segs) if proto == "tcp" else b"".join(f.payload for f in segs)
        sessions.append(
            Session(
                src_ip=src,
                src_port=sport,
                dst_ip=dst,
                dst_port=dport,
                proto=proto,
                first_ts=segs[0].ts,
                last_ts=segs[-1].ts,
                packets=len(segs),
                payload=payload[:_MAX_TEXT_BODY],
            )
        )
    sessions.sort(key=lambda s: s.first_ts)
    return sessions


def _reassemble(segs: list[Frame]) -> bytes:
    """按 TCP 序号拼接：丢弃重传、跳过已覆盖区间。"""
    buffer = bytearray()
    next_seq: int | None = None
    for seg in segs:
        data = seg.payload
        if next_seq is not None and seg.seq < next_seq:
            skip = next_seq - seg.seq
            if skip >= len(data):
                continue
            data = data[skip:]
        buffer.extend(data)
        next_seq = (seg.seq + len(seg.payload)) & 0xFFFFFFFF
    return bytes(buffer)


def split_messages(session: Session) -> list[HttpMessage]:
    """把会话载荷拆成 HTTP 报文；不像 HTTP 时退回「整段文本」一条。"""
    parsed = _parse_http(session.payload)
    if parsed:
        return [
            HttpMessage(
                session=session,
                method=method,
                path=path,
                host=host,
                content_type=detect_content_type(header_ct, body),
                body=_decode(body),
            )
            for method, path, host, header_ct, body in parsed
        ]

    text = _decode(session.payload)
    if not _looks_textual(text):
        return []
    return [
        HttpMessage(
            session=session,
            method="",
            path="<data>",
            host="",
            content_type=detect_content_type("", session.payload),
            body=text,
        )
    ]


def _parse_http(payload: bytes) -> list[tuple[str, str, str, str, bytes]]:
    """顺序解析 HTTP 报文；返回 (method, path, host, content-type, body)。"""
    out: list[tuple[str, str, str, str, bytes]] = []
    rest = payload
    while rest:
        head_end = rest.find(b"\r\n\r\n")
        if head_end < 0:
            break
        head = rest[:head_end].decode("latin-1", "replace")
        lines = head.split("\r\n")
        start_line = lines[0].strip()
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()

        body_start = head_end + 4
        content_length = headers.get("content-length", "")
        if content_length.isdigit():
            body = rest[body_start : body_start + int(content_length)]
            consumed = body_start + int(content_length)
        else:
            # 没有 Content-Length：把剩余全部当正文（演示数据常见），解析到此结束
            body = rest[body_start:]
            consumed = len(rest)

        tokens = start_line.split()
        if tokens and tokens[0] in HTTP_METHODS:
            method = tokens[0]
            path = tokens[1] if len(tokens) > 1 else "/"
        elif start_line.startswith("HTTP/"):
            method = ""
            path = "<response>"
        else:
            # 不是标准首行：可能是裸文本，交给上层兜底
            break
        out.append((method, path, headers.get("host", ""), headers.get("content-type", ""), body))
        rest = rest[consumed:]
        if consumed >= len(rest) + consumed:
            break
    return out


def detect_content_type(header_ct: str, body: bytes) -> str:
    """按首部 + 内容嗅探给出 json/form/csv/text 四类之一。"""
    lowered = header_ct.lower()
    stripped = body.lstrip()
    if "json" in lowered or stripped[:1] in (b"{", b"["):
        return "json"
    if "x-www-form-urlencoded" in lowered or (b"=" in body and b"&" in body):
        return "form"
    if "csv" in lowered or (body.count(b",") >= 2 and b"\n" in body):
        return "csv"
    return "text"


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace")


def _looks_textual(text: str) -> bool:
    """粗略判断是否是可读文本（避免把二进制帧送去识别）。"""
    if not text:
        return False
    control = sum(1 for ch in text if ord(ch) < 9 or (13 < ord(ch) < 32))
    return control / len(text) < 0.05
