"""脱敏文件导出：把"同一请求内存里的原文"按策略重建成可下载文件。

设计约束与 ``tasks/export.py`` 一致：**原文不落库**，直接导出在一次请求内生成，快照导出复用有界、短寿命内存中的检测证据
和替换结果（``POST /api/v1/detect/export`` / ``POST /api/v1/detect/file/export``）。
结构化格式无法逐字节保留原排版（JSON 会重新缩进、CSV 会重新转义、DOCX 只重建
"段落级"文本），属于"尽力重建、格式可能规范化"：内容（字段、行、语句、段落）在，
样式会丢。PDF 无法可靠重建，因此只支持导出为 txt。

``build_masked_bytes`` 只依赖标准库（csv/json/zipfile），不引入新依赖。
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from xml.sax.saxutils import escape

from ..identify.masking import (
    mask_spans_with_strategy,
    mask_value_strategy,
    normalize_strategy,
)
from ..identify.pipeline import DocumentReport
from ..parsers.base import Document
from ..security import SecurityError, protected_spans

#: 允许显式指定的输出格式
EXPORT_FORMATS = ("txt", "json", "csv", "sql", "docx", "xlsx")

#: ``fmt="auto"`` 时按文档形态选默认输出格式
DEFAULT_FORMATS: dict[str, str] = {
    "text": "txt",
    "json": "json",
    "csv": "csv",
    "sql": "sql",
    "docx": "docx",
    "xlsx": "xlsx",
    "pdf": "txt",  # PDF 只能导出文本
}

#: 输出格式 → (Content-Type, 扩展名)
_MEDIA_TYPES: dict[str, tuple[str, str]] = {
    "txt": ("text/plain;charset=utf-8", "txt"),
    "json": ("application/json;charset=utf-8", "json"),
    "csv": ("text/csv;charset=utf-8", "csv"),
    "sql": ("application/sql;charset=utf-8", "sql"),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
}


def _resolve_fmt(document: Document, fmt: str) -> str:
    """归一化输出格式：``auto`` 跟随文档形态；显式值必须在白名单内。"""
    key = (fmt or "auto").strip().lower()
    if key == "auto":
        key = DEFAULT_FORMATS.get(document.content_type, "txt")
    if key not in EXPORT_FORMATS:
        raise ValueError("不支持的输出格式")
    if document.content_type == "pdf" and key != "txt":
        raise ValueError("PDF 仅支持导出 txt")
    if document.content_type == "xlsx" and key not in ("xlsx", "txt"):
        raise ValueError("Excel 仅支持导出 xlsx 或 txt")
    if key in ('json', 'csv', 'sql', 'xlsx') and document.content_type != key:
        raise SecurityError('EXPORT_FORMAT_UNSUPPORTED', '不支持该输入与输出格式组合')
    return key


def _masked_texts(document: Document, report: DocumentReport, strategy: str, secret: str) -> dict[str, str]:
    """逐 segment 生成掩码文本，按 path 索引。

    报告里的 ``all_findings`` 是 ``document.segments`` 的子序列（没命中的段不出现），
    因此不能按下标对齐，必须按 path 回填；找不到报告的段原样保留。``spans`` 是相对
    segment 文本的偏移，交给 :func:`mask_spans_with_strategy` 从后往前替换。
    """
    findings = report.all_findings or report.findings
    paths = [segment.path for segment in document.segments]
    if (len(set(paths)) != len(paths)
            or len({f.path for f in findings}) != len(findings)
            or any(f.path not in paths for f in findings)):
        raise SecurityError('MASK_LOCATION_INVALID', '脱敏片段定位不一致，请重新检测')
    by_path = {finding.path: finding for finding in findings}
    masked: dict[str, str] = {}
    for segment in document.segments:
        finding = by_path.get(segment.path)
        if finding is None:
            masked[segment.path] = segment.text
        elif finding.spans and not finding.requires_full_mask:
            spans = protected_spans(segment.text, finding.spans)
            effective = 'block' if strategy == 'generalize' and any(
                len(span.get('coverage_types', [])) > 1 for span in spans) else strategy
            masked[segment.path] = mask_spans_with_strategy(segment.text, spans, effective, secret)
        else:
            # 非位置型证据不能退回原文或低危警示副本，保守处理整个片段。
            masked[segment.path] = mask_value_strategy(segment.text, None, strategy, secret)
    return masked


# ---- txt -------------------------------------------------------------------


def _build_txt(document: Document, _report: DocumentReport, _strategy: str, _secret: str, masked: dict[str, str]) -> bytes:
    """纯文本：每个 segment 一行，按文档顺序拼接。"""
    return "\n".join(masked.get(segment.path, segment.text) for segment in document.segments).encode("utf-8")


# ---- json ------------------------------------------------------------------


def _json_path_tokens(path: str) -> list[str | int] | None:
    """把 ``$.a.b[0]`` 拆成 ``["a", "b", 0]``；含点号的键名无法区分，返回 None。"""
    if not path.startswith("$"):
        return None
    tokens: list[str | int] = []
    index = 1
    length = len(path)
    while index < length:
        char = path[index]
        if char == ".":
            index += 1
            end = index
            while end < length and path[end] not in ".[":
                end += 1
            if end == index:
                return None
            tokens.append(path[index:end])
            index = end
        elif char == "[":
            end = path.find("]", index)
            if end < 0:
                return None
            try:
                tokens.append(int(path[index + 1 : end]))
            except ValueError:
                return None
            index = end + 1
        else:
            return None
    return tokens or None


def _set_json_value(root: object, path: str, value: str) -> bool:
    """按简化 JSONPath 把 ``value`` 写回已解析的 JSON 树；定位失败返回 False。"""
    tokens = _json_path_tokens(path)
    if not tokens:
        return False
    node = root
    for token in tokens[:-1]:
        try:
            node = node[token]  # type: ignore[index]
        except (KeyError, IndexError, TypeError):
            return False
    last = tokens[-1]
    try:
        if isinstance(node, dict) and isinstance(last, str):
            node[last] = value
            return True
        if isinstance(node, list) and isinstance(last, int):
            node[last] = value
            return True
    except (KeyError, IndexError, TypeError):
        return False
    return False


def _build_json(document: Document, _report: DocumentReport, _strategy: str, _secret: str, masked: dict[str, str]) -> bytes:
    """JSON：解析原文，按 finding.path 把敏感字段替换成掩码值后重新序列化。

    重新 ``json.dumps(indent=2)`` 会规范化空白与键顺序（内容不变）；特殊键名
    按类型化位置回写，定位失败报错；未脱敏数值保持原始类型。
    """
    try:
        data = json.loads(document.raw)
    except ValueError as exc:
        raise ValueError("JSON 原文无法解析") from exc
    for segment in document.segments:
        value = masked[segment.path]
        if value == segment.text:
            continue  # 未替换的数值保留原始JSON类型
        if segment.location is not None:
            try:
                if not segment.location:
                    data = value
                else:
                    node = data
                    for token in segment.location[:-1]:
                        node = node[token]
                    token = segment.location[-1]
                    if str(node[token]) != segment.text:
                        raise KeyError('source mismatch')
                    node[token] = value
            except (KeyError, IndexError, TypeError):
                raise SecurityError('MASK_LOCATION_INVALID', 'JSON脱敏回写失败') from None
        elif segment.path == '$' and isinstance(data, str):
            data = value
        elif not _set_json_value(data, segment.path, value):
            raise SecurityError('MASK_LOCATION_INVALID', 'JSON脱敏回写失败')
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


# ---- csv -------------------------------------------------------------------


def _parse_csv_path(path: str) -> tuple[int, str] | None:
    """``row2.手机号`` → ``(2, "手机号")``；不合法返回 None。"""
    if not path.startswith("row"):
        return None
    rest = path[3:]
    dot = rest.find(".")
    if dot <= 0:
        return None
    try:
        row_index = int(rest[:dot])
    except ValueError:
        return None
    return (row_index, rest[dot + 1:]) if row_index > 0 else None


def _build_csv(document: Document, _report: DocumentReport, _strategy: str, _secret: str, masked: dict[str, str]) -> bytes:
    """CSV：重新读原文，按段落的行列元数据替换单元格，再由 csv 模块负责转义。"""
    rows = [row for row in csv.reader(io.StringIO(document.raw)) if any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError("CSV 原文为空")
    columns = list(document.meta.get("columns") or [])  # type: ignore[arg-type]
    column_index = {str(name): index for index, name in enumerate(columns)}
    for segment in document.segments:
        if segment.location is not None:
            row_index, column_position = segment.location
        else:
            parsed = _parse_csv_path(segment.path)
            if parsed is None or len(column_index) != len(columns):
                raise SecurityError('MASK_LOCATION_INVALID', 'CSV脱敏回写失败')
            row_index, column = parsed
            column_position = column_index.get(column)
        if (type(row_index) is not int or type(column_position) is not int
                or not 0 <= row_index < len(rows)
                or not 0 <= column_position < len(rows[row_index])
                or rows[row_index][column_position] != segment.text):
            raise SecurityError('MASK_LOCATION_INVALID', 'CSV脱敏回写失败')
        rows[row_index][column_position] = masked[segment.path]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


# ---- sql -------------------------------------------------------------------


def _build_sql(document: Document, report: DocumentReport, strategy: str, secret: str, masked: dict[str, str]) -> bytes:
    """SQL：用 spans 的绝对偏移从后往前 splice，注释与多条语句原样保留。

    SQL 字面量有真实 ``offset``（见 ``parsers/sql_parser.py``），所以这里能逐字节
    保留 DDL / 注释 / 语句结构，只替换字面量内容。
    """
    raw = document.raw
    replacements = []
    for segment in document.segments:
        value = masked[segment.path]
        if value == segment.text:
            continue
        start = segment.offset
        end = start + len(segment.text)
        if start < 0 or end > len(raw) or raw[start:end] != segment.text:
            raise SecurityError('MASK_LOCATION_INVALID', 'SQL脱敏回写失败')
        # 输入字面量保留SQL的双引号转义，替换后统一规范化，避免策略文本破坏SQL。
        value = value.replace("''", "'").replace("'", "''")
        replacements.append((start, end, value))
    parts = []
    cursor = len(raw)
    for start, end, replacement in sorted(replacements, reverse=True):
        if end > cursor:
            raise SecurityError('MASK_LOCATION_INVALID', 'SQL脱敏范围冲突')
        parts.extend((raw[end:cursor], replacement))
        cursor = start
    parts.append(raw[:cursor])
    return ''.join(reversed(parts)).encode('utf-8')


# ---- docx ------------------------------------------------------------------

_DOCX_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_CONTENT_TYPES_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELS_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    "</Relationships>"
)


def _docx_document_xml(paragraphs: list[str]) -> str:
    """把段落列表拼成最小可用的 ``word/document.xml``（每个掩码段一个 ``w:p``）。"""
    body = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{escape(paragraph)}</w:t></w:r></w:p>'
        for paragraph in paragraphs
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:document xmlns:w="{_DOCX_NAMESPACE}"><w:body>{body}</w:body></w:document>'
    )


def _build_docx(document: Document, _report: DocumentReport, _strategy: str, _secret: str, masked: dict[str, str]) -> bytes:
    """DOCX：零依赖手写 zip（Content_Types / rels / document.xml），可被本仓解析器反解。

    只重建段落级文本：原文档的样式、表格结构、图片会丢失（"尽力重建"）。
    """
    paragraphs = [masked.get(segment.path, segment.text) for segment in document.segments]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
        archive.writestr("_rels/.rels", _RELS_XML)
        archive.writestr("word/document.xml", _docx_document_xml(paragraphs))
    return buffer.getvalue()


# ---- xlsx ------------------------------------------------------------------

_XLSX_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_XLSX_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_XLSX_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_XLSX_NUMERIC = re.compile(r"^-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")

_XLSX_ROOT_RELS_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    f'<Relationships xmlns="{_XLSX_PKG_REL_NS}">'
    f'<Relationship Id="rId1" Type="{_XLSX_REL_NS}/officeDocument" Target="xl/workbook.xml"/>'
    "</Relationships>"
)


def _xlsx_column_letters(position: int) -> str:
    """列序号 → 列字母（1 → A、27 → AA）。"""
    letters = ""
    while position > 0:
        position, remainder = divmod(position - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _xlsx_content_types(sheet_count: int) -> str:
    """``[Content_Types].xml``：工作簿主部件 + 每张工作表各一条 Override。"""
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        + overrides
        + "</Types>"
    )


def _xlsx_workbook_xml(names: list[str]) -> str:
    """``xl/workbook.xml``：按原顺序保留工作表名，关系 id 与 rels 文件一一对应。"""
    sheets = "".join(
        f'<sheet name="{escape(name, {chr(34): "&quot;"})}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(names, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<workbook xmlns="{_XLSX_NS}" xmlns:r="{_XLSX_REL_NS}"><sheets>{sheets}</sheets></workbook>'
    )


def _xlsx_workbook_rels(sheet_count: int) -> str:
    """``xl/_rels/workbook.xml.rels``：关系 id → 工作表文件。"""
    rels = "".join(
        f'<Relationship Id="rId{index}" Type="{_XLSX_REL_NS}/worksheet" '
        f'Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<Relationships xmlns="{_XLSX_PKG_REL_NS}">{rels}</Relationships>'
    )


def _xlsx_cell_xml(reference: str, kind: str, value: str) -> str:
    """单个单元格：原数值格仍写数字，其余（文本、被掩码的数值、布尔/日期）写内联文本。"""
    if kind == "n" and _XLSX_NUMERIC.match(value):
        return f'<c r="{reference}"><v>{value}</v></c>'
    return f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>'


def _xlsx_sheet_xml(
    rows: list[list[tuple[str, str, object]]],
    segments: list,
    masked: dict[str, str],
) -> str:
    """一张工作表的 ``sheetData``：按解析时记录的行列结构逐格重建。"""
    rows_xml: list[str] = []
    for row_number, cells in rows:
        cells_xml: list[str] = []
        for position, (reference, kind, payload) in enumerate(cells, start=1):
            if isinstance(payload, int):
                segment = segments[payload]
                value = masked.get(segment.path, segment.text)
            else:
                value = str(payload)
            cells_xml.append(
                _xlsx_cell_xml(reference or f"{_xlsx_column_letters(position)}{row_number}", kind, value)
            )
        rows_xml.append(f'<row r="{row_number}">' + "".join(cells_xml) + "</row>")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<worksheet xmlns="{_XLSX_NS}"><sheetData>' + "".join(rows_xml) + "</sheetData></worksheet>"
    )


def _build_xlsx(document: Document, _report: DocumentReport, _strategy: str, _secret: str, masked: dict[str, str]) -> bytes:
    """XLSX：零依赖手写 zip（Content_Types / rels / workbook / worksheets），可被本仓解析器反解。

    只重建单元格文本：样式、公式、合并单元格、隐藏行列不保留（"尽力重建"）。
    """
    meta = document.meta.get("xlsx") or {}
    sheets = list(meta.get("sheets") or [])  # type: ignore[union-attr]
    if not sheets:
        raise ValueError("Excel 原文结构不可用")
    names = [str(sheet.get("name", "")) for sheet in sheets]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _xlsx_content_types(len(sheets)))
        archive.writestr("_rels/.rels", _XLSX_ROOT_RELS_XML)
        archive.writestr("xl/workbook.xml", _xlsx_workbook_xml(names))
        archive.writestr("xl/_rels/workbook.xml.rels", _xlsx_workbook_rels(len(sheets)))
        for index, sheet in enumerate(sheets, start=1):
            archive.writestr(
                f"xl/worksheets/sheet{index}.xml",
                _xlsx_sheet_xml(list(sheet.get("rows") or []), document.segments, masked),  # type: ignore[union-attr]
            )
    return buffer.getvalue()


_BUILDERS = {
    "txt": _build_txt,
    "json": _build_json,
    "csv": _build_csv,
    "sql": _build_sql,
    "docx": _build_docx,
    "xlsx": _build_xlsx,
}


def build_masked_bytes(
    document: Document,
    report: DocumentReport,
    *,
    strategy: str,
    fmt: str,
    secret: str,
    masked_values: dict[str, str] | None = None,
) -> tuple[bytes, str, str]:
    """按策略把文档重建成脱敏文件，返回 ``(内容, Content-Type, 扩展名)``。

    - ``strategy``：六种策略之一（未知值回退 ``partial``；``encrypt`` 需要非空密钥）；
    - ``fmt``：``auto`` 跟随文档形态，或显式 ``txt/json/csv/sql/docx/xlsx``；
    - ``secret``：hash / pseudonym 的盐与 encrypt 的密钥（来自 ``settings.export_secret``）。

    只替换 ``all_findings`` 里的命中片段（不受 ``min_level`` 影响），保证导出内容
    与用户看到的级别筛选无关、不漏脱敏。
    """
    key = normalize_strategy(strategy)
    if key == "encrypt" and not secret:
        raise ValueError("脱敏导出密钥未配置")
    resolved = _resolve_fmt(document, fmt)
    try:
        masked = _masked_texts(document, report, key, secret) if masked_values is None else masked_values
        if set(masked) != {segment.path for segment in document.segments}:
            raise SecurityError('MASK_LOCATION_INVALID', '快照脱敏片段不完整')
        content = _BUILDERS[resolved](document, report, key, secret, masked)
    except SecurityError:
        raise
    except Exception:
        raise SecurityError('MASKING_FAILED', '脱敏文件生成失败，未返回原文') from None
    media_type, ext = _MEDIA_TYPES[resolved]
    return content, media_type, ext
