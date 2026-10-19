"""Word（.docx）接入：用标准库解析 OOXML，不引入第三方依赖。

.docx 本质是一个 zip：正文在 ``word/document.xml``。这里按"文档顺序"遍历
``w:body`` 的直接子节点，段落取 ``w:p``（拼接其中所有 ``w:t``，``w:tab`` 记制表符、
``w:br`` 记换行），表格取 ``w:tbl``（逐格再取段落文本）。

Segment 路径：段落 ``p{序号}``、表格单元格 ``table{表号}.{行}.{列}``，
这样脱敏副本可以按路径精确回填到原文档结构里。
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile

from .base import Document, ParseError, Segment

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# 单个 .docx 解压后的 document.xml 上限，避免压缩炸弹
_MAX_XML_BYTES = 64 * 1024 * 1024


def _paragraph_text(paragraph: ET.Element) -> str:
    """把 ``w:p`` 里的 run 拼成文本，tab/br 还原成制表符与换行。"""
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{_W}t":
            parts.append(node.text or "")
        elif node.tag == f"{_W}tab":
            parts.append("\t")
        elif node.tag == f"{_W}br":
            parts.append("\n")
    return "".join(parts)


def _cell_text(cell: ET.Element) -> str:
    lines = [_paragraph_text(p) for p in cell.findall(f"{_W}p")]
    return "\n".join(line for line in lines if line.strip())


def parse_docx(data: bytes, source: str = "data.docx") -> Document:
    """解析 .docx，返回按段落/表格单元格切分的 Document。"""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > _MAX_XML_BYTES:
                raise ParseError("Word 文档正文过大（解压后超过 64MB）")
            xml_bytes = archive.read(info)
    except KeyError as exc:
        raise ParseError("不是有效的 .docx：缺少 word/document.xml（老式 .doc 请另存为 .docx）") from exc
    except zipfile.BadZipFile as exc:
        raise ParseError("不是有效的 .docx：文件不是 zip 结构（可能已损坏或加密）") from exc

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ParseError(f"Word 正文 XML 解析失败：{exc}") from exc

    body = root.find(f"{_W}body")
    if body is None:
        raise ParseError("Word 文档缺少 body 节点")

    segments: list[Segment] = []
    paragraphs = 0
    tables = 0
    for child in list(body):
        if child.tag == f"{_W}p":
            text = _paragraph_text(child)
            if text.strip():
                paragraphs += 1
                segments.append(Segment(path=f"p{paragraphs}", text=text))
        elif child.tag == f"{_W}tbl":
            tables += 1
            for row_index, row in enumerate(child.findall(f"{_W}tr"), start=1):
                for column_index, cell in enumerate(row.findall(f"{_W}tc"), start=1):
                    text = _cell_text(cell)
                    if text.strip():
                        segments.append(
                            Segment(path=f"table{tables}.{row_index}.{column_index}", text=text)
                        )

    if not segments:
        raise ParseError("Word 文档里没有可检测的文本（空文档或只有图片）")
    return Document(
        source=source,
        content_type="docx",
        segments=segments,
        raw="\n".join(segment.text for segment in segments),
        meta={"paragraphs": paragraphs, "tables": tables},
    )
