"""接口请求/响应模型（Pydantic v2）。

Swagger（``/docs``）直接读这里，因此描述写清楚：初赛评审点开就能看懂每个字段。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..identify import EntityType


class DetectRequest(BaseModel):
    """即时检测请求（不落库，用于左右对照演示）。"""

    text: str = Field(min_length=1, max_length=200_000, description="待检测文本")
    content_type: str = Field(default="text", description="text / json / csv / sql（自动探测）")
    enable_l2: bool | None = Field(default=None, description="是否启用 L2 语义识别（需推理服务在线）")
    enable_l3: bool | None = Field(default=None, description="是否启用 L3 本地大模型复核（需本地推理服务在线）")
    # None = 跟服务端默认（FLOWMASK_ENABLE_NER）；显式 true/false 按本次请求
    enable_ner: bool | None = Field(default=None, description="是否启用 L1.5 实体识别（人名/地址，适合自由文本）")
    enable_keywords: bool | None = Field(default=None, description="是否启用 L1 敏感词表（法规/行业/企业三级知识库）；None=跟随服务端默认（开）")
    mask_level: str | None = Field(default=None, description="强制按该级别脱敏；空表示按实际级别")
    min_level: str = Field(default="SAFE", description="只返回 ≥ 该级别的结果（SAFE/L1/L2/L3）")


class TextTaskIn(BaseModel):
    """提交文本任务请求（会落库，因此有长度上限且不允许 mask_level=SAFE）。"""

    text: str = Field(min_length=1, max_length=200_000, description="待检测文本（最长 20 万字符）")
    enable_l2: bool | None = Field(default=None, description="是否启用 L2 语义识别（需推理服务在线）")
    enable_l3: bool | None = Field(default=None, description="是否启用 L3 本地大模型复核（需本地推理服务在线）")
    enable_ner: bool = Field(default=False, description="是否启用 L1.5 实体识别（人名/地址）")
    mask_level: str | None = Field(default=None, description="强制按该级别脱敏；任务落库不接受 SAFE")
    min_level: str = Field(default="SAFE", description="只返回 ≥ 该级别的结果（SAFE/L1/L2/L3）")


class SpanOut(BaseModel):
    coverage_types: list[str] = Field(default_factory=list)
    """命中区间：前端据此在原文上高亮（偏移是字符下标，不含敏感值本身）。"""

    start: int
    end: int
    type: str = Field(description="英文类别键：id_card / phone / ...")
    label: str = Field(description="中文类别名：身份证号 / 手机号 / ...")
    rule: str = Field(description="命中的规则名，便于解释与排障")


class SegmentOut(BaseModel):
    requires_full_mask: bool = False
    path: str = Field(description="片段位置：JSON Path / 列名+行号 / SQL 语句+字面量")
    level: str
    score: float
    types: list[str]
    hits: int
    masked: str = Field(description="脱敏副本（不含原文敏感值）")
    model_chain: str = Field(description="实际使用的模型链：l1 / l1>l2 / l1>l2>l3")
    engine_runs: dict[str, str] = Field(default_factory=dict)
    reason: str | None = None
    degraded: bool = False
    direct: bool = False
    spans: list[SpanOut] = Field(default_factory=list, description="命中区间（原文高亮用）")


class DetectOut(BaseModel):
    result_id: str | None = None
    source_fingerprint: str | None = None
    ruleset_revision: str | None = None
    policy_version: str | None = None
    effective_options: dict[str, Any] = Field(default_factory=dict)
    engine_runs: dict[str, dict[str, int]] = Field(default_factory=dict)
    coverage: Literal["complete", "partial"] = "complete"
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
    type_labels: list[str] = Field(default_factory=list, description="类别中文名，顺序与 types 一致")
    source: str = Field(default="", description="任务来源：文件名或 text")
    content_type: str = Field(default="", description="任务内容形态：text/json/csv/sql/…")


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
    tasks: int = Field(description="筛选范围内有检测记录的任务数（去重）")
    by_level: dict[str, int] = Field(description="分级分布（饼图）")
    by_type: list[dict[str, Any]] = Field(description="类别 TopN：type/label/count/hits/levels")
    score_histogram: dict[str, int] = Field(description="分数直方图")
    task_id: str | None = None
    total_hits: int = 0
    tasks_total: int = Field(default=0, description="全库任务总数（兼容旧 tasks 语义）")
    by_day: list[dict[str, Any]] = Field(default_factory=list, description="按当地日趋势")
    risk: dict[str, Any] = Field(default_factory=dict, description="告警处置口径的同筛选统计")
    dimensions: dict[str, Any] = Field(default_factory=dict, description="维度字典（不受筛选影响）")
    filter: dict[str, Any] = Field(default_factory=dict, description="当前筛选回显")


class AlertOut(BaseModel):
    """一条分级告警（检测结果 → 告警 → 处置）。"""

    id: int
    task_id: str
    finding_id: int = Field(description="对应的检测结果 id")
    level: str = Field(description="检测级别：L3 / L2 / L1")
    level_label: str = Field(description="级别中文名：高危 / 中危 / 低危")
    severity: str = Field(description="告警等级：紧急 / 重要 / 提示")
    types: list[str] = Field(description="英文类别键")
    type_labels: list[str] = Field(default_factory=list, description="类别中文名，顺序与 types 一致")
    source: str = Field(default="", description="任务来源：文件名或 text")
    content_type: str = Field(default="", description="任务内容形态：text/json/csv/sql/…")
    score: float
    path: str = Field(description="触发告警的片段位置")
    title: str
    suggestion: str = Field(description="脱敏/处置建议（与四级脱敏模板同一口径）")
    status: str = Field(description="open / ack / ignored")
    status_label: str = Field(description="待处理 / 已认领 / 已忽略")
    created_at: str
    handled_at: str | None = None
    handled_by: str | None = Field(default=None, description="认领/忽略的操作人")
    note: str = ""


class AlertPage(BaseModel):
    items: list[AlertOut]
    total: int
    page: int
    page_size: int


class AlertSummaryOut(BaseModel):
    total: int
    open: int = Field(description="待处理告警数（角标/看板用）")
    by_level: dict[str, int] = Field(description="按级别分布")
    by_status: dict[str, int] = Field(description="按状态分布")
    task_id: str | None = None
    handled: int = Field(default=0, description="ack + ignored（已接手处置，不代表已完成）")
    handled_ratio: float = Field(default=0.0, description="handled / total，total=0 时为 0.0")
    open_by_level: dict[str, int] = Field(default_factory=dict, description="按级别的待处理数")


class AlertAckIn(BaseModel):
    """处理告警：认领（ack）、忽略（ignored）或重置（open）。"""

    status: str = Field(default="ack", description="ack / ignored / open")
    note: str = Field(default="", max_length=200, description="处理备注")


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

    # 前端「新增规则」表单没有 ID 输入项，省略时由服务端生成（custom_xxxxxxxx）；
    # 保留显式传入的能力（脚本/API 用户希望固定 ID 时）。
    id: str | None = Field(
        default=None,
        min_length=2,
        max_length=32,
        pattern=r"^[a-z0-9_]+$",
        description="规则 ID；省略时自动生成",
    )
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
            compiled = re.compile(value)
        except re.error as exc:
            raise ValueError(f"正则表达式非法: {exc}") from exc
        # 捕获组用于定位「要脱敏的片段」；没有捕获组时 match.group(1) 会抛
        # IndexError，把整条检测链路打成 500，所以创建入口就要拦住。
        if compiled.groups < 1:
            raise ValueError("正则至少要有一个捕获组，用 (...) 包住要脱敏的内容")
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
    legacy_rows: int = Field(default=0, description="使用旧签名方案（未覆盖 prev_hash）的记录数")


class KeywordTermOut(BaseModel):
    """一条敏感词：term 是词本身，tier 表示它来自哪一级知识库。"""

    term: str
    category: str
    tier: str = Field(description="regulatory / industry / enterprise")
    weight: float
    confidence: float


class KeywordsOut(BaseModel):
    """当前生效的敏感词表。"""

    total: int = Field(description="本次过滤后的条数")
    all_total: int = Field(description="词表总条目数")
    tiers: dict[str, int] = {}
    terms: list[KeywordTermOut] = []


class HealthOut(BaseModel):
    status: str
    version: str
    db_path: str
    enable_l2: bool
    enable_l3: bool
    # L1 敏感词表：当前加载的条目数与三级分布
    keywords: int = 0
    keyword_tiers: dict[str, int] = {}
    # L1.5 是否可用（模型加载成功），供前端决定是否显示「自由文本模式」
    ner: bool = False
    ner_backend: str = ""
    # 图片 OCR 是否可用（需要可选依赖 requirements-ocr.txt，默认关闭）
    ocr: bool = False
    ocr_backend: str = ""
    # 本地 AI 助手是否可用（默认 rules 模式，不需要模型）
    assistant: bool = False
    assistant_model: str = ""
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
    enable_l2: bool | None = Field(default=None)
    enable_l3: bool | None = Field(default=None)
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
    enable_l2: bool | None = None
    enable_l3: bool | None = None
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
    # 告警联动：本会话各窗口任务自动生成的告警聚合（明细走 /alerts?task_id=...）
    alert_total: int = 0
    alert_open: int = 0
    alert_by_level: dict[str, int] = Field(default_factory=dict)
    last_alert_id: int | None = None
    last_alert_title: str = ""
    last_alert_level: str = ""


class LiveListOut(BaseModel):
    sessions: list[LiveOut] = Field(default_factory=list)


class BatchItemOut(BaseModel):
    """批量任务里的单个文件结果：成功给 task_id，失败只给 error（不影响同批其它文件）。"""

    filename: str
    task_id: str = ""
    content_type: str = ""
    segments: int = 0
    error: str = ""


class BatchTasksOut(BaseModel):
    items: list[BatchItemOut] = Field(default_factory=list)
    total: int = 0
    accepted: int = 0



class PreferenceIn(BaseModel):
    """保存主题偏好：只接受 light / dark / system，其余值由接口返回 400。"""

    theme: str = Field(min_length=1, max_length=16, description="light / dark / system")


class PreferencesOut(BaseModel):
    operator_id: str
    theme: str | None = Field(default=None, description="当前操作人保存的主题；null 表示没选过")
    default_theme: str = Field(description="服务端默认主题（FLOWMASK_DEFAULT_THEME）")
    available_themes: list[str] = Field(default_factory=list)


class PreferenceUserOut(BaseModel):
    operator_id: str
    theme: str
    updated_at: datetime


class PreferenceUsersOut(BaseModel):
    users: list[PreferenceUserOut] = Field(default_factory=list)
    total: int = 0


# --------------------------------------------------------------------------
# 第一次优化：脱敏策略 / 场景模板 / 脱敏文件导出 / AI 助手
# （契约先落在这里，实现分别在 identify/masking.py、scenarios.py、assistant.py、
#   tasks/document_export.py、api/routes_detect.py、api/routes_assistant.py）
# --------------------------------------------------------------------------


class MaskStrategyOut(BaseModel):
    """一种脱敏策略：前端下拉框与说明文案都从这里取，避免前后端各写一份。"""

    key: str
    label: str
    description: str
    reversible: bool = Field(default=False, description="是否可通过服务端密钥/映射还原")
    examples: list[dict[str, str]] = Field(
        default_factory=list, description="示例：category / raw / masked（示例值均为虚构）"
    )


class MaskStrategiesOut(BaseModel):
    strategies: list[MaskStrategyOut]
    default: str = "partial"


class ScenarioOut(BaseModel):
    """使用场景模板：一次选好"脱敏到什么程度、用什么策略、要不要开 L2/L3/NER"。"""

    key: str
    label: str
    description: str
    content_types: list[str] = Field(default_factory=list, description="适用数据形态：text/json/csv/sql/image")
    strategy: str
    mask_level: str = Field(description="强制脱敏级别：L3/L2/L1")
    min_level: str = Field(default="SAFE", description="返回/显示的最低级别")
    enable_l2: bool = False
    enable_l3: bool = False
    enable_ner: bool = False
    retention: str = Field(default="", description="数据保留/流转说明")


class ScenariosOut(BaseModel):
    scenarios: list[ScenarioOut]
    total: int = 0


class DetectExportRequest(BaseModel):
    """即时脱敏导出：同一请求内用内存里的原文重建文件，原文不落库。"""

    text: str | None = Field(default=None, min_length=1, max_length=200_000, description="旧直接导出原文；快照导出可省略")
    result_id: str | None = Field(default=None, max_length=128)
    policy_version: str | None = Field(default=None, max_length=128)
    content_type: str = Field(
        default="text",
        description="原文形态：text / json / csv / sql / docx / xlsx（docx、xlsx 仅上传入口使用）",
    )
    strategy: str = Field(
        default="partial",
        description="脱敏策略：block / partial / hash / generalize / pseudonym / encrypt",
    )
    fmt: str = Field(
        default="auto",
        description="输出格式：auto（跟随 content_type）/ txt / json / csv / sql / docx / xlsx",
    )
    enable_l2: bool | None = None
    enable_l3: bool | None = None
    enable_ner: bool | None = None
    enable_keywords: bool | None = None
    min_level: str = Field(default="SAFE", description="只返回 ≥ 该级别的结果（不影响导出内容的完整脱敏）")


class DetectDecryptIn(BaseModel):
    """加密策略导出的还原：只接受本机服务端密钥加密的 ``enc:`` 片段。"""

    ciphertext: str = Field(min_length=1, max_length=200_000)


class DetectDecryptOut(BaseModel):
    text: str
    replaced: int = Field(description="还原的密文片段数")
    strategy: str = "encrypt"


class AssistantSuggestIn(BaseModel):
    """AI 助手推荐脱敏方案的输入：**只传字段名与计数，不传敏感值**。"""

    scenario: str | None = Field(default=None, description="场景键（可选，见 /scenarios）")
    content_type: str = Field(default="text")
    fields: list[str] = Field(default_factory=list, max_length=64, description="字段/列名（不含值）")
    level_counts: dict[str, int] = Field(default_factory=dict, description="各级别命中数")
    text_length: int = Field(default=0, ge=0, le=1_000_000)
    prompt: str = Field(default="", max_length=200, description="用途说明（不含敏感值，如“交给学校科研团队分析”）")


class AssistantSuggestionOut(BaseModel):
    strategy: str
    mask_level: str
    min_level: str
    enable_l2: bool
    enable_l3: bool
    enable_ner: bool
    retention: str
    rationale: str
    source: str = Field(description="rules（本地规则）/ local_llm（本机模型）/ degraded（模型失败回退规则）")


class AssistantAdviseIn(BaseModel):
    """统计看板的风险研判输入：聚合数字，不含原文。"""

    by_level: dict[str, int] = Field(default_factory=dict)
    by_type: list[dict[str, Any]] = Field(default_factory=list)
    total_findings: int = 0
    tasks: int = Field(default=0, description="筛选范围内有检测记录的任务数（不再表示待处理任务数）")
    scenario: str | None = None
    scope: str = Field(default="", description="统计范围前缀，如「近 30 天」")
    total_alerts: int = 0
    open_alerts: int = 0
    handled_ratio: float | None = Field(default=None, description="已处置告警占比；None 表示未提供")
    open_by_level: dict[str, int] = Field(default_factory=dict)


class AssistantAdviseOut(BaseModel):
    summary: str
    findings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    source: str = "rules"


class AssistantHealthOut(BaseModel):
    enabled: bool
    mode: str
    model: str
    available: bool = Field(description="本机模型是否可用；rules 模式下为 True")
    note: str = ""


class AssistantModelsOut(BaseModel):
    models: list[str] = Field(default_factory=list)
    endpoint: str = ""
    note: str = ""
