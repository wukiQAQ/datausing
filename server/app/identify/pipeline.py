"""三级协同识别流水线。

一条 Segment 的处理顺序：L1 规则 →（可选）L2 语义 →（可选）L3 护栏 → 评分分级 → 脱敏。
设计要点：

- **算力只花在可疑数据上**：L2/L3 默认关闭；开启后也只对 L1 低置信度 / 分数落在模糊区
  的文本调用，其余直接返回。
- **失败必须降级不阻塞**：L2/L3 超时或报错时保留 L1 结论、标记 ``degraded``，
  流水线继续——推理服务不能成为单点。
- **脱敏区间只来自 L1**：L2/L3 只提供类别级置信度与解释，无法定位字符区间，
  因此"屏蔽哪几个字"始终可复现、可审计。
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field, replace

from ..core.config import Settings
from ..parsers.base import Document, Segment
from .l2 import SemanticClassifier, SemanticHit, build_semantic_classifier
from .l3 import GuardClassifier, GuardVerdict, build_guard_classifier
from .masking import mask
from .normalize import map_hit_span, normalize_with_map
from .scoring import analyze as analyze_l1
from .scoring import level_for, score
from .types import EntityType, Hit, Level, Result, entity_label

L2_MIN_CONFIDENCE = 0.70
L2_SCORE_BONUS = 0.05
# L2 判"非敏感"的最小置信度，以及据此的分数压制力度：
# 压制是乘性的，0.9 上限保证"很确信是示例数据"时能把 L2 级形似号码压回 SAFE。
#: benign 压制阈值：在 75 条留出集上调参（0.65/0.75 时误报 25.9%→0%，高危漏检保持 0%）
L2_BENIGN_MIN_CONFIDENCE = 0.65
L2_BENIGN_MAX_PENALTY = 0.90
# 高危直达类（证件/银行卡/密钥）默认不压制（宁误报勿漏报）；
# 只有当 L2 非常确信"这是示例数据"时才允许压制，阈值更高。
#: 直达高危（证件/卡/密钥）要求的 benign 置信度更高，避免一句“示例”把真泄露放过去
L2_BENIGN_DIRECT_MIN_CONFIDENCE = 0.75


def _env_float(name: str, default: float) -> float:
    """读取阈值环境变量（评测调参用），非法值退回默认。"""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _benign_threshold() -> float:
    return _env_float("FLOWMASK_L2_BENIGN_MIN", L2_BENIGN_MIN_CONFIDENCE)


def _direct_benign_threshold() -> float:
    return _env_float("FLOWMASK_L2_BENIGN_DIRECT_MIN", L2_BENIGN_DIRECT_MIN_CONFIDENCE)
AMBIGUOUS_MIN = 0.25
AMBIGUOUS_MAX = 0.85

#: 模型点出这些类别（或泛化标签 PII）时，认为"确实涉及敏感个人信息"
PII_CATEGORIES = frozenset(
    {
        "PII",
        "PHONE",
        "ID_CARD",
        "BANK_CARD",
        "EMAIL",
        "IP",
        "SECRET",
        "PASSWORD",
        "NAME",
        "ADDRESS",
        "MEDICAL",
        "PERSONAL_INFORMATION",
        "PERSONAL_DATA",
    }
)


def _env_flag(name: str, default: bool) -> bool:
    """读取布尔开关（评测调参用），非法值退回默认。"""
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _l3_category_raise() -> bool:
    """L3 类别感知：模型点出 PII 类别时允许把低危抬到中危（默认关，见 l3_measure 实测）。"""
    return _env_flag("FLOWMASK_L3_CATEGORY_RAISE", False)


def _l3_trust_level_hint() -> bool:
    """是否采信指令模型直接给出的分级建议（L3/L2/L1/SAFE）。"""
    return _env_flag("FLOWMASK_L3_TRUST_LEVEL_HINT", True)


def _l3_max_target() -> Level:
    """L3 非 Unsafe 情形下允许抬到的最高级别（类别感知/分级建议共用此上限）。"""
    raw = os.environ.get("FLOWMASK_L3_MAX_TARGET", "L2").strip().upper()
    try:
        return Level.parse(raw) if raw else Level.MID
    except ValueError:
        return Level.MID


def sha256_text(text: str) -> str:
    """原文指纹：写库/审计只存哈希，不存原文。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


MAX_L2_CONTEXT_CHARS = 4000


