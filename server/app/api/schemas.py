"""接口请求/响应模型（Pydantic v2）。

Swagger（``/docs``）直接读这里，因此描述写清楚：初赛评审点开就能看懂每个字段。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..identify import EntityType


class DetectRequest(BaseModel):
    """即时检测请求（不落库，用于左右对照演示）。"""

    text: str = Field(min_length=1, max_length=200_000, description="待检测文本")
    content_type: str = Field(default="text", description="text / json / csv / sql（自动探测）")
    enable_l2: bool = Field(default=False, description="是否启用 L2 语义识别（需推理服务在线）")
    enable_l3: bool = Field(default=False, description="是否启用 L3 本地大模型复核（需 Ollama 在线）")
    # None = 跟服务端默认（FLOWMASK_ENABLE_NER）；显式 true/false 按本次请求
    enable_ner: bool | None = Field(default=None, description="是否启用 L1.5 实体识别（人名/地址，适合自由文本）")
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
    # L1.5 是否可用（模型加载成功），供前端决定是否显示「自由文本模式」
    ner: bool = False
    ner_backend: str = ""
    uptime_s: int

class DynamicRenderIn(BaseModel):
    """动态脱敏渲染请求：同一段文本，按是否授权返回两种视图。"""

    text: str = Field(min_length=1, max_length=20000)
    authorized: bool = False
    mask_level: str | None = Field(default=None, description="可选：强制按该级别脱敏（L3/L2/L1/SAFE）")


class DynamicRenderOut(BaseModel):
    authorized: bool
    policy: str
    level: str
    level_label: str
    color: str
    score: float
    types: list[str]
    type_labels: list[str]
    spans: list[SpanOut] = Field(default_factory=list)
    masked: str
    text: str
    model_chain: str
    operator_id: str
    note: str


class PolicyOut(BaseModel):
    levels: list[dict[str, object]]

class TrafficFormatsOut(BaseModel):
    """流量导入能力声明（前端据此显示上传入口）。"""

    formats: list[str]
    max_upload_mb: int
    note: str = ""


class TrafficImportOut(BaseModel):
    """导入结果：报文/会话规模 + 后台任务号（结果仍从 /tasks/{id} 查）。"""

    task_id: str
    source: str
    packets: int
    decoded_frames: int
    sessions: int
    messages: int
    segments: int
    operator_id: str


class IngestSegmentIn(BaseModel):
    """外部采集器推送的一条正文。"""

    path: str = Field(min_length=1, max_length=512, description="来源标识，如 tcp 10.0.0.5:5001→10.0.0.9:80 POST /api/order")
    text: str = Field(min_length=1, max_length=200_000, description="待检测正文（原文不落库）")
    hint: str = Field(default="", max_length=128, description="字段名/列名等上下文，帮助识别姓名/地址这类弱规则")


class TrafficIngestIn(BaseModel):
    """旁路 agent 推送一批正文：一次请求 = 一个检测任务。"""

    source: str = Field(default="agent", max_length=128, description="采集来源标识（会写进任务 source）")
    content_type: str = Field(default="ingest", max_length=32)
    segments: list[IngestSegmentIn] = Field(min_length=1, max_length=200)
    enable_l2: bool = Field(default=False)
    enable_l3: bool = Field(default=False)
    enable_ner: bool | None = Field(default=None)
    mask_level: str | None = Field(default=None)
    min_level: str = Field(default="SAFE")


class TrafficIngestOut(BaseModel):
    """推送结果：后台任务号，检测结果从 /tasks/{id} 或看板查。"""

    task_id: str
    source: str
    segments: int
    operator_id: str


class LiveStartIn(BaseModel):
    """开始实时跟随一个持续写入的 pcap 文件。"""

    pcap_path: str = Field(
        default="live.pcap",
        max_length=512,
        description="抓包文件路径（相对数据目录，或数据目录下的绝对路径）",
    )
    interval_ms: int = Field(default=500, ge=100, le=10_000, description="轮询间隔（毫秒）")
    window_s: float = Field(default=2.0, ge=0.2, le=60.0, description="聚合窗口：每窗口生成一个任务")
    enable_l2: bool = False
    enable_l3: bool = False
    # None 表示“跟服务端默认走”（FLOWMASK_ENABLE_NER）；显式 true/false 则按本次请求
    enable_ner: bool | None = None
    mask_level: str | None = None
    min_level: str = "SAFE"


class LiveOut(BaseModel):
    """实时跟随的当前状态。``running=false`` 表示已停止（含线程异常退出）。"""

    live_id: str
    pcap_path: str
    source: str
    running: bool
    started_at: datetime
    stopped_at: datetime | None = None
    interval_ms: int
    window_s: float
    packets: int
    segments: int
    tasks: int
    last_task_id: str | None = None
    last_error: str = ""


class LiveListOut(BaseModel):
    sessions: list[LiveOut] = Field(default_factory=list)
