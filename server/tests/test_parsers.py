"""解析层测试：文本 / JSON / CSV / SQL / Word / PDF / 上传分派 / 编码兼容。"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.parsers import (
    ParseError,
    ParseUnsupported,
    parse_bytes,
    parse_csv,
    parse_docx,
    parse_json,
    parse_pdf,
    parse_sql,
    parse_text,
    parse_text_input,
    supported_formats,
)

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def make_docx(body_xml: str) -> bytes:
    """构造最小可用的 .docx（只含 word/document.xml，够我们的解析器用）。"""
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}"><w:body>{body_xml}</w:body></w:document>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def test_parse_text_keeps_whole_text_as_single_segment() -> None:
    document = parse_text("第一行\n\n第二行\n")
    assert document.content_type == "text"
    # 整段文本作为一个 Segment：正则可跨行匹配，脱敏后也能整体回写
    assert len(document.segments) == 1
    assert document.segments[0].path == "$"
    assert document.segments[0].offset == 0
    assert document.segments[0].text == "第一行\n\n第二行\n"


def test_parse_json_flattens_string_values() -> None:
    document = parse_json('{"user":{"name":"张三","phone":"13800138000"},"tags":["a"]}')
    paths = [segment.path for segment in document.segments]
    assert "$.user.name" in paths and "$.user.phone" in paths and "$.tags[0]" in paths
    assert all(segment.text for segment in document.segments)


def test_parse_json_rejects_broken_input() -> None:
    with pytest.raises(ParseError):
        parse_json("{not json")


def test_parse_csv_maps_header_and_rows() -> None:
    document = parse_csv("name,phone\n李四,13900139000\n王五,\n")
    texts = [segment.text for segment in document.segments]
    assert "李四" in texts and "13900139000" in texts
    assert document.segments[0].path == "row1.name"
    assert document.meta["columns"] == ["name", "phone"]


def test_parse_csv_gbk_encoding() -> None:
    document = parse_bytes("data.csv", "姓名,电话\n张三,13800138000\n".encode("gbk"))
    assert document.content_type == "csv"
    assert any("张三" in segment.text for segment in document.segments)


def test_parse_sql_extracts_string_literals() -> None:
    document = parse_sql("INSERT INTO users (name, phone) VALUES ('张三', '13800138000');")
    texts = [segment.text for segment in document.segments]
    assert "张三" in texts and "13800138000" in texts
    assert document.meta["ddl"] == 0 and document.meta["dml"] == 1


def test_parse_bytes_unsupported_suffix() -> None:
    with pytest.raises(ParseUnsupported):
        parse_bytes("a.bin", b"\x00\x01")


def test_parse_docx_paragraphs_and_tables() -> None:
    body = (
        "<w:p><w:r><w:t>客户张三 13800138000</w:t></w:r></w:p>"
        "<w:tbl><w:tr>"
        "<w:tc><w:p><w:r><w:t>李四</w:t></w:r></w:p></w:tc>"
        "<w:tc><w:p><w:r><w:t>13900139000</w:t></w:r></w:p></w:tc>"
        "</w:tr></w:tbl>"
        "<w:p><w:r><w:t></w:t></w:r></w:p>"
    )
    document = parse_bytes("客户清单.docx", make_docx(body))
    assert document.content_type == "docx"
    # 空段落被跳过；表格按 table{表号}.{行}.{列} 定位
    assert [segment.path for segment in document.segments] == ["p1", "table1.1.1", "table1.1.2"]
    assert document.meta == {"paragraphs": 1, "tables": 1}
    assert "13800138000" in document.raw


def test_parse_docx_rejects_non_zip() -> None:
    with pytest.raises(ParseError):
        parse_docx(b"this is not a docx")


def test_parse_docx_requires_document_xml() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "hi")
    with pytest.raises(ParseError) as excinfo:
        parse_docx(buffer.getvalue())
    assert "document.xml" in str(excinfo.value)


def test_parse_docx_empty_document() -> None:
    with pytest.raises(ParseError):
        parse_docx(make_docx("<w:p><w:r><w:t>   </w:t></w:r></w:p>"))


def test_parse_pdf_without_engine_gives_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object):
        if name in {"pdfplumber", "pypdf"}:
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ParseUnsupported) as excinfo:
        parse_pdf(b"%PDF-1.4 fake")
    assert "pdfplumber" in str(excinfo.value)


def make_pdf(*lines: str) -> bytes:
    """手工构造一个最小可用 PDF（Helvetica，单页），用于验证 PDF 文本层抽取。"""
    content_lines = ["BT", "/F1 12 Tf", "20 60 Td"]
    for index, line in enumerate(lines):
        if index:
            content_lines.append("0 -16 Td")
        content_lines.append(f"({line}) Tj")
    content_lines.append("ET")
    stream = "\n".join(content_lines)
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 120] "
        "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode("latin-1")
    return bytes(out)


@pytest.mark.skipif(
    __import__("app.parsers.pdf_parser", fromlist=["pdf_engine"]).pdf_engine() is None,
    reason="PDF 引擎未安装（pip install pdfplumber）",
)
def test_parse_pdf_extracts_text_layer() -> None:
    document = parse_bytes("report.pdf", make_pdf("phone 13800138000", "id 11010519491231002X"))
    assert document.content_type == "pdf"
    text = " ".join(segment.text for segment in document.segments)
    assert "13800138000" in text


def test_supported_formats_includes_docx_and_pdf() -> None:
    formats = supported_formats()
    assert ".docx" in formats["supported"]
    assert ".pdf" in formats["supported"]
    assert formats["docx"] is True
    # PDF 是否可用取决于可选依赖，这里只校验字段存在
    assert "pdf" in formats


def test_parse_text_input_detects_json() -> None:
    assert parse_text_input('{"a":"13800138000"}').content_type == "json"
    assert parse_text_input("随便一段文本").content_type == "text"
