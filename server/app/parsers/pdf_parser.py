"""PDF 接入：优先 ``pdfplumber``，退而用 ``pypdf``，两者都没有时给出可照做的安装提示。

PDF 是"排版格式"不是"内容格式"：扫描件没有文本层，抽取只能是空字符串——
这种情形返回明确错误提示（需要 OCR），而不是假装成功。

Segment 路径用 ``page{页号}``（页号从 1 开始）；``raw`` 是各页文本按序拼接。
"""

from __future__ import annotations

import io

from .base import Document, ParseError, ParseUnsupported, Segment

_MAX_PAGES = 200


def pdf_engine() -> str | None:
    """返回当前可用的 PDF 解析引擎名；都不可用返回 None。"""
    try:
        import pdfplumber  # noqa: F401

        return "pdfplumber"
    except ImportError:
        pass
    try:
        import pypdf  # noqa: F401

        return "pypdf"
    except ImportError:
        return None


def _extract_with_pdfplumber(data: bytes) -> list[str]:
    import pdfplumber

    pages: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages[:_MAX_PAGES]:
            pages.append(page.extract_text() or "")
    return pages


def _extract_with_pypdf(data: bytes) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages: list[str] = []
    for page in reader.pages[:_MAX_PAGES]:
        pages.append(page.extract_text() or "")
    return pages


def parse_pdf(data: bytes, source: str = "data.pdf") -> Document:
    """解析 PDF 的文本层，按页切分。"""
    engine = pdf_engine()
    if engine is None:
        raise ParseUnsupported(
            "PDF 解析需要可选依赖：执行 pip install pdfplumber（或 pip install pypdf）后重试"
        )
    try:
        if engine == "pdfplumber":
            pages = _extract_with_pdfplumber(data)
        else:
            pages = _extract_with_pypdf(data)
    except ParseError:
        raise
    except Exception as exc:  # 第三方库对损坏文件会抛各种异常，统一转成 ParseError
        raise ParseError(f"PDF 解析失败（引擎 {engine}）：{exc}") from exc

    segments = [
        Segment(path=f"page{index}", text=text)
        for index, text in enumerate(pages, start=1)
        if text.strip()
    ]
    if not segments:
        raise ParseError("PDF 里没有可提取的文本层（可能是扫描件/图片版，需要 OCR）")
    return Document(
        source=source,
        content_type="pdf",
        segments=segments,
        raw="\n".join(segment.text for segment in segments),
        meta={"pages": len(pages), "engine": engine, "text_pages": len(segments)},
    )