def _document_context(document: Document) -> str:
    """把整份文档拼成 L2 的上下文（截断控制成本）：键名 + 值都要，只看值会丢线索。"""
    parts: list[str] = []
    used = 0
    for segment in document.segments:
        piece = segment.text
        if segment.hint:
            piece = f"{segment.hint}: {piece}"
        if used + len(piece) > MAX_L2_CONTEXT_CHARS:
            parts.append(piece[: max(MAX_L2_CONTEXT_CHARS - used, 0)])
            break
        parts.append(piece)
        used += len(piece)
    return "\n".join(parts)


def _rebase_result(
    result: Result,
    shift: int,
    index_map: list[int] | None = None,
    text_length: int = 0,
) -> Result:
    """把"检测文本"上的命中偏移映射回"原文"，并重算分数。

    两件事同时发生：
    1. 结构化数据的字段名前缀（``hint: ``）只是识别上下文，不能进脱敏区间——偏移减 ``shift``；
    2. 归一化（全角转半角、去掉数字间分隔符）会改变长度——用 ``index_map`` 映射回原文区间，
       这样脱敏仍然覆盖原文里"带连字符/空格"的完整号码。

    落在前缀或映射不出来的命中直接丢弃；重算分数保证 level 与最终命中集合一致。
    """
    hits: list[Hit] = []
    for hit in result.hits:
        start = hit.start - shift
        end = hit.end - shift
        if index_map is None:
            if end <= 0:
                continue
            start = max(start, 0)
        else:
            span = map_hit_span(index_map, start, end, text_length)
            if span is None:
                continue
            start, end = span
        hits.append(replace(hit, start=start, end=end))
    rebased = score(hits)
    rebased.extra_evidence = list(result.extra_evidence)
    rebased.degraded = result.degraded
    # L3 复核抬升的级别下限要跟着走：重算分数不能把大模型/指令裁判的判断抹掉
    rebased.level_floor = result.level_floor
    if result.score_scale != 1.0:
        rebased.score_scale = result.score_scale
        rebased.score = round(min(rebased.score * result.score_scale, 1.0), 3)
        # 压制后沿用链路里的直接标记（已被 L2 否决时 direct=False），
        # 否则重算会把直达高危又加回来，压制白做。
        rebased.direct = result.direct
        rebased.level = level_for(rebased.score, rebased.direct)
    if rebased.level_floor.severity > rebased.level.severity:
        rebased.level = rebased.level_floor
    return rebased


@dataclass(slots=True)
class DetectOptions:
    """一次检测的开关。默认只跑 L1（最快、离线可跑）。"""

    enable_l2: bool = False
    enable_l3: bool = False
    mask_level: Level | None = None
    min_level: Level = Level.SAFE


@dataclass(slots=True)
class SegmentFinding:
    """一条落库/返回给前端的检测结果。"""

    path: str
    original_hash: str
    masked: str
    level: Level
    score: float
    types: list[str]
    hits: int
    rule_ids: list[str]
    confidence_vector: list[dict[str, object]]
    model_chain: str
    direct: bool = False
    degraded: bool = False
    reason: str | None = None
    # 命中区间（字符偏移 + 类别），供前端在原文上高亮；只有 L1 能给出精确区间
    spans: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "level": self.level.value,
            "level_label": self.level.label,
            "color": self.level.color,
            "score": self.score,
            "types": self.types,
            "hits": self.hits,
            "masked": self.masked,
            "original_hash": self.original_hash,
            "rule_ids": self.rule_ids,
            "confidence_vector": self.confidence_vector,
            "model_chain": self.model_chain,
            "direct": self.direct,
            "degraded": self.degraded,
            "reason": self.reason,
            "spans": self.spans,
        }


@dataclass(slots=True)
class DocumentReport:
    """一份文档的完整检测报告。"""

    source: str
    content_type: str
    segments: int
    findings: list[SegmentFinding] = field(default_factory=list)
    by_level: dict[str, int] = field(default_factory=dict)
    duration_ms: int = 0
    degraded: bool = False


