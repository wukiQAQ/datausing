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
import time
from dataclasses import dataclass, field

from ..core.config import Settings
from ..parsers.base import Document, Segment
from .l2 import SemanticClassifier, SemanticHit, build_semantic_classifier
from .l3 import GuardClassifier, build_guard_classifier
from .masking import mask
from .scoring import analyze as analyze_l1
from .scoring import level_for
from .types import EntityType, Level, Result

L2_MIN_CONFIDENCE = 0.70
L2_SCORE_BONUS = 0.05
AMBIGUOUS_MIN = 0.25
AMBIGUOUS_MAX = 0.85


def sha256_text(text: str) -> str:
    """原文指纹：写库/审计只存哈希，不存原文。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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

        for segment in document.segments:
            finding = self.analyze_segment(segment, options)
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

    def analyze_segment(self, segment: Segment, options: DetectOptions) -> SegmentFinding | None:
        result, model_chain, reason = self._run_chain(segment.text, options)
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
        )

    # ---- 内部：三级链路 -------------------------------------------------
    def _run_chain(self, text: str, options: DetectOptions) -> tuple[Result, str, str | None]:
        result = analyze_l1(text)
        chain = ["l1"]
        reason: str | None = None

        if options.enable_l2 and self.semantic is not None and self.semantic.available():
            if result.level in (Level.SAFE, Level.LOW) or not result.direct:
                try:
                    semantic_hits = self.semantic.predict(text)
                except Exception:  # 推理服务异常一律降级，不打断主链路
                    semantic_hits = []
                    result.degraded = True
                if semantic_hits:
                    result = _merge_l2(result, semantic_hits)
                    chain.append("l2")

        ambiguous = AMBIGUOUS_MIN <= result.score < AMBIGUOUS_MAX and not result.direct
        if options.enable_l3 and self.guard is not None and self.guard.available() and ambiguous:
            try:
                verdict = self.guard.judge(text)
            except Exception:
                verdict = None
                result.degraded = True
            if verdict is None:
                result.degraded = True
            else:
                chain.append("l3")
                reason = verdict.reason or None
                if verdict.level.severity > result.level.severity:
                    result.level = verdict.level
                result.extra_evidence.append(
                    {
                        "type": "l3_guard",
                        "confidence": 1.0,
                        "weight": 0.0,
                        "rule": verdict.model,
                        "risk": verdict.risk,
                    }
                )

        return result, ">".join(chain), reason


def _merge_l2(result: Result, semantic_hits: list[SemanticHit]) -> Result:
    """把 L2 的类别置信度并入 L1 结果：补类别、加少量分数、记录证据。"""
    updated_types = list(result.types)
    bonus = 0.0
    for hit in semantic_hits:
        if hit.confidence < L2_MIN_CONFIDENCE:
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
