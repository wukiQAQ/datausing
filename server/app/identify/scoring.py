"""敏感度评分与四级分级。

评分公式（与 Go 版 ``internal/dlp/score.go`` 一致，也是《需求分析》创新点 3 的落点）：

    score = max(weight_i × confidence_i)                       # ① 类别差异化赋权取加权最大
          + min(0.06 × (类别数 - 1), 0.18)                      # ② 多标签共现修正（封顶 0.18）
          + 身份类加成（2 类 +0.10，≥3 类 +0.15）                # ③ 能组合定位到自然人时升级
    score = min(score, 1.0)

    if 命中类别 ∈ {身份证, 银行卡, 密钥凭据} 且 confidence ≥ 0.9:
        level = L3                                             # ④ 极端阈值直达，不受加权影响
    else: score ≥ 0.85 → L3；≥ 0.55 → L2；≥ 0.25 → L1；否则 SAFE

这样设计的原因：取"加权最大"而不是求和，是为了不让低权重类别（如 IP）把高危类别拖下来；
共现修正与身份加成刻画的是"多条信息组合后可定位到个人"的风险，且有上限避免爆表；
极端类别直达则是安全底线——证件号、银行卡、密钥不允许被平均掉。
"""

from __future__ import annotations

from .decode import decode_enabled_by_default, decoded_hits
from .keywords import keyword_hits, keywords_enabled_by_default
from .gazetteer import gazetteer_enabled_by_default, gazetteer_hits
from .rules import dedupe_overlaps, detect
from .types import EntityType, Hit, Level, Result

THRESHOLD_HIGH = 0.85
THRESHOLD_MID = 0.55
THRESHOLD_LOW = 0.25

CO_OCCUR_STEP = 0.06
CO_OCCUR_CAP = 0.18
IDENTITY_BONUS_TWO = 0.10
IDENTITY_BONUS_THREE = 0.15
DIRECT_CONFIDENCE = 0.90

DIRECT_TYPES: frozenset[EntityType] = frozenset(
    {EntityType.ID_CARD, EntityType.BANK_CARD, EntityType.SECRET}
)
IDENTITY_TYPES: frozenset[EntityType] = frozenset(
    {EntityType.NAME, EntityType.PHONE, EntityType.ADDRESS, EntityType.EMAIL, EntityType.ID_CARD}
)


def level_for(score: float, direct: bool = False) -> Level:
    """分数/直达标记 → 四级。"""
    if direct or score >= THRESHOLD_HIGH:
        return Level.HIGH
    if score >= THRESHOLD_MID:
        return Level.MID
    if score >= THRESHOLD_LOW:
        return Level.LOW
    return Level.SAFE


# 敏感词表命中的封顶加分：它只是「这段正文在谈敏感主题」的线索，不是敏感值本身。
# 单独出现绝不能把一个 SAFE 文本顶到 L1，所以总分上限压到 LOW 阈值以下；
# 只有在已有真实命中时，它才作为旁证把分数往上推一点点。
KEYWORD_BONUS_STEP = 0.03
KEYWORD_BONUS_CAP = 0.10


def _is_keyword_hit(hit: Hit) -> bool:
    return hit.rule.startswith("kw-")


def score(hits: list[Hit]) -> Result:
    """对一组命中做评分分级。输入为空时返回 SAFE 空结果。

    参与「加权最大 / 多标签共现 / 身份类加成 / 极端类别直达」的只有真实敏感值命中；
    敏感词表命中（rule 以 ``kw-`` 开头）单独计入封顶加分，避免把「正文提到身份证」
    误判成「正文泄露了身份证号」。
    """
    if not hits:
        return Result()

    value_hits = [hit for hit in hits if not _is_keyword_hit(hit)]
    keyword_hits = [hit for hit in hits if _is_keyword_hit(hit)]

    types: list[EntityType] = []
    for hit in value_hits:
        if hit.type not in types:
            types.append(hit.type)

    if value_hits:
        base = max(hit.weight * hit.confidence for hit in value_hits)
    else:
        base = 0.0
    co_occur = min(CO_OCCUR_STEP * (len(types) - 1), CO_OCCUR_CAP) if types else 0.0

    identity_count = sum(1 for entity in types if entity in IDENTITY_TYPES)
    identity_bonus = 0.0
    if identity_count >= 3:
        identity_bonus = IDENTITY_BONUS_THREE
    elif identity_count == 2:
        identity_bonus = IDENTITY_BONUS_TWO

    keyword_types = {hit.type for hit in keyword_hits}
    keyword_bonus = min(KEYWORD_BONUS_STEP * len(keyword_types), KEYWORD_BONUS_CAP)

    total = round(min(base + co_occur + identity_bonus + keyword_bonus, 1.0), 3)
    direct = any(
        hit.type in DIRECT_TYPES
        and (hit.confidence >= DIRECT_CONFIDENCE or hit.direct_hint)
        for hit in value_hits
    )
    return Result(
        hits=list(hits),
        score=total,
        level=level_for(total, direct),
        types=types,
        direct=direct,
    )


def analyze(
    text: str,
    *,
    enable_decode: bool | None = None,
    enable_gazetteer: bool | None = None,
    enable_keywords: bool | None = None,
) -> Result:
    """一次完整识别：L1 规则 + 敏感词表 + 地名库补白 + 编码载荷解码复核 + 评分分级。

    ``enable_decode`` / ``enable_gazetteer`` / ``enable_keywords`` 传 None 时跟随环境变量开关
    （默认开启）；显式传 False 可关掉，便于做消融实验与回归对照。
    """
    hits = detect(text)
    extras: list[Hit] = []

    # 敏感词表：补「这段正文在谈敏感主题」的线索，权重低，单命中不会直接升级
    if enable_keywords is None:
        enable_keywords = keywords_enabled_by_default()
    if enable_keywords:
        extras.extend(keyword_hits(text))

    if enable_gazetteer is None:
        enable_gazetteer = gazetteer_enabled_by_default()
    if enable_gazetteer:
        extras.extend(gazetteer_hits(text))

    if enable_decode is None:
        enable_decode = decode_enabled_by_default()
    if enable_decode:
        extras.extend(decoded_hits(text, detect))

    if extras:
        hits = dedupe_overlaps(hits + extras)
    return score(hits)
