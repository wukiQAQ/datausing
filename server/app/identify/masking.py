"""四级脱敏执行。

模板与 Go 版一致，也是《需求分析》"四级分级脱敏"的落点：

| 级别 | 处置方式 | 示例 |
| --- | --- | --- |
| L3 高危 | 全屏蔽为类别占位 | ``11010519491231002X`` → ``[已屏蔽:身份证号]`` |
| L2 中危 | 保留首字符 + 星号 | ``13800138000`` → ``1******`` |
| L1 低危 | 前置警示、保留原文 | ``[低危] 13800138000`` |
| SAFE 安全 | 不处理 | 原文 |

只改命中片段、不动上下文，这样"这条数据是在什么业务里泄露的"仍然可读；
类别名用中文占位符，演示与报告里一眼能看懂被屏蔽的是什么。
"""

from __future__ import annotations

from .types import EntityType, Hit, Level, Result, entity_label

MAX_STARS = 6


def _stars(length: int) -> str:
    return "*" * max(1, min(length - 1, MAX_STARS))


def mask_value(value: str, entity: EntityType, level: Level) -> str:
    """按级别对单个敏感值做脱敏。"""
    if not value:
        return value
    if level == Level.HIGH:
        return f"[已屏蔽:{entity_label(entity)}]"
    if level == Level.MID:
        return value[0] + _stars(len(value))
    if level == Level.LOW:
        return f"[低危]{value}"
    return value


def mask(text: str, result: Result, level: Level | None = None) -> str:
    """按命中区间替换脱敏副本；``level`` 可覆盖（用于"强制某种脱敏强度"的演示）。"""
    if not text or not result.hits:
        return text
    effective = level or result.level
    if effective == Level.SAFE:
        return text

    parts: list[str] = []
    cursor = 0
    for hit in sorted(result.hits, key=lambda item: item.start):
        if hit.start < cursor:
            continue
        parts.append(text[cursor:hit.start])
        parts.append(mask_value(hit.value, hit.type, effective))
        cursor = hit.end
    parts.append(text[cursor:])
    return "".join(parts)


def mask_hits(text: str, hits: list[Hit], level: Level) -> str:
    """只给命中列表与级别，直接产出脱敏文本（脱敏算子的通用入口）。"""
    if not text or not hits or level == Level.SAFE:
        return text
    parts: list[str] = []
    cursor = 0
    for hit in sorted(hits, key=lambda item: item.start):
        if hit.start < cursor:
            continue
        parts.append(text[cursor:hit.start])
        parts.append(mask_value(hit.value, hit.type, level))
        cursor = hit.end
    parts.append(text[cursor:])
    return "".join(parts)
