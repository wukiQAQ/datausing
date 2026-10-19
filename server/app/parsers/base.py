"""多格式接入的统一中间模型。

所有解析器最终都产出一个 ``Document``：文档由若干 ``Segment`` 组成，每个 Segment
是一段"可识别的文本"（JSON 的字符串值、CSV 的单元格、SQL 的字面量、整段纯文本）。
识别层只依赖这个模型，因此新增文件格式不会动识别与脱敏代码——对应需求里的"插件化"。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Segment:
    """一段待检测文本。

    ``offset`` 是它在原文中的字符偏移（未知时为 -1）。有偏移的格式（text/sql）
    可以按区间回写；结构化格式（json/csv）用 ``path`` 定位字段，导出时按字段回填。
    """

    path: str
    text: str
    offset: int = -1
    # 字段名/列名（JSON 键、CSV 表头、SQL 列名）：结构化数据里"键"本身就是语义上下文，
    # 检测时把它拼在值前面（如 ``api_key: sk-...``）能显著提升 L1 命中率，脱敏只作用在值上。
    hint: str = ""


@dataclass(slots=True)
class Document:
    """一次接入的文档。"""

    source: str
    content_type: str
    segments: list[Segment] = field(default_factory=list)
    raw: str = ""
    meta: dict[str, object] = field(default_factory=dict)

    @property
    def segment_count(self) -> int:
        return len(self.segments)


class ParseError(Exception):
    """文件无法解析（内容损坏、编码不支持等）。"""


class ParseUnsupported(ParseError):
    """格式尚未接入（如 docx/pdf 阶段四才做）。"""
