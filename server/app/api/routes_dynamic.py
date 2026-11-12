"""动态脱敏：原文不落库，按"当前访问者是否有授权"决定返回原文还是脱敏副本。

赛题 v2 要求「静态批量脱敏 + 动态脱敏视图（原文不变、授权切换）」：
这条链路不落库、只做即时渲染，因此每次渲染都写一条审计链记录（谁、在什么时候、
对哪段文本、拿到了原文还是脱敏副本），保证"能看到原文"这件事也可追溯。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from ..audit.chain import append_entry
from ..core.config import Settings
from ..identify import ENTITY_LABELS, Level
from ..identify.pipeline import DetectOptions, sha256_text
from ..security import SecurityError, protected_text
from .deps import Operator, db_dep, operator_dep, pipeline_dep, settings_dep
from .schemas import DynamicRenderIn, DynamicRenderOut, PolicyOut, SpanOut

router = APIRouter()

# 静态策略表：演示"不授权看脱敏、授权看原文"的规则可解释、非黑箱
POLICY_ROWS: tuple[dict[str, object], ...] = (
    {
        "level": "L3",
        "label": "高危",
        "without_auth": "整体替换为 [已屏蔽:类别]，不展示任何原文",
        "with_auth": "展示原文 + 风险标记",
    },
    {
        "level": "L2",
        "label": "中危",
        "without_auth": "保留首字符 + 星号",
        "with_auth": "展示原文 + 风险标记",
    },
    {
        "level": "L1",
        "label": "低危",
        "without_auth": "保留风险警示，敏感值至少部分遮蔽",
        "with_auth": "展示原文",
    },
    {
        "level": "SAFE",
        "label": "安全",
        "without_auth": "原文",
        "with_auth": "原文",
    },
)


def _parse_mask_level(value: str | None) -> Level | None:
    if not value:
        return None
    try:
        return Level.parse(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"mask_level 非法：{value!r}（可选 L3/L2/L1/SAFE）") from exc


def _reveal_operators(settings: Settings) -> set[str]:
    """解析原文查看白名单（逗号分隔）；空配置表示谁都不给原文。"""
    return {item.strip() for item in settings.reveal_operators.split(",") if item.strip()}


@router.get("/dynamic/policy", response_model=PolicyOut)
def dynamic_policy() -> PolicyOut:
    return PolicyOut(levels=[dict(row) for row in POLICY_ROWS])


@router.post("/dynamic/render", response_model=DynamicRenderOut)
def dynamic_render(
    payload: DynamicRenderIn,
    db=Depends(db_dep),
    settings: Settings = Depends(settings_dep),
    operator: Operator = Depends(operator_dep),
    pipeline=Depends(pipeline_dep),
) -> DynamicRenderOut:
    options = DetectOptions(
        enable_l2=settings.enable_l2,
        enable_ner=settings.enable_ner,
        enable_l3=settings.enable_l3,
        mask_level=_parse_mask_level(payload.mask_level),
    )
    try:
        finding = pipeline.analyze_text(payload.text, options)
    except Exception:
        raise SecurityError('DETECTION_FAILED', '检测失败，未返回原文', 503) from None

    if finding is None:
        level = Level.SAFE
        masked = payload.text
        spans: list[SpanOut] = []
        types: list[str] = []
        model_chain = "l1"
        confidence_vector: list[dict[str, object]] = []
    else:
        level = finding.level
        masked = protected_text(payload.text, finding, options.mask_level)
        spans = [SpanOut(**dict(span)) for span in finding.spans]
        types = list(finding.types)
        model_chain = finding.model_chain
        confidence_vector = list(finding.confidence_vector)

    # 授权由服务端判定：客户端自报的 authorized 只表示「想不想要原文」，
    # 真正放行还要看操作人是否在 FLOWMASK_REVEAL_OPERATORS 白名单里。
    # 否则任何人加一个 authorized=true 就能绕过脱敏（旧实现的缺陷）。
    authorized = bool(payload.authorized and operator.operator_id in _reveal_operators(settings))
    shown = payload.text if authorized else masked
    policy = "full_access" if authorized else "masked"

    # 每次渲染都进审计链：能看原文这件事本身也要可追溯（task_id=0 表示非任务型访问）
    append_entry(
        db,
        task_id=0,
        input_hash=sha256_text(payload.text),
        output_hash=sha256_text(shown),
        model_chain=model_chain,
        confidence_vector=json.dumps(confidence_vector, ensure_ascii=False),
        rule_id="dynamic-render",
        operator_id=operator.operator_id,
        user_id=operator.user_id,
    )

    if authorized:
        note = f"已按授权展示原文（操作人 {operator.operator_id}）；本次访问已写入审计链。"
    else:
        note = "未授权：返回脱敏副本。需要原文时开启「授权查看原文」，并留下审计记录。"
        if payload.authorized:
            note += " 服务端授权不足（需在 FLOWMASK_REVEAL_OPERATORS 白名单中）。"
    return DynamicRenderOut(
        authorized=authorized,
        policy=policy,
        level=level.value,
        level_label=level.label,
        color=level.color,
        score=finding.score if finding else 0.0,
        types=types,
        spans=spans,
        type_labels=[ENTITY_LABELS.get(name, name) for name in types],
        masked=masked,
        text=shown,
        model_chain=model_chain,
        operator_id=operator.operator_id,
        note=note,
    )
