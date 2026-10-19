"""接口请求/响应模型（Pydantic v2）。

Swagger（``/docs``）直接读这里，因此描述写清楚：初赛评审点开就能看懂每个字段。
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..identify import EntityType


class DetectRequest(BaseModel):
    """即时检测请求（不落库，用于左右对照演示）。"""

    text: str = Field(min_length=1, max_length=200_000, description="待检测文本")
    content_type: str = Field(default="text", description="text / json / csv / sql（自动探测）")
    enable_l2: bool = Field(default=False, description="是否启用 L2 语义识别（需推理服务在线）")
    enable_l3: bool = Field(default=False, description="是否启用 L3 本地大模型复核（需 Ollama 在线）")
    mask_level: str | None = Field(default=None, description="强制按该级别脱敏；空表示按实际级别")
    min_level: str = Field(default="SAFE", description="只返回 ≥ 该级别的结果（SAFE/L1/L2/L3）")


class SpanOut(BaseModel):
    """命中区间：前端据此在原文上高亮（偏移是字符下标，不含敏感值本身）。"""

    start: int
    end: int
    type: str = Field(description="英文类别键：id_card / phone / ...")
    label: str = Field(description="中文类别名：身份证号 / 手机号 / ...")
    rule: str = Field(description="命中的规则名，便于解释与排障")


class SegmentOut(BaseModel):
    path: str = Field(description="片段位置：JSON Path / 列名+行号 / SQL 语句+字面量")
    level: str
    score: float
    types: list[str]
    hits: int
    masked: str = Field(description="脱敏副本（不含原文敏感值）")
    model_chain: str = Field(description="实际使用的模型链：l1 / l1>l2 / l1>l2>l3")
    reason: str | None = None
    degraded: bool = False
    direct: bool = False
    spans: list[SpanOut] = Field(default_factory=list, description="命中区间（原文高亮用）")


class DetectOut(BaseModel):
    content_type: str
    segments: int = Field(description="实际检测的片段数")
    findings: list[SegmentOut]
    by_level: dict[str, int]
    duration_ms: int
    degraded: bool
    masked_document: dict[str, Any] = Field(description="脱敏后的文档表示，用于左右对照")


class TaskCreateOut(BaseModel):
    task_id: str
    status: str


class TaskOut(BaseModel):
    id: str
    created_at: str
    finished_at: str | None = None
    status: str = Field(description="pending / running / done / failed")
    source: str
    content_type: str
    options: dict[str, Any] = Field(default_factory=dict)
    total_segments: int = 0
    total_findings: int = 0
    error: str | None = None


class FindingOut(BaseModel):
    id: int
    task_id: str
    path: str
    level: str
    score: float
    types: list[str]
    hits: int
    masked: str
    original_hash: str = Field(description="原文 SHA-256：可对位审计，但不泄露内容")
    model_chain: str
    confidence_vector: list[dict[str, Any]] = Field(default_factory=list)
    rule_id: str | None = None
    degraded: bool = False
    created_at: str


class FindingPage(BaseModel):
    items: list[FindingOut]
    total: int
    page: int
    page_size: int


class TaskPage(BaseModel):
    items: list[TaskOut]
    total: int
    page: int
    page_size: int


class StatsOut(BaseModel):
    total_findings: int
    tasks: int
    by_level: dict[str, int] = Field(description="分级分布（饼图）")
    by_type: list[dict[str, Any]] = Field(description="类别 TopN（柱状图）")
    score_histogram: dict[str, int] = Field(description="分数直方图")
    task_id: str | None = None


class RuleOut(BaseModel):
    id: str
    name: str
    type: str
    pattern: str
    weight: float
    confidence: float
    enabled: bool
    updated_at: str | None = None
    builtin: bool = True


class RuleIn(BaseModel):
    """自定义规则（正则 + 类别 + 权重）。"""

    id: str = Field(min_length=2, max_length=32, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=64)
    type: str
    pattern: str = Field(min_length=1, max_length=500)
    weight: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    enabled: bool = True

    @field_validator("type")
    @classmethod
    def _valid_type(cls, value: str) -> str:
        allowed = {entity.value for entity in EntityType}
        if value not in allowed:
            raise ValueError(f"未知类别: {value}，可选 {sorted(allowed)}")
        return value

    @field_validator("pattern")
    @classmethod
    def _valid_pattern(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"正则表达式非法: {exc}") from exc
        return value


class AuditOut(BaseModel):
    id: int
    ts: str
    task_id: str
    input_hash: str
    output_hash: str
    model_chain: str
    confidence_vector: str | None = None
    rule_id: str | None = None
    operator_id: str
    user_id: str
    prev_hash: str
    signature: str
    entry_hash: str = Field(description="服务端按固定口径重算的本条哈希，可手动复核")


class AuditPage(BaseModel):
    items: list[AuditOut]
    total: int
    page: int
    page_size: int


class VerifyOut(BaseModel):
    ok: bool
    total: int
    checked: int
    broken_at: int | None = None
    reason: str = ""
    head_hash: str


class HealthOut(BaseModel):
    status: str
    version: str
    db_path: str
    enable_l2: bool
    enable_l3: bool
    uptime_s: int
