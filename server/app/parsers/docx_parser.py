"""Word（.docx）接入：阶段四实现。

预留接口与错误语义：调用方拿到 ``ParseUnsupported`` 后应提示"该格式将在阶段四支持"，
而不是 500。实现时用 ``python-docx`` 抽段落与表格单元格，路径用 ``p{段落号}`` /
``table{表号}.{行}.{列}``。
"""

from __future__ import annotations

from .base import Document, ParseUnsupported


def parse_docx(data: bytes, source: str = "data.docx") -> Document:
    raise ParseUnsupported(
        "Word（.docx）解析将在阶段四接入：python-docx 抽段落与表格后，按 p{段落}/table{表}.{行}.{列} 生成 Segment"
    )
