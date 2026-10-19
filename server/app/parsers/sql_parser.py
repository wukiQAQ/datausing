"""SQL 文件解析：DDL 原样保留，只对 DML 里的字符串字面量做字段级脱敏。

做法：
1. 用一个能识别引号与注释的扫描器把文件拆成语句；
2. ``CREATE/ALTER/DROP/COMMENT/...`` 这类 DDL 语句整体跳过（表结构、约束不动）；
3. 其余 DML 语句中用正则抽出单引号字面量，作为待检测 Segment（带偏移，可回写）。

MVP 阶段粒度是"字面量级"而不是"列级"：INSERT 的 VALUES 与 UPDATE 的赋值都按
字面量检测；后续做列级策略时，可在 Segment.path 里补上 ``表名.列名``。
"""

from __future__ import annotations

import re

from .base import Document, ParseError, Segment

_DDL_KEYWORDS = (
    "create", "alter", "drop", "comment", "truncate", "grant", "revoke", "use", "set", "delimiter",
)
_LITERAL_RE = re.compile(r"'((?:[^']|'')*)'")
# 字面量前面的 ``列名 =``（UPDATE SET / WHERE）或 INSERT 的列清单，用来给检测层补字段名上下文
_COLUMN_BEFORE_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\S?\s*$")
_INSERT_RE = re.compile(
    r"insert\s+into\s+[A-Za-z_][\w.]*\s*\(([^)]*)\)\s*values\s*\(",
    re.IGNORECASE,
)


def _insert_columns(statement: str) -> list[str]:
    """提取 ``INSERT INTO t(a,b) VALUES(...)`` 的列名，供逐字面量对齐。"""
    match = _INSERT_RE.search(statement)
    if not match:
        return []
    return [part.strip() for part in match.group(1).split(",")]


def _hint_for(statement: str, literal_start: int, index: int, insert_columns: list[str]) -> str:
    """给一个字面量找字段名：INSERT 按列清单对齐，其余按 ``列名 =`` 回看。"""
    if insert_columns and index < len(insert_columns):
        return insert_columns[index]
    match = _COLUMN_BEFORE_RE.search(statement[:literal_start])
    return match.group(1) if match else ""


def split_statements(text: str) -> list[tuple[int, str]]:
    """按分号拆语句，返回 (起始偏移, 语句文本)；引号与注释内的分号不拆。"""
    statements: list[tuple[int, str]] = []
    start = 0
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in {"'", '"'}:
            quote = char
            index += 1
            while index < length:
                if text[index] == quote:
                    # 处理 '' 转义
                    if index + 1 < length and text[index + 1] == quote:
                        index += 2
                        continue
                    break
                index += 1
            index += 1
            continue
        if char == "-" and text.startswith("--", index):
            end = text.find("\n", index)
            index = length if end < 0 else end + 1
            continue
        if char == "/" and text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        if char == ";":
            statements.append((start, text[start:index]))
            start = index + 1
        index += 1
    statements.append((start, text[start:]))
    return [(offset, stmt) for offset, stmt in statements if stmt.strip()]


def parse_sql(text: str, source: str = "data.sql") -> Document:
    statements = split_statements(text)
    if not statements:
        raise ParseError("SQL 内容为空")

    segments: list[Segment] = []
    ddl_count = dml_count = 0
    for offset, statement in statements:
        head = statement.lstrip().split(None, 1)[0].lower() if statement.strip() else ""
        if head in _DDL_KEYWORDS:
            ddl_count += 1
            continue
        dml_count += 1
        insert_columns = _insert_columns(statement)
        for index, match in enumerate(_LITERAL_RE.finditer(statement)):
            value = match.group(1)
            if not value.strip():
                continue
            segments.append(
                Segment(
                    path=f"stmt{dml_count}.literal{len(segments) + 1}",
                    text=value,
                    offset=offset + match.start(1),
                    hint=_hint_for(statement, match.start(1), index, insert_columns),
                )
            )

    if not segments:
        raise ParseError("SQL 中没有可检测的 DML 字符串字面量（DDL 原样保留）")
    return Document(
        source=source,
        content_type="sql",
        segments=segments,
        raw=text,
        meta={"statements": len(statements), "ddl": ddl_count, "dml": dml_count},
    )
