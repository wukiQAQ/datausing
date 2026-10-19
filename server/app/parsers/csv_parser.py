"""CSV 文件解析：每个单元格一个 Segment，路径为 ``row{行号}.{列名}``。

行号从 1 开始（数据行，表头不计），列名取表头；无表头时用 ``col{n}``。
"""

from __future__ import annotations

import csv
import io

from .base import Document, ParseError, Segment


def _read_csv_rows(text: str) -> list[list[str]]:
    # 与 Excel 导出的 CSV 兼容：优先 utf-8-sig（去 BOM），再尝试 GBK
    for encoding in ("utf-8-sig", "gbk"):
        try:
            text.encode(encoding).decode(encoding)
        except (UnicodeError, LookupError):
            continue
        try:
            return list(csv.reader(io.StringIO(text)))
        except csv.Error as exc:
            raise ParseError(f"CSV 解析失败：{exc}") from exc
    raise ParseError("CSV 编码无法识别（支持 UTF-8 / GBK）")


def parse_csv(text: str, source: str = "data.csv") -> Document:
    rows = [row for row in _read_csv_rows(text) if any(cell.strip() for cell in row)]
    if not rows:
        raise ParseError("CSV 内容为空")

    header, data_rows = rows[0], rows[1:]
    columns = [
        (header[index].strip() if index < len(header) and header[index].strip() else f"col{index + 1}")
        for index in range(max(len(row) for row in rows))
    ]

    segments: list[Segment] = []
    for row_index, row in enumerate(data_rows, start=1):
        for column_index, cell in enumerate(row):
            if not cell.strip():
                continue
            column = columns[column_index] if column_index < len(columns) else f"col{column_index + 1}"
            segments.append(Segment(path=f"row{row_index}.{column}", text=cell, hint=column))

    if not segments:
        raise ParseError("CSV 中没有可检测的单元格")
    return Document(
        source=source,
        content_type="csv",
        segments=segments,
        raw=text,
        meta={"rows": len(data_rows), "columns": columns},
    )