class DetectionPipeline:
    """识别 + 评分 + 脱敏的编排器。"""

    def __init__(
        self,
        semantic: SemanticClassifier | None = None,
        guard: GuardClassifier | None = None,
    ) -> None:
        self.semantic = semantic
        self.guard = guard

    @classmethod
    def from_settings(cls, settings: Settings) -> "DetectionPipeline":
        return cls(
            semantic=build_semantic_classifier(settings),
            guard=build_guard_classifier(settings),
        )

    # ---- 对外入口 -------------------------------------------------------
    def analyze_document(self, document: Document, options: DetectOptions) -> DocumentReport:
        started = time.perf_counter()
        findings: list[SegmentFinding] = []
        by_level = {level.value: 0 for level in Level}
        degraded = False

        # 文档级上下文：JSON/CSV/SQL 的线索词（"示例""测试"）可能不在同一个值里，
        # 传给 L2 让它按整份文档判断"这是文档示例还是真实数据"。
        context = _document_context(document)
        for segment in document.segments:
            finding = self.analyze_segment(segment, options, l2_context=context)
            if finding is None:
                continue
            degraded = degraded or finding.degraded
            by_level[finding.level.value] += 1
            if finding.level.severity < options.min_level.severity:
                continue
            findings.append(finding)

        return DocumentReport(
            source=document.source,
            content_type=document.content_type,
            segments=document.segment_count,
            findings=findings,
            by_level=by_level,
            duration_ms=int((time.perf_counter() - started) * 1000),
            degraded=degraded,
        )

    def analyze_text(self, text: str, options: DetectOptions) -> SegmentFinding | None:
        return self.analyze_segment(Segment(path="$", text=text, offset=0), options)

    def analyze_segment(
        self,
        segment: Segment,
        options: DetectOptions,
        l2_context: str | None = None,
    ) -> SegmentFinding | None:
        # 结构化数据（JSON/CSV/SQL）把字段名拼到值前面一起检测：
        # 字段名是语义上下文（"api_key"、"身份证号"、"password"），没有它，
        # 裸值只能靠形态猜，误判与漏判都会明显上升。
        hint = (segment.hint or "").strip()
        prefix = f"{hint}: " if hint else ""
        # 识别前先归一化（全角→半角、去掉数字之间的空格/连字符），
        # 但脱敏与落库仍以原文为准：偏移通过 index_map 映射回去
        normalized, index_map = normalize_with_map(segment.text)
        normalized_changed = normalized != segment.text
        result, model_chain, reason = self._run_chain(prefix + normalized, options, l2_context)
        if prefix or normalized_changed:
            result = _rebase_result(
                result,
                len(prefix),
                index_map if normalized_changed else None,
                len(segment.text),
            )
        if not result.hits and not result.extra_evidence:
            return None
        return SegmentFinding(
            path=segment.path,
            original_hash=sha256_text(segment.text),
            masked=mask(segment.text, result, options.mask_level),
            level=result.level,
            score=result.score,
            types=result.type_keys,
            hits=len(result.hits),
            rule_ids=result.rule_ids,
            confidence_vector=result.confidence_vector,
            model_chain=model_chain,
            direct=result.direct,
            degraded=result.degraded,
            reason=reason,
            spans=[
                {
                    "start": hit.start,
                    "end": hit.end,
                    "type": hit.type.value,
                    "label": entity_label(hit.type),
                    "rule": hit.rule,
                }
                for hit in result.hits
            ],
        )

    # ---- 内部：三级链路 -------------------------------------------------
    def _run_chain(
        self,
        text: str,
        options: DetectOptions,
        l2_context: str | None = None,
    ) -> tuple[Result, str, str | None]:
        result = analyze_l1(text)
        chain = ["l1"]
        reason: str | None = None

        if options.enable_l2 and self.semantic is not None and self.semantic.available():
            # L2 只处理 L1 命中的样本：直达高危也要过一遍语义复核，
            # 否则"文档里的示例证件号"这类形似误报永远压不下去。
            if result.hits:
                try:
                    # 文档级上下文优先：JSON/CSV 的每个值单独看没有"示例"线索，
                    # 把整份文档的文本给 L2，才能判断"这段是文档示例还是真实数据"。
                    semantic_hits = self.semantic.predict(l2_context or text)
                except Exception:  # 推理服务异常一律降级，不打断主链路
                    semantic_hits = []
                    result.degraded = True
                if semantic_hits:
                    benign = [hit for hit in semantic_hits if hit.benign]
                    typed = [hit for hit in semantic_hits if not hit.benign]
                    if benign:
                        strongest = max(hit.confidence for hit in benign)
                        threshold = (
                            _direct_benign_threshold()
                            if result.direct
                            else _benign_threshold()
                        )
                        if strongest >= threshold:
                            result = _apply_l2_benign(result, strongest)
                    if typed:
                        result = _merge_l2(result, typed)
                    chain.append("l2")

        ambiguous = AMBIGUOUS_MIN <= result.score < AMBIGUOUS_MAX and not result.direct
        if options.enable_l3 and self.guard is not None and self.guard.available() and ambiguous:
            try:
                verdict = self.guard.judge(text)
            except Exception:  # 大模型服务异常一律降级，不打断主链路
                verdict = None
                result.degraded = True
            if verdict is None:
                result.degraded = True
            else:
                chain.append("l3")
                reason = verdict.reason or None
                target = _l3_target_level(verdict, result)
                raised = target is not None
                if raised:
                    result.level = target
                    # 记录级别下限：归一化/结构化前缀会触发重算分数，不记下限会被算回去
                    result.level_floor = target
                result.extra_evidence.append(
                    {
                        "type": "l3_guard",
                        "confidence": 1.0,
                        "weight": 0.0,
                        "rule": verdict.model,
                        "risk": verdict.risk,
                        # 模型给出的类别与分级建议都留证据：解释与消融实验都要用
                        "categories": list(verdict.categories),
                        "level_hint": verdict.level_hint,
                        "raised": raised,
                    }
                )

        return result, ">".join(chain), reason


