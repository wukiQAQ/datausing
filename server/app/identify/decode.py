"""编码载荷的隐式敏感数据识别（base64 / URL 百分号编码）。

为什么需要这一层：DLP 的常见绕过手段不是改写内容，而是**把内容编码后再传输**——
``{"phone":"13800138000"}`` 做一次 base64 就变成 ``eyJwaG9uZSI6IjEzODAwMTM4MDAwIn0=``，
正则看不到手机号，但数据本体已经出去了。评测集里唯一一条分级错误就是这类样本。

设计取舍：
- 只做**解码后复核**，不把解码结果混进正文——命中区间仍落在编码片段上，
  这样脱敏时替换的是整段编码，而不是在原文里凭空插入字符；
- 置信度乘 0.95 的折价系数：编码载荷是间接证据，比明文命中断一档；
- 解码后必须「像文本」（可打印占比 ≥ 90%、含字母或数字），
  否则随机二进制/ID 串会被误当编码内容，制造大量误报。
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from urllib.parse import unquote

from .types import EntityType, Hit

# 编码载荷是间接证据，命中置信度打个折（明文命中不打折）
DECODE_CONFIDENCE_PENALTY = 0.95
# 太短的片段解码后信息量不足，且容易撞上正常单词
MIN_ENCODED_LEN = 12
MIN_DECODED_LEN = 6

_BASE64_RE = re.compile(
    r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{" + str(MIN_ENCODED_LEN) + r",}={0,2}(?![A-Za-z0-9+/=])"
)
_PERCENT_CHUNK_RE = re.compile(
    r"(?:%[0-9A-Fa-f]{2}|[A-Za-z0-9._~:/?#@!$&'()*+,;=\-]){" + str(MIN_ENCODED_LEN) + r",}"
)


@dataclass(frozen=True, slots=True)
class DecodedSpan:
    """原文里一段可解码的载荷。"""

    start: int
    end: int
    text: str  # 解码后的文本
    encoding: str  # "base64" / "percent"

    def original(self, source: str) -> str:
        return source[self.start : self.end]


def _looks_textual(decoded: str) -> bool:
    """解码结果是否像正常文本：可打印占比高，且至少有字母/数字/汉字。"""
    if len(decoded) < MIN_DECODED_LEN:
        return False
    printable = sum(1 for ch in decoded if ch.isprintable() or ch in "\r\n\t")
    if printable / len(decoded) < 0.9:
        return False
    return any(ch.isalnum() or "\u4e00" <= ch <= "\u9fff" for ch in decoded)


def _try_base64(value: str) -> str | None:
    padded = value + "=" * (-len(value) % 4)
    try:
        raw = base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError):
        return None
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        decoded = raw.decode("gbk", errors="strict") if _gbk_safe(raw) else ""
    if not decoded:
        return None
    return decoded if _looks_textual(decoded) else None


def _gbk_safe(raw: bytes) -> bool:
    try:
        raw.decode("gbk", errors="strict")
    except UnicodeDecodeError:
        return False
    return True


def _try_percent(value: str) -> str | None:
    # 至少两个 %XX 才当作百分号编码，否则正常单词里的 % 会被误判
    if len(re.findall(r"%[0-9A-Fa-f]{2}", value)) < 2:
        return None
    try:
        decoded = unquote(value, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    return decoded if decoded != value and _looks_textual(decoded) else None


def iter_decoded_spans(text: str) -> list[DecodedSpan]:
    """找出文本里所有可解码且解码结果像文本的片段（按出现顺序、互不重叠）。"""
    spans: list[DecodedSpan] = []
    taken: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < e and s < end for s, e in taken)

    for match in _BASE64_RE.finditer(text):
        value = match.group(0)
        decoded = _try_base64(value)
        if decoded is None or overlaps(match.start(), match.end()):
            continue
        spans.append(DecodedSpan(match.start(), match.end(), decoded, "base64"))
        taken.append((match.start(), match.end()))

    for match in _PERCENT_CHUNK_RE.finditer(text):
        value = match.group(0)
        decoded = _try_percent(value)
        if decoded is None or overlaps(match.start(), match.end()):
            continue
        spans.append(DecodedSpan(match.start(), match.end(), decoded, "percent"))
        taken.append((match.start(), match.end()))

    spans.sort(key=lambda span: span.start)
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
        hits.append(
            Hit(
                type=strongest.type,
                start=span.start,
                end=span.end,
                value=text[span.start : span.end],
                weight=strongest.weight,
                confidence=round(strongest.confidence * DECODE_CONFIDENCE_PENALTY, 3),
                rule=f"decoded-{span.encoding}:{strongest.rule}",
            )
        )
    return hits


def decode_enabled_by_default() -> bool:
    """编码载荷识别默认开启（可用 FLOWMASK_DISABLE_DECODE=1 关掉）。"""
    import os

    return os.environ.get("FLOWMASK_DISABLE_DECODE", "").strip().lower() not in {"1", "true", "yes"}


__all__ = [
    "DECODE_CONFIDENCE_PENALTY",
    "DecodedSpan",
    "decode_enabled_by_default",
    "decoded_hits",
    "iter_decoded_spans",
    "EntityType",
]
