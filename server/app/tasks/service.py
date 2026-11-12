"""任务编排：上传/粘贴 → 解析 → 三级检测 → 落库 → 审计链。

一个任务 = 一次"文档级"检测。解析与检测可能耗时（Word/PDF、L2/L3 推理），
因此接口层只负责建任务并立刻返回 ``task_id``，真正的执行放在后台线程，
前端轮询 ``GET /api/v1/tasks/{id}`` 看状态。单进程一体机用线程足够，
不引 Celery/Redis 之类的重依赖（评委也能一眼看懂链路）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from typing import Any

from ..alerts import insert_alerts
from ..audit import append_entry_conn
from ..core.db import Database, utcnow_iso
from ..filters import (
    CONTENT_TYPE_LABELS,
    LEVEL_LABELS,
    LEVEL_ORDER,
    TREND_DAYS,
    Filter,
    content_type_label,
    day_modifier,
    parse_filter,
    trend_start_day,
    type_label,
)
from ..identify import ENTITY_LABELS, DetectionPipeline, DetectOptions, Level
from ..parsers.base import Document
from ..security import POLICY_VERSION, SecurityError, protected_text, safe_stored_mask

logger = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

LEVELS = list(LEVEL_ORDER)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _row_to_finding(row: Any) -> dict[str, Any]:
    item = dict(row)
    item['masked'] = safe_stored_mask(item['masked'])
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
    # 关联 tasks 得到的展示字段：查单条/列表都可能没 JOIN 到任务（历史脏数据），缺失给空串
    item["source"] = item.pop("task_source", None) or ""
    item["content_type"] = item.pop("task_content_type", None) or ""
    # 按 types 原顺序给中文名，前端无需自己维护映射表
    item["type_labels"] = [type_label(key) for key in item["types"]]
    return item


def create_task(
    db: Database,
    *,
    source: str,
    content_type: str,
    document: Document,
    options: DetectOptions,
    pipeline: DetectionPipeline | None = None,
    comparison_owner: str = "",
    comparison_secret: str = "",
) -> str:
    """建任务并异步执行，立刻返回 task_id。"""
    task_id = uuid.uuid4().hex[:16]
    options_json = json.dumps(
        {
            "enable_l2": options.enable_l2,
            "enable_l3": options.enable_l3,
            "mask_level": options.mask_level.value if options.mask_level else None,
            "min_level": options.min_level.value,
            "storage_policy_version": POLICY_VERSION,
            "storage_mask_floor": "L2",
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
        args=(db, task_id, document, options, runner, comparison_owner, comparison_secret),
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
    comparison_owner: str = "",
    comparison_secret: str = "",
) -> None:
    """后台执行体：任何异常都要落成 failed 状态，不让任务永远卡在 running。"""
    failure_code = 'TASK_PERSISTENCE_FAILED'
    try:
        with db.write() as conn:
            conn.execute(
                "UPDATE tasks SET status=? WHERE id=?",
                (STATUS_RUNNING, task_id),
            )
        failure_code = 'TASK_DETECTION_FAILED'
        report = pipeline.analyze_document(document, options)

        # L1是本机预览语义，不能作为持久化的隐私保证。存储边界重新生成安全副本。
        segments = {segment.path: segment for segment in document.segments}
        if len(segments) != len(document.segments):
            raise SecurityError('MASK_LOCATION_INVALID', '任务片段定位不一致')
        for finding in report.all_findings or report.findings:
            segment = segments.get(finding.path)
            if segment is None or _hash(segment.text) != finding.original_hash:
                raise SecurityError('MASK_LOCATION_INVALID', '任务结果与来源不一致')
            finding.masked = protected_text(segment.text, finding, options.mask_level)

        if comparison_owner:
            from ..identify.snapshots import get_snapshot_store
            try:
                get_snapshot_store().put(document, report,
                    {'mask_level': options.mask_level.value if options.mask_level else None},
                    _hash(document.raw), POLICY_VERSION, comparison_owner, comparison_secret,
                    result_id='task:' + task_id)
            except Exception:  # noqa: BLE001 - 快照只是对比用的短时缓存，失败不能影响既有任务
                # 只记录稳定错误码与关联号，不写文档内容/异常文本，避免原文进日志。
                logger.warning('SNAPSHOT_WRITE_FAILED task_id=%s error_id=%s',
                               task_id, uuid.uuid4().hex)
        failure_code = 'TASK_PERSISTENCE_FAILED'
        with db.write() as conn:
            inserted: list[tuple[int, Any]] = []
            for finding in report.findings:
                cursor = conn.execute(
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
                inserted.append((int(cursor.lastrowid or 0), finding))
            # 告警与 findings 同事务落库：保证「有检测结果就有对应告警」，
            # 失败时整体回滚，不出现"任务完成但队列里没告警"的半状态。
            insert_alerts(conn, task_id=task_id, findings=inserted)
            # 审计链与 findings/告警同事务：任一条写入失败整体回滚，
            # 不会出现「findings 已提交、任务却因审计失败被标记 failed」的半状态。
            for finding in report.findings:
                append_entry_conn(
                    conn,
                    task_id=task_id,
                    input_hash=finding.original_hash,
                    output_hash=_hash(finding.masked),
                    model_chain=finding.model_chain,
                    confidence_vector=json.dumps(finding.confidence_vector, ensure_ascii=False),
                    rule_id=",".join(finding.rule_ids),
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

        # 审计链写入已并入上面的同一事务（见 append_entry_conn 调用），
        # 不再在事务外补写，避免「findings 已提交、审计失败后任务被改 failed」。
    except Exception as exc:  # noqa: BLE001 - 后台任务必须兜住所有异常
        error = exc if isinstance(exc, SecurityError) else SecurityError(
            failure_code, '任务执行失败，未保存原文', 503)
        logger.warning('%s task_id=%s error_id=%s', error.code, task_id, error.error_id)
        try:
            with db.write() as conn:
                conn.execute(
                    "UPDATE tasks SET status=?, finished_at=?, error=? WHERE id=?",
                    (STATUS_FAILED, utcnow_iso(), f'{error.code}:{error.error_id}', task_id),
                )
        except Exception:  # noqa: BLE001 - 兜底写库失败也不能让异常穿出 daemon 线程
            logger.error('TASK_STATUS_WRITE_FAILED task_id=%s error_id=%s', task_id, error.error_id)


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
    types: str | None = None,
    content_type: str | None = None,
    start: str | None = None,
    end: str | None = None,
    days: int | None = None,
    tz_offset: int = 0,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    """分页查检测结果；筛选口径与 /stats 完全一致（同一个 parse_filter）。"""
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 100))
    flt = parse_filter(
        task_id=task_id,
        level=level,
        types=types,
        content_type=content_type,
        start=start,
        end=end,
        days=days,
        tz_offset=tz_offset,
    )
    clause, params = flt.where_sql()
    base = f"FROM findings f LEFT JOIN tasks t ON t.id=f.task_id {clause}"
    with db.connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT f.*, t.source AS task_source, t.content_type AS task_content_type {base} "
            f"ORDER BY f.id DESC LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    return [_row_to_finding(row) for row in rows], int(total)


def get_finding(db: Database, finding_id: int) -> dict[str, Any] | None:
    """单条详情；顺带带出任务来源与内容形态（缺失时由 _row_to_finding 补空串）。"""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT f.*, t.source AS task_source, t.content_type AS task_content_type "
            "FROM findings f LEFT JOIN tasks t ON t.id=f.task_id WHERE f.id=?",
            (finding_id,),
        ).fetchone()
    return None if row is None else _row_to_finding(row)


def _findings_base(clause: str) -> str:
    """findings + tasks 的统一 FROM 片段（content_type 筛选依赖 tasks）。"""
    return f"FROM findings f LEFT JOIN tasks t ON t.id=f.task_id {clause}"


def _trend_suffix(*, has_where: bool, tz_offset: int, trend_limited: bool) -> tuple[str, list[Any]]:
    """无显式时间筛选时，看板趋势只统计最近 90 个当地日（KPI 仍全量）。"""
    if not trend_limited:
        return "", []
    prefix = " AND " if has_where else " WHERE "
    expr = f"{prefix}date(f.created_at, ?)>=?"
    return expr, [day_modifier(tz_offset), trend_start_day(tz_offset, days=TREND_DAYS)]


def _day_rows(
    conn,
    *,
    base: str,
    params: list[Any],
    tz_offset: int,
    trend_limited: bool,
    key_name: str,
    group_by: str,
    has_where: bool,
) -> dict[str, dict[str, int]]:
    """按当地日 + 额外维度分组计数；返回 {日期: {维度值: 计数}}。

    日期由 SQLite ``date(created_at, 'N minutes')`` 切出，避免 Python 侧再转时区。
    ``key_name`` 只用于读取结果列名，``group_by`` 是真正的分组表达式；
    ``base`` 是调用方拼好的 ``FROM ... [WHERE ...]`` 片段，findings 与 alerts 各自传入。
    """
    suffix, suffix_params = _trend_suffix(
        has_where=has_where, tz_offset=tz_offset, trend_limited=trend_limited
    )
    sql = (
        f"SELECT date(f.created_at, ?) AS day, {key_name} AS key, COUNT(*) AS n "
        f"{base}{suffix} "
        f"GROUP BY day, {group_by} ORDER BY day"
    )
    # 占位符按 SQL 文本顺序：SELECT 的时区修饰符 → WHERE 筛选参数 → 趋势窗口修饰符与起始日
    query_params = [day_modifier(tz_offset), *params, *suffix_params]
    grouped: dict[str, dict[str, int]] = {}
    for row in conn.execute(sql, query_params):
        day = row["day"]
        if not day:
            continue
        entry = grouped.setdefault(day, {})
        key = str(row["key"])
        entry[key] = entry.get(key, 0) + int(row["n"])
    return grouped


def stats(
    db: Database,
    task_id: str | None = None,
    *,
    level: str | None = None,
    types: str | None = None,
    content_type: str | None = None,
    start: str | None = None,
    end: str | None = None,
    days: int | None = None,
    tz_offset: int = 0,
) -> dict[str, Any]:
    """看板数据：KPI、分级/类别分布、趋势、告警处置与维度字典。

    旧字段（total_findings / by_level / by_type / score_histogram / tasks / task_id）
    全部保留；``tasks`` 改为「筛选范围内有检测记录的任务数」，``tasks_total`` 才是全库任务数。
    """
    flt = parse_filter(
        task_id=task_id,
        level=level,
        types=types,
        content_type=content_type,
        start=start,
        end=end,
        days=days,
        tz_offset=tz_offset,
    )
    clause, params = flt.where_sql()
    by_level = {key: 0 for key in LEVELS}
    by_type: dict[str, dict[str, Any]] = {}
    buckets = {"0-0.25": 0, "0.25-0.5": 0, "0.5-0.75": 0, "0.75-1.0": 0}
    trend_limited = not flt.has_time_filter

    with db.connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) {_findings_base(clause)}", params).fetchone()[0]
        total_hits = conn.execute(
            f"SELECT COALESCE(SUM(f.hits), 0) {_findings_base(clause)}", params
        ).fetchone()[0]
        filtered_tasks = conn.execute(
            f"SELECT COUNT(DISTINCT f.task_id) {_findings_base(clause)}", params
        ).fetchone()[0]

        # 一次分组同时得到「各级别数量」与「每个类别的数量/命中次数/级别分布」
        for row in conn.execute(
            f"SELECT f.types AS types, f.level AS level, COUNT(*) AS n, "
            f"COALESCE(SUM(f.hits), 0) AS hits {_findings_base(clause)} GROUP BY f.types, f.level",
            params,
        ):
            count = int(row["n"])
            row_level = row["level"]
            if row_level in by_level:
                by_level[row_level] += count
            for key in str(row["types"]).split(","):
                if not key:
                    continue
                item = by_type.setdefault(
                    key,
                    {"count": 0, "hits": 0, "levels": {level_key: 0 for level_key in LEVELS}},
                )
                item["count"] += count
                item["hits"] += int(row["hits"])
                if row_level in item["levels"]:
                    item["levels"][row_level] += count

        histogram = conn.execute(
            "SELECT "
            "COALESCE(SUM(CASE WHEN f.score < 0.25 THEN 1 ELSE 0 END), 0) AS b1, "
            "COALESCE(SUM(CASE WHEN f.score >= 0.25 AND f.score < 0.5 THEN 1 ELSE 0 END), 0) AS b2, "
            "COALESCE(SUM(CASE WHEN f.score >= 0.5 AND f.score < 0.75 THEN 1 ELSE 0 END), 0) AS b3, "
            "COALESCE(SUM(CASE WHEN f.score >= 0.75 THEN 1 ELSE 0 END), 0) AS b4 "
            f"{_findings_base(clause)}",
            params,
        ).fetchone()
        buckets["0-0.25"] = int(histogram["b1"])
        buckets["0.25-0.5"] = int(histogram["b2"])
        buckets["0.5-0.75"] = int(histogram["b3"])
        buckets["0.75-1.0"] = int(histogram["b4"])

        day_grouped = _day_rows(
            conn,
            base=_findings_base(clause),
            params=params,
            tz_offset=flt.tz_offset,
            trend_limited=trend_limited,
            key_name="f.level",
            group_by="f.level",
            has_where=bool(clause),
        )
        by_day: list[dict[str, Any]] = []
        for day in sorted(day_grouped):
            entry = {"date": day, **{key: 0 for key in LEVELS}, "total": 0}
            for level_key, value in day_grouped[day].items():
                if level_key in entry:
                    entry[level_key] = value
                entry["total"] += value
            by_day.append(entry)

        tasks_total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]

        # -- 告警处置（与 findings 同一套筛选；级别筛 alerts.level） -----------------
        alerts_filter = Filter(
            task_id=flt.task_id,
            level=flt.level,
            types=list(flt.types),
            content_type=flt.content_type,
            start=flt.start,
            end=flt.end,
            days=flt.days,
            tz_offset=flt.tz_offset,
            for_alerts=True,
            matches_nothing=flt.level == "SAFE",  # alerts 没有 SAFE 记录
        )
        risk_clause, risk_params = alerts_filter.where_sql()
        risk_total = conn.execute(
            f"SELECT COUNT(*) FROM alerts f LEFT JOIN tasks t ON t.id=f.task_id {risk_clause}",
            risk_params,
        ).fetchone()[0]
        risk_by_level = {"L3": 0, "L2": 0, "L1": 0}
        open_by_level = {"L3": 0, "L2": 0, "L1": 0}
        by_status = {"open": 0, "ack": 0, "ignored": 0}
        for row in conn.execute(
            f"SELECT f.level AS level, f.status AS status, COUNT(*) AS n "
            f"FROM alerts f LEFT JOIN tasks t ON t.id=f.task_id {risk_clause} "
            f"GROUP BY f.level, f.status",
            risk_params,
        ):
            count = int(row["n"])
            if row["level"] in risk_by_level:
                risk_by_level[row["level"]] += count
            if row["status"] in by_status:
                by_status[row["status"]] += count
            if row["status"] == "open" and row["level"] in open_by_level:
                open_by_level[row["level"]] += count

        risk_day_grouped = _day_rows(
            conn,
            base=f"FROM alerts f LEFT JOIN tasks t ON t.id=f.task_id {risk_clause}",
            params=risk_params,
            tz_offset=flt.tz_offset,
            trend_limited=trend_limited,
            key_name="f.status",
            group_by="f.status",
            has_where=bool(risk_clause),
        )
        risk_by_day: list[dict[str, Any]] = []
        for day in sorted(risk_day_grouped):
            entry = {"date": day, "open": 0, "ack": 0, "ignored": 0, "total": 0}
            for status_key, value in risk_day_grouped[day].items():
                if status_key in entry:
                    entry[status_key] = value
                entry["total"] += value
            risk_by_day.append(entry)

        # -- 维度字典：不受筛选影响，供前端下拉框/最近任务用 ------------------------
        content_types = [
            {
                "value": row["content_type"],
                "label": content_type_label(row["content_type"]),
                "count": int(row["n"]),
            }
            for row in conn.execute(
                "SELECT content_type, COUNT(*) AS n FROM tasks "
                "GROUP BY content_type ORDER BY n DESC, content_type"
            )
        ]
        recent_tasks = [
            {
                "id": row["id"],
                "source": row["source"],
                "content_type": row["content_type"],
                "created_at": row["created_at"],
                "total_findings": int(row["total_findings"]),
            }
            for row in conn.execute(
                "SELECT t.id AS id, t.source AS source, t.content_type AS content_type, "
                "t.created_at AS created_at, "
                "(SELECT COUNT(*) FROM findings f2 WHERE f2.task_id=t.id) AS total_findings "
                "FROM tasks t ORDER BY t.created_at DESC, t.rowid DESC LIMIT 50"
            )
        ]

    handled = by_status["ack"] + by_status["ignored"]
    top_types = sorted(by_type.items(), key=lambda kv: (-kv[1]["count"], kv[0]))[:10]
    return {
        # -- 旧字段（兼容既有看板/批量视图） --
        "total_findings": int(total),
        "tasks": int(filtered_tasks),
        "by_level": by_level,
        "by_type": [
            {
                "type": key,
                "label": type_label(key),
                "count": item["count"],
                "hits": item["hits"],
                "levels": item["levels"],
            }
            for key, item in top_types
        ],
        "score_histogram": buckets,
        "task_id": task_id,
        # -- 新增 KPI / 趋势 / 处置 / 维度 --
        "total_hits": int(total_hits),
        "tasks_total": int(tasks_total),
        "by_day": by_day,
        "risk": {
            "total": int(risk_total),
            "open": by_status["open"],
            "ack": by_status["ack"],
            "ignored": by_status["ignored"],
            "handled": handled,
            "handled_ratio": round(handled / risk_total, 4) if risk_total else 0.0,
            "by_level": risk_by_level,
            "open_by_level": open_by_level,
            "by_day": risk_by_day,
        },
        "dimensions": {
            "types": [
                {"key": entity.value, "label": label} for entity, label in ENTITY_LABELS.items()
            ],
            "content_types": content_types,
            "tasks": recent_tasks,
            "levels": [{"value": key, "label": LEVEL_LABELS[key]} for key in LEVEL_ORDER],
        },
        "filter": flt.echo(trend_limited=trend_limited),
    }
