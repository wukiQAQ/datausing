"""文档解析层：把各种输入统一成 ``Document``（若干 ``Segment``）。

新增格式只需实现一个 ``parse_xxx(data/text) -> Document`` 并在 ``registry.py`` 登记。
"""

from .base import Document, ParseError, ParseUnsupported, Segment
from .csv_parser import parse_csv
from .docx_parser import parse_docx
from .json_parser import parse_json
from .pdf_parser import parse_pdf
from .registry import (
    PENDING_EXTENSIONS,
    SUPPORTED_TEXT_EXTENSIONS,
    parse_bytes,
    parse_text_input,
    supported_formats,
)
from .sql_parser import parse_sql
from .text import parse_text

__all__ = [
    "Document",
    "PENDING_EXTENSIONS",
    "ParseError",
    "ParseUnsupported",
    "SUPPORTED_TEXT_EXTENSIONS",
    "Segment",
    "parse_bytes",
    "parse_csv",
    "parse_docx",
    "parse_json",
    "parse_pdf",
    "parse_sql",
    "parse_text",
    "parse_text_input",
    "supported_formats",
]
