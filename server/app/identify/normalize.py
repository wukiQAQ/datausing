"""文本归一化：在识别前把"人眼看的号码"变成"规则能认的号码"。

真实数据里同一个手机号有无数种写法：``138-0013-8000``、``138 0013 8000``、
``１３８００１３８０００``（全角）、``138 0013 8000``（含不可见空格）。规则只认连续数字，
不归一化就会整片漏检。

设计要点：

* **只做"无损归一化"**：全角转半角、字母数字区间内的分隔符（空格/连字符）去掉；
  不删任何其它字符，保证 ``index_map`` 可以把归一化后的偏移精确映射回原文，
  脱敏仍然打在原文的正确区间上（包含被去掉的分隔符）。
* **脱敏对象仍是原文**：`mask()` 用的是映射回原文的区间，因此导出/预览里看到的是
  原文被整体替换，不会出现"原文数字还在、只是少了分隔符"的半吊子结果。
"""

from __future__ import annotations

_FULLWIDTH_OFFSET = 0xFEE0
# 只在这些分隔符出现在两个数字之间时丢弃（不碰 "zhang-san" 这类词内连字符）
_DIGIT_SEPARATORS = frozenset({" ", "-", "\t", "\u00a0", "\u3000", "\u200b"})


def _to_halfwidth(char: str) -> str:
    """全角 ASCII（！～）转半角；其它字符原样返回。"""
    code = ord(char)
    if 0xFF01 <= code <= 0xFF5E:
        return chr(code - _FULLWIDTH_OFFSET)
    if code == 0x3000:  # 全角空格
        return " "
    return char


def _is_ascii_digit(char: str) -> bool:
    return "0" <= char <= "9"


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """返回（归一化文本，原文偏移映射表）。

    ``index_map[i]`` 是归一化后第 i 个字符在原文中的下标；映射表长度与归一化文本一致。
    """
    # 第一步：全角转半角（长度不变，可同时建映射）
    chars: list[str] = []
    index_map: list[int] = []
    for index, raw in enumerate(text):
        half = _to_halfwidth(raw)
        chars.append(half)
        index_map.append(index)

    # 第二步：删掉"数字之间"的分隔符（长度会变，映射同步收缩）
    kept_chars: list[str] = []
    kept_map: list[int] = []
    total = len(chars)
    for position, char in enumerate(chars):
        if char in _DIGIT_SEPARATORS:
            previous = chars[position - 1] if position > 0 else ""
            following = chars[position + 1] if position + 1 < total else ""
            if _is_ascii_digit(previous) and _is_ascii_digit(following):
                continue
        kept_chars.append(char)
        kept_map.append(index_map[position])
    return "".join(kept_chars), kept_map


def normalize(text: str) -> str:
    """归一化入口（不需要偏移映射时用，便于测试与外部复用）。"""
    return normalize_with_map(text)[0]


def map_hit_span(index_map: list[int], start: int, end: int, text_length: int) -> tuple[int, int] | None:
    """把归一化文本上的 [start, end) 映射回原文区间；越界返回 None。"""
    if start < 0 or end <= start or start >= len(index_map):
        return None
    original_start = index_map[start]
    last = min(end, len(index_map)) - 1
    original_end = min(index_map[last] + 1, text_length)
    if original_end <= original_start:
        return None
    return original_start, original_end
