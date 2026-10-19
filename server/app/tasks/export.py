"""导出：审计日志（JSONL / JSON / CSV / SQL）与脱敏片段。

设计约束：**原文不落库**，库里只有 ``masked``（脱敏副本）与 ``original_hash``。
因此从库里"重建一份完整的脱敏文档"是做不到的——这是刻意的取舍（赛题要求
原始敏感值不入库）。需要完整文档时走两条路：

1. 同一请求内即时生成（``POST /api/v1/detect`` 直接返回脱敏文本，用于左右对照）；
2. 导出 findings 明细（本模块），每行自带脱敏片段 + 原文哈希，可审计、可回溯。
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from ..core.db import Database, utcnow_iso
from ..identify.pipeline import DocumentReport
from ..parsers.base import Document

EXPORT_FORMATS = ("jsonl", "json", "csv", "sql")

_CSV_COLUMNS = (
    "id",
    "path",
    "level",
    "score",
    "types",
    "hits",
    "masked",
    "original_hash",
    "model_chain",
    "rule_id",
    "degraded",
    "created_at",
)


def _rows(db: Database, task_id: str) -> list[dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM findings WHERE task_id=? ORDER BY id ASC",
            (task_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _meta(db: Database, task_id: str, total: int) -> dict[str, Any]:
    with db.connect() as conn:
        row = conn.execute("SELECT source, content_type FROM tasks WHERE id=?", (task_id,)).fetchone()
    return {
        "type": "meta",
        "task_id": task_id,
        "source": row["source"] if row else "",
        "content_type": row["content_type"] if row else "",
        "exported_at": utcnow_iso(),
        "operator": "api",
        "total": total,
        "schema": "flowmask.findings/v1",
    }


def _sql_literal(value: Any) -> str:
    """SQL 导出用：单引号转义，NULL 用 NULL。"""
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def export_findings(db: Database, task_id: str, fmt: str = "jsonl") -> tuple[bytes, str, str]:
    """返回 (内容, Content-Type, 建议文件名)。"""
    fmt = (fmt or "jsonl").lower()
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"不支持的导出格式: {fmt}，可选 {', '.join(EXPORT_FORMATS)}")
    rows = _rows(db, task_id)
    meta = _meta(db, task_id, len(rows))
    stamp = meta["exported_at"].replace(":", "").replace("-", "")

    if fmt == "jsonl":
        lines = [json.dumps(meta, ensure_ascii=False)]
        lines.extend(json.dumps(row, ensure_ascii=False) for row in rows)
        return ("\n".join(lines) + "\n").encode("utf-8"), "application/x-ndjson; charset=utf-8", f"flowmask-{task_id}-{stamp}.jsonl"

    if fmt == "json":
        payload = {"meta": meta, "findings": rows}
        return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"), "application/json; charset=utf-8", f"flowmask-{task_id}-{stamp}.json"

    if fmt == "csv":
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in _CSV_COLUMNS})
        # 加 UTF-8 BOM，避免 Excel 打开中文乱码
        return ("\ufeff" + buffer.getvalue()).encode("utf-8"), "text/csv; charset=utf-8", f"flowmask-{task_id}-{stamp}.csv"

    # fmt == "sql"：给出可直接 sqlite3 导入的 INSERT 语句
    statements = ["-- FlowMask 检测结果导出（脱敏副本，无原文）"]
    for row in rows:
        statements.append(
            "INSERT INTO findings (task_id, path, level, score, types, hits, masked, original_hash, "
            "model_chain, confidence_vector, rule_id, degraded, created_at) VALUES ("
            + ", ".join(
                _sql_literal(row.get(key))
                for key in (
                    "task_id",
                    "path",
                    "level",
                    "score",
                    "types",
                    "hits",
                    "masked",
                    "original_hash",
                    "model_chain",
                    "confidence_vector",
                    "rule_id",
                    "degraded",
                    "created_at",
                )
            )
            + ");"
        )
    return ("\n".join(statements) + "\n").encode("utf-8"), "application/sql; charset=utf-8", f"flowmask-{task_id}-{stamp}.sql"


def masked_document(document: Document, report: DocumentReport) -> dict[str, Any]:
    """即时生成"脱敏后文档"的表示（用于左右对照与下载）。

    - 文本类：把每段的脱敏结果按原顺序拼回去（同一请求内，内存里就有原文）；
    - 结构化类（json/csv/sql）：返回逐路径的脱敏片段，前端按路径渲染对照表。
    """
    if document.content_type == "text":
        return {
            "content_type": "text",
            "masked": "\n".join(f.masked for f in report.findings),
            "segments": [{"path": f.path, "masked": f.masked} for f in report.findings],
        }
    return {
        "content_type": document.content_type,
        "segments": [{"path": f.path, "level": f.level.value, "masked": f.masked} for f in report.findings],
    }
