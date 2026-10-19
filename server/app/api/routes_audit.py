"""审计溯源接口：分页查询、哈希链校验、导出。

演示要点：调一次 ``GET /api/v1/audit/verify`` 返回 ``ok=true``，
手动改一条库里的记录（或删一条）后同一个接口立刻返回 ``ok=false`` + 断链位置，
这就是"审计日志不可篡改"的可验证证明。
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from ..audit.chain import entry_hash_of, list_entries, verify_chain
from ..core.db import Database
from .deps import db_dep
from .schemas import AuditOut, AuditPage, VerifyOut

router = APIRouter(tags=["audit"])


@router.get("/audit", response_model=AuditPage, summary="审计记录（倒序分页）")
def list_audit(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Database = Depends(db_dep),
) -> AuditPage:
    rows, total = list_entries(db, limit=page_size, offset=(page - 1) * page_size)
    items = []
    for row in rows:
        payload = dict(row)
        payload["entry_hash"] = entry_hash_of(payload)
        items.append(AuditOut(**payload))
    return AuditPage(items=items, total=total, page=page, page_size=page_size)


@router.get("/audit/verify", response_model=VerifyOut, summary="校验哈希链完整性")
def verify(db: Database = Depends(db_dep)) -> VerifyOut:
    return VerifyOut(**verify_chain(db))


@router.get("/audit/export", summary="导出审计记录（jsonl/json/csv）")
def export_audit(
    fmt: str = Query(default="jsonl", alias="format"),
    db: Database = Depends(db_dep),
) -> Response:
    fmt = (fmt or "jsonl").lower()
    rows, total = list_entries(db, limit=200, offset=0)
    rows = list(reversed(rows))  # 导出按时间正序
    if fmt == "jsonl":
        body = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
        return Response(
            content=body.encode("utf-8"),
            media_type="application/x-ndjson; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="flowmask-audit.jsonl"'},
        )
    if fmt == "json":
        return Response(
            content=json.dumps({"total": total, "items": rows}, ensure_ascii=False, indent=2).encode("utf-8"),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="flowmask-audit.json"'},
        )
    if fmt == "csv":
        buffer = io.StringIO()
        columns: list[str] = [
            "id",
            "ts",
            "task_id",
            "input_hash",
            "output_hash",
            "model_chain",
            "confidence_vector",
            "rule_id",
            "operator_id",
            "user_id",
            "prev_hash",
            "signature",
        ]
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _cell(row.get(key)) for key in columns})
        return Response(
            content=("\ufeff" + buffer.getvalue()).encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="flowmask-audit.csv"'},
        )
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"不支持的格式: {fmt}，可选 jsonl / json / csv",
    )


def _cell(value: Any) -> Any:
    """CSV 里把 dict/list 压成 JSON 字符串，避免出现 "[object Object]" 之类。"""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value
