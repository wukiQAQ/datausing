"""解析层测试：文本 / JSON / CSV / SQL / 上传分派 / 编码兼容。"""

from __future__ import annotations

import pytest

from app.parsers import (
    ParseError,
    ParseUnsupported,
    parse_bytes,
    parse_csv,
    parse_json,
    parse_sql,
    parse_text,
    parse_text_input,
)


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


def test_parse_bytes_pending_docx_gives_hint() -> None:
    with pytest.raises(ParseUnsupported) as excinfo:
        parse_bytes("a.docx", b"PK\x03\x04")
    assert "docx" in str(excinfo.value).lower()


def test_parse_text_input_detects_json() -> None:
    assert parse_text_input('{"a":"13800138000"}').content_type == "json"
    assert parse_text_input("随便一段文本").content_type == "text"
