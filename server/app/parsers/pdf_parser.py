"""PDF 接入：阶段四实现。

实现时用 ``pdfplumber``（或 PyMuPDF）按页抽文本，路径用 ``page{页码}.block{块号}``；
扫描件需要 OCR，本期不做。
"""

from __future__ import annotations

from .base import Document, ParseUnsupported


def parse_pdf(data: bytes, source: str = "data.pdf") -> Document:
    raise ParseUnsupported(
        "PDF 解析将在阶段四接入：pdfplumber 按页抽文本后，按 page{页}.block{块} 生成 Segment；扫描件需 OCR，本期不做"
    )
