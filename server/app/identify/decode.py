"""编码载荷的隐式敏感数据识别（base64 / URL-safe base64 / hex / 百分号编码，支持两层嵌套）。

为什么需要这一层：DLP 的常见绕过手段不是改写内容，而是**把内容编码后再传输**——
``{"phone":"13800138000"}`` 做一次 base64 就变成 ``eyJwaG9uZSI6IjEzODAwMTM4MDAwIn0=``，
正则看不到手机号，但数据本体已经出去了。更隐蔽的做法是二次编码（base64 套百分号、base64 套 base64）。

覆盖范围与判定：
- base64（标准 + URL-safe 字母表，可缺省 padding）；
- 十六进制串（``31333830…`` 连写或 ``\\x31\\x33…`` 转义写法）；
- URL 百分号编码（≥2 个 ``%XX``）；
- **最多两层嵌套**：解码结果若整体仍是一个编码片段，继续解一层；
- 解码后必须「像文本」（可打印占比 ≥ 90%、含字母或数字）才复核，
  随机二进制/ID 串/短 hex 不会被当成编码内容；
- 命中区间始终落在**最外层**编码片段上，脱敏替换整段，不在正文里凭空插入字符；
- 置信度按层数折价（每层 ×0.95）：编码套得越深，证据越间接。
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from urllib.parse import unquote

from .types import EntityType, Hit

# 每多一层编码，置信度再乘一次（间接证据）
DECODE_CONFIDENCE_PENALTY = 0.95
# 最多支持两层嵌套（base64(percent(text))、base64(base64(text))）
MAX_DECODE_DEPTH = 2
# 太短的片段解码后信息量不足，且容易撞上正常单词
MIN_ENCODED_LEN = 12
MIN_HEX_LEN = 20  # 11 位手机号 → 22 个 hex 字符
MIN_DECODED_LEN = 6
# 与 scoring.DIRECT_CONFIDENCE 同口径：底层证据足够硬就可以直达高危
DIRECT_EVIDENCE_FLOOR = 0.90

_HEX = "0-9A-Fa-f"

_BASE64_RE = re.compile(
    r"(?<![A-Za-z0-9+/_\-])[A-Za-z0-9+/_\-]{" + str(MIN_ENCODED_LEN) + r",}={0,2}(?![A-Za-z0-9+/=_\-])"
)
_HEX_RE = re.compile(r"(?<![0-9A-Za-z])[" + _HEX + r"]{" + str(MIN_HEX_LEN) + r",}(?![0-9A-Za-z])")
_HEX_ESC_RE = re.compile(r"(?:\\x[" + _HEX + r"]{2}){4,}")
_PERCENT_CHUNK_RE = re.compile(
    r"(?:%[0-9A-Fa-f]{2}|[A-Za-z0-9._~:/?#@!$&'()*+,;=\-]){" + str(MIN_ENCODED_LEN) + r",}"
)


@dataclass(frozen=True, slots=True)
class DecodedSpan:
    """原文里一段可解码的载荷。"""

    start: int
    end: int
    text: str  # 最终解码后的文本（可能经过多层）
    encoding: str  # 编码链，如 "base64" / "base64>percent"

    @property
    def layers(self) -> int:
        return len([part for part in self.encoding.split(">") if part])


def _looks_textual(decoded: str) -> bool:
    """解码结果是否像正常文本：可打印占比高，且至少有字母/数字/汉字。"""
    if len(decoded) < MIN_DECODED_LEN:
        return False
    printable = sum(1 for ch in decoded if ch.isprintable() or ch in "\r\n\t")
    if printable / len(decoded) < 0.9:
        return False
    return any(ch.isalnum() or "\u4e00" <= ch <= "\u9fff" for ch in decoded)


def _decode_bytes(raw: bytes) -> str | None:
    for encoding in ("utf-8", "gbk"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if _looks_textual(text):
            return text
    return None


def _try_base64(value: str) -> str | None:
    candidate = value.replace("-", "+").replace("_", "/")
    padded = candidate + "=" * (-len(candidate) % 4)
    try:
        raw = base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError):
        return None
    return _decode_bytes(raw)


def _try_hex(value: str) -> str | None:
    if len(value) % 2 != 0:
        return None
    try:
        raw = bytes.fromhex(value)
    except ValueError:
        return None
    return _decode_bytes(raw)


def _try_hex_escapes(value: str) -> str | None:
    raw = bytes(int(pair, 16) for pair in re.findall(r"\\x([" + _HEX + r"]{2})", value))
    return _decode_bytes(raw) if raw else None


def _try_percent(value: str) -> str | None:
    # 至少两个 %XX 才当作百分号编码，否则正常单词里的 % 会被误判
    if len(re.findall(r"%[0-9A-Fa-f]{2}", value)) < 2:
        return None
    try:
        decoded = unquote(value, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    return decoded if decoded != value and _looks_textual(decoded) else None


def _decode_one(value: str) -> tuple[str, str] | None:
    """尝试对**整段**值解一层编码，返回 (解码文本, 编码类型)。"""
    attempts = (
        ("hex", _try_hex_escapes),
        ("hex", _try_hex),
        ("percent", _try_percent),
        ("base64", _try_base64),
    )
    for kind, func in attempts:
        decoded = func(value)
        if decoded is not None and decoded != value:
            return decoded, kind
    return None


def decode_layers(value: str, *, max_depth: int = MAX_DECODE_DEPTH) -> tuple[str, str] | None:
    """对一段值做最多 ``max_depth`` 层解码，返回 (最终文本, 编码链)。"""
    current = value
    chain: list[str] = []
    for _ in range(max_depth):
        step = _decode_one(current)
        if step is None:
            break
        current, kind = step
        chain.append(kind)
    if not chain:
        return None
    if not _looks_textual(current):
        return None
    return current, ">".join(chain)


def _candidate_spans(text: str) -> list[tuple[int, int]]:
    """按优先级收集候选片段：hex 转义 > 纯 hex > 百分号 > base64（避免 hex 被 base64 抢走）。"""
    found: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < e and s < end for s, e in found)

    # 每个正则只接受「首层解码类型与之相符」的片段：
    # 否则一串 base64 候选可能被 hex 先解开，把编码链标错、也放过短 hex。
    for pattern, expected in (
        (_HEX_ESC_RE, "hex"),
        (_HEX_RE, "hex"),
        (_PERCENT_CHUNK_RE, "percent"),
        (_BASE64_RE, "base64"),
    ):
        for match in pattern.finditer(text):
            if overlaps(match.start(), match.end()):
                continue
            decoded = decode_layers(match.group(0))
            if decoded is None or decoded[1].split(">")[0] != expected:
                continue
            found.append((match.start(), match.end()))
    found.sort()
    return found


def iter_decoded_spans(text: str) -> list[DecodedSpan]:
    """找出文本里所有可解码且解码结果像文本的片段（按出现顺序、互不重叠）。"""
    spans: list[DecodedSpan] = []
    for start, end in _candidate_spans(text):
        decoded = decode_layers(text[start:end])
        if decoded is None:
            continue
        final, chain = decoded
        spans.append(DecodedSpan(start, end, final, chain))
    return spans


def decoded_hits(text: str, detect) -> list[Hit]:
    """解码片段后复核：解码文本里命中什么，就在编码片段上记一条同类命中。

    ``detect`` 由调用方注入（``app.identify.rules.detect``），避免模块循环依赖。
    """
    hits: list[Hit] = []
    for span in iter_decoded_spans(text):
        inner = detect(span.text)
        if not inner:
            continue
        strongest = max(inner, key=lambda hit: hit.weight * hit.confidence)
        confidence = strongest.confidence * (DECODE_CONFIDENCE_PENALTY**span.layers)
        hits.append(
            Hit(
                type=strongest.type,
                start=span.start,
                end=span.end,
                value=text[span.start : span.end],
                weight=strongest.weight,
                confidence=round(confidence, 3),
                rule=f"decoded-{span.encoding}:{strongest.rule}",
                # 编码层数只削弱证据强度；底层命中足以判高危时仍应直达 L3
                direct_hint=strongest.confidence >= DIRECT_EVIDENCE_FLOOR,
            )
        )
    return hits


def decode_enabled_by_default() -> bool:
    """编码载荷识别默认开启（可用 FLOWMASK_DISABLE_DECODE=1 关掉）。"""
    import os

    return os.environ.get("FLOWMASK_DISABLE_DECODE", "").strip().lower() not in {"1", "true", "yes"}


__all__ = [
    "DECODE_CONFIDENCE_PENALTY",
    "MAX_DECODE_DEPTH",
    "DecodedSpan",
    "decode_enabled_by_default",
    "decode_layers",
    "decoded_hits",
    "iter_decoded_spans",
    "EntityType",
]