def _l3_target_level(verdict: GuardVerdict, result: Result) -> Level | None:
    """L3 复核后要抬到的级别；``None`` 表示「只解释、不改级别」。

    优先级：护栏 Unsafe > 指令模型的分级建议 > 类别感知（点出 PII 类别）。

    为什么要设上限与"只升不降"：L3 的定位是复核 + 解释，无边界地信任大模型会把低危
    样本抬成中危——实测放开后分级准确率从 98.7% 掉到 94.7%。因此**所有**升级（含
    Unsafe）都受 ``FLOWMASK_L3_MAX_TARGET``（默认 L2）约束，且只升不降。

    为什么 Unsafe 也要受上限：1.5B 指令模型零样本对边界样本会给出 Unsafe（实测把
    「诊断: 2型糖尿病」判成 Unsafe/L3），不设上限会把金标 L2 抬成 L3、准确率反而掉
    到 97.3%。需要"高危直接升"时显式设 ``FLOWMASK_L3_MAX_TARGET=L3``。
    """
    target: Level | None = None
    if verdict.risk.strip().lower() == "unsafe":
        target = Level.HIGH
    else:
        if _l3_trust_level_hint():
            hint = verdict.suggested_level
            if hint is not None and hint.severity > result.level.severity:
                target = hint
        if target is None and _l3_category_raise() and verdict.has_pii_category(PII_CATEGORIES):
            # 模型点出了身份证/PII 等类别：把低危抬到中危，而不是一路抬到高危
            if result.level.severity < Level.MID.severity:
                target = Level.MID
    if target is not None:
        max_target = _l3_max_target()
        if target.severity > max_target.severity:
            target = max_target
    if target is not None and target.severity <= result.level.severity:
        target = None
    return target


def _apply_l2_benign(result: Result, confidence: float) -> Result:
    """L2 判"示例/测试数据"时按置信度压制分数。

    为什么是乘性压制而不是直接置 SAFE：模型也可能错，乘性压制在低置信度时温和、
    高置信度时接近归零，留出可调空间；压制依据写进 ``extra_evidence``，可审计、可复算。
    """
    if confidence < L2_BENIGN_MIN_CONFIDENCE:
        return result
    penalty = min(confidence, L2_BENIGN_MAX_PENALTY)
    result.score_scale = round(result.score_scale * (1.0 - penalty), 4)
    result.score = round(result.score * (1.0 - penalty), 3)
    # 语义否决后取消"极端类别直达"：直达是给"宁误报勿漏报"用的兜底，
    # 既然 L2 已判定这是示例数据，就不该再靠直达把级别钉死在高危。
    result.direct = False
    result.level = level_for(result.score, result.direct)
    result.extra_evidence.append(
        {
            "type": "l2_benign",
            "confidence": round(confidence, 4),
            "weight": 0.0,
            "rule": "l2-benign",
        }
    )
    return result


def _merge_l2(result: Result, semantic_hits: list[SemanticHit]) -> Result:
    """把 L2 的类别置信度并入 L1 结果：补类别、加少量分数、记录证据。"""
    updated_types = list(result.types)
    bonus = 0.0
    for hit in semantic_hits:
        if hit.benign or hit.type is None or hit.confidence < L2_MIN_CONFIDENCE:
            continue
        if hit.type not in updated_types:
            updated_types.append(hit.type)
            bonus += L2_SCORE_BONUS * hit.confidence
        result.extra_evidence.append(
            {
                "type": hit.type.value,
                "confidence": round(hit.confidence, 4),
                "weight": 0.0,
                "rule": hit.source,
            }
        )
    if bonus:
        result.score = round(min(result.score + bonus, 1.0), 3)
        result.level = level_for(result.score, result.direct)
        result.types = updated_types
    return result
