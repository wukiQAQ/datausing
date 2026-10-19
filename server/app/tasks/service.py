"""任务编排：上传/粘贴 → 解析 → 三级检测 → 落库 → 审计链。

一个任务 = 一次"文档级"检测。解析与检测可能耗时（Word/PDF、L2/L3 推理），
因此接口层只负责建任务并立刻返回 ``task_id``，真正的执行放在后台线程，
前端轮询 ``GET /api/v1/tasks/{id}`` 看状态。单进程一体机用线程足够，
不引 Celery/Redis 之类的重依赖（评委也能一眼看懂链路）。
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from typing import Any

from ..audit import append_entry
from ..core.db import Database, utcnow_iso
from ..identify import DetectionPipeline, DetectOptions, Level
from ..parsers.base import Document

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

LEVELS = [level.value for level in Level]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _row_to_finding(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["degraded"] = bool(item["degraded"])
    try:
        item["types"] = [t for t in str(item["types"]).split(",") if t]
    except Exception:  # 理论上不会发生，容错避免接口 500
        item["types"] = []
    if item.get("confidence_vector"):
        try:
            item["confidence_vector"] = json.loads(item["confidence_vector"])
        except json.JSONDecodeError:
            item["confidence_vector"] = []
    else:
        item["confidence_vector"] = []
    return item


def create_task(
    db: Database,
    *,
    source: str,
    content_type: str,
    document: Document,
    options: DetectOptions,
    pipeline: DetectionPipeline | None = None,
) -> str:
    """建任务并异步执行，立刻返回 task_id。"""
    task_id = uuid.uuid4().hex[:16]
    options_json = json.dumps(
        {
            "enable_l2": options.enable_l2,
            "enable_l3": options.enable_l3,
            "mask_level": options.mask_level.value if options.mask_level else None,
            "min_level": options.min_level.value,
        },
        ensure_ascii=False,
    )
    with db.write() as conn:
        conn.execute(
            """
            INSERT INTO tasks (id, created_at, status, source, content_type, options)
            VALUES (?,?,?,?,?,?)
            """,
            (task_id, utcnow_iso(), STATUS_PENDING, source, content_type, options_json),
        )

    runner = pipeline or DetectionPipeline.from_settings(_settings())
    thread = threading.Thread(
        target=_execute,
        args=(db, task_id, document, options, runner),
        name=f"flowmask-task-{task_id}",
        daemon=True,
    )
    thread.start()
    return task_id


def _settings():
    from ..core.config import get_settings

    return get_settings()


def _execute(
    db: Database,
    task_id: str,
    document: Document,
    options: DetectOptions,
    pipeline: DetectionPipeline,
) -> None:
    """后台执行体：任何异常都要落成 failed 状态，不让任务永远卡在 running。"""
    try:
        with db.write() as conn:
            conn.execute(
                "UPDATE tasks SET status=? WHERE id=?",
                (STATUS_RUNNING, task_id),
            )
        report = pipeline.analyze_document(document, options)

        with db.write() as conn:
            for finding in report.findings:
                conn.execute(
                    """
                    INSERT INTO findings
                      (task_id, path, level, score, types, hits, masked, original_hash,
                       model_chain, confidence_vector, rule_id, degraded, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        task_id,
                        finding.path,
                        finding.level.value,
                        finding.score,
                        ",".join(finding.types),
                        finding.hits,
                        finding.masked,
                        finding.original_hash,
                        finding.model_chain,
                        json.dumps(finding.confidence_vector, ensure_ascii=False),
                        ",".join(finding.rule_ids),
                        1 if finding.degraded else 0,
                        utcnow_iso(),
                    ),
                )
            conn.execute(
                "UPDATE tasks SET status=?, finished_at=?, total_segments=?, total_findings=? WHERE id=?",
                (
                    STATUS_DONE,
                    utcnow_iso(),
                    report.segments,
                    len(report.findings),
                    task_id,
                ),
            )

        # 审计链：一条 finding 一条记录（只存哈希，不存原文）
        for finding in report.findings:
            append_entry(
                db,
                task_id=task_id,
                input_hash=finding.original_hash,
                output_hash=_hash(finding.masked),
                model_chain=finding.model_chain,
                confidence_vector=json.dumps(finding.confidence_vector, ensure_ascii=False),
                rule_id=",".join(finding.rule_ids),
            )
    except Exception as exc:  # noqa: BLE001 - 后台任务必须兜住所有异常
        with db.write() as conn:
            conn.execute(
                "UPDATE tasks SET status=?, finished_at=?, error=? WHERE id=?",
                (STATUS_FAILED, utcnow_iso(), f"{type(exc).__name__}: {exc}", task_id),
            )


def get_task(db: Database, task_id: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if row is None:
        return None
    task = dict(row)
    try:
        task["options"] = json.loads(task["options"])
    except json.JSONDecodeError:
        task["options"] = {}
    return task


def list_tasks(db: Database, limit: int = 20, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    with db.connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    items = []
    for row in rows:
        task = dict(row)
        try:
            task["options"] = json.loads(task["options"])
        except json.JSONDecodeError:
            task["options"] = {}
        items.append(task)
    return items, int(total)


def page_findings(
    db: Database,
    *,
    task_id: str | None = None,
    level: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 100))
    where: list[str] = []
    params: list[Any] = []
    if task_id:
        where.append("task_id=?")
        params.append(task_id)
    if level:
        where.append("level=?")
        params.append(level.upper())
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with db.connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM findings {clause}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM findings {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    return [_row_to_finding(row) for row in rows], int(total)


def get_finding(db: Database, finding_id: int) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM findings WHERE id=?", (finding_id,)).fetchone()
    return None if row is None else _row_to_finding(row)


def stats(db: Database, task_id: str | None = None) -> dict[str, Any]:
    """看板数据：分级分布、类别 TopN、分数直方图、任务计数。"""
    clause = "WHERE task_id=?" if task_id else ""
    params: tuple[Any, ...] = (task_id,) if task_id else ()
    by_level = {level: 0 for level in LEVELS}
    by_type: dict[str, int] = {}
    buckets = {"0-0.25": 0, "0.25-0.5": 0, "0.5-0.75": 0, "0.75-1.0": 0}

    with db.connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM findings {clause}", params).fetchone()[0]
        for row in conn.execute(f"SELECT level, COUNT(*) AS n FROM findings {clause} GROUP BY level", params):
            by_level[row["level"]] = int(row["n"])
        for row in conn.execute(f"SELECT types FROM findings {clause}", params):
            for typ in str(row["types"]).split(","):
                if typ:
                    by_type[typ] = by_type.get(typ, 0) + 1
        for row in conn.execute(f"SELECT score FROM findings {clause}", params):
            value = float(row["score"])
            if value < 0.25:
                buckets["0-0.25"] += 1
            elif value < 0.5:
                buckets["0.25-0.5"] += 1
            elif value < 0.75:
                buckets["0.5-0.75"] += 1
            else:
                buckets["0.75-1.0"] += 1
        task_total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]

    top_types = sorted(by_type.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    return {
        "total_findings": int(total),
        "tasks": int(task_total),
        "by_level": by_level,
        "by_type": [{"type": key, "count": count} for key, count in top_types],
        "score_histogram": buckets,
        "task_id": task_id,
    }
