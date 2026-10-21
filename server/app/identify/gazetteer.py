"""地名库（gazetteer）补白：自由文本里只出现省/市名时也能召回「地址」类敏感。

背景：现有 L1 地址规则要么依赖字段名（``地址：``），要么依赖 ``省/市/区/路/号`` 这类后缀。
但真实叙述里常常只出现一个地名：

    他昨天从湖南过来，住了一晚就走了
    这批货是从四川发出来的

这类文本里「湖南 / 四川」是能被人工识别的地址信息，却因为缺少后缀与字段名被漏掉——
压力集里纯 L1 的地址召回因此偏低。这一层用**行政区地名库 + 上下文约束**补召回：

- 地名命中必须满足「后缀」或「方位词」之一，避免把普通词里的字片段当地名：
  后缀 = 省/市/区/县/路/街/镇/村/号/自治区/特别行政区；方位词 = 在/到/去/往/从/来自/位于/住在/
  回到/迁往/定居/发往/寄往；
- 类别固定为 ``address``、级别只到 L1（权重/置信度与 ``address_kv`` 同量级，不抬级别）；
- 与已有命中重叠时由 ``dedupe_overlaps`` 保留更精确的那条（字段名规则优先）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .types import EntityType, Hit

# 与 address_kv 同量级：只补类别、不单独把分数抬到中危
GAZETTEER_ADDRESS_WEIGHT = 0.50
GAZETTEER_ADDRESS_CONFIDENCE = 0.65

PROVINCES: tuple[str, ...] = (
    "北京",
    "天津",
    "上海",
    "重庆",
    "河北",
    "山西",
    "辽宁",
    "吉林",
    "黑龙江",
    "江苏",
    "浙江",
    "安徽",
    "福建",
    "江西",
    "山东",
    "河南",
    "湖北",
    "湖南",
    "广东",
    "海南",
    "四川",
    "贵州",
    "云南",
    "陕西",
    "甘肃",
    "青海",
    "台湾",
    "内蒙古",
    "广西",
    "西藏",
    "宁夏",
    "新疆",
    "香港",
    "澳门",
)

# 省会 + 计划单列市 + 主要地级市（够覆盖业务文本里的常见地名，不追求穷举）
CITIES: tuple[str, ...] = (
    "石家庄",
    "太原",
    "沈阳",
    "长春",
    "哈尔滨",
    "南京",
    "杭州",
    "合肥",
    "福州",
    "南昌",
    "济南",
    "郑州",
    "武汉",
    "长沙",
    "广州",
    "海口",
    "成都",
    "贵阳",
    "昆明",
    "西安",
    "兰州",
    "西宁",
    "呼和浩特",
    "南宁",
    "拉萨",
    "银川",
    "乌鲁木齐",
    "深圳",
    "厦门",
    "宁波",
    "青岛",
    "大连",
    "苏州",
    "无锡",
    "东莞",
    "佛山",
    "珠海",
    "三亚",
    "洛阳",
    "徐州",
    "温州",
    "泉州",
    "烟台",
    "常州",
    "南通",
    "嘉兴",
    "绍兴",
    "中山",
    "惠州",
    "汕头",
)

# 长词优先，避免「内蒙古」被「内蒙」截断这类问题
_PLACES: tuple[str, ...] = tuple(sorted({*PROVINCES, *CITIES}, key=len, reverse=True))

_SUFFIX = r"(?:省|市|区|县|路|街|镇|村|号|自治区|特别行政区)"
#「看到/想到/收到」里的「到」是动词后缀而不是方位词，用前置字排除掉
_TO_VERB_PREFIX = "看想收得达遇听感找梦受遭"
_DIRECTION = (
    r"(?:在|(?<![" + _TO_VERB_PREFIX + r"])到|去|往|从|来自|位于|住在|回到|迁往|定居|发往|寄往)"
)

_PLACE_ALT = "|".join(_PLACES)
# 方位词在左，地名在右，命中从地名开始（不含方位词，脱敏只盖地名本身）
_DIRECTIONAL_RE = re.compile(r"(?:" + _DIRECTION + r")\s*(" + _PLACE_ALT + r")")
@dataclass(frozen=True, slots=True)
class PlaceMention:
    start: int
    end: int
    place: str
    reason: str  # "suffix" / "direction"


def iter_place_mentions(text: str) -> list[PlaceMention]:
    """扫描文本里的行政区地名，只保留有后缀或方位词作为依据的命中。"""
    mentions: list[PlaceMention] = []
    taken: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < e and s < end for s, e in taken)

    for match in _DIRECTIONAL_RE.finditer(text):
        start, end = match.span(1)
        if overlaps(start, end):
            continue
        mentions.append(PlaceMention(start, end, match.group(1), "direction"))
        taken.append((start, end))

    for match in re.finditer(_PLACE_ALT, text):
        start, end = match.span()
        if overlaps(start, end):
            continue
        # 只有当紧邻的后面出现行政后缀时，才算「有依据」
        if not re.match(_SUFFIX, text[end:]) and not re.match(r"\s*" + _SUFFIX, text[end:]):
            continue
        mentions.append(PlaceMention(start, end, match.group(0), "suffix"))
        taken.append((start, end))

    mentions.sort(key=lambda item: item.start)
    return mentions


def gazetteer_hits(text: str) -> list[Hit]:
    """把地名命中转成 address 类 Hit（L1 量级，不抬级别）。"""
    hits: list[Hit] = []
    for mention in iter_place_mentions(text):
        hits.append(
            Hit(
                type=EntityType.ADDRESS,
                start=mention.start,
                end=mention.end,
                value=text[mention.start : mention.end],
                weight=GAZETTEER_ADDRESS_WEIGHT,
                confidence=GAZETTEER_ADDRESS_CONFIDENCE,
                rule=f"gazetteer-{mention.reason}",
            )
        )
    return hits


def gazetteer_enabled_by_default() -> bool:
    """地名库默认开启（可用 FLOWMASK_DISABLE_GAZETTEER=1 关掉）。"""
    import os

    return os.environ.get("FLOWMASK_DISABLE_GAZETTEER", "").strip().lower() not in {"1", "true", "yes"}


def known_places() -> Iterable[str]:
    """暴露地名表给测试/文档使用。"""
    return _PLACES


__all__ = [
    "CITIES",
    "GAZETTEER_ADDRESS_CONFIDENCE",
    "GAZETTEER_ADDRESS_WEIGHT",
    "PROVINCES",
    "PlaceMention",
    "gazetteer_enabled_by_default",
    "gazetteer_hits",
    "iter_place_mentions",
    "known_places",
]
