"""分级告警与脱敏建议联动（需求 9.2）。

链路：任务落库 findings 的同时按级别生成告警 → 告警带一条「怎么处置」的建议
→ 接口可查询/汇总/认领或忽略，形成「检测 → 告警 → 处置」闭环。

级别口径与四级脱敏模板一致（`identify/masking.py`）：

| 检测级别 | 告警等级 | 建议动作 |
| --- | --- | --- |
| L3 高危 | 紧急 | 整段替换为类别占位，禁止明文留存/跨域传输 |
| L2 中危 | 重要 | 保留首字符 + 星号掩码，只在脱敏视图展示 |
| L1 低危 | 提示 | 保留原文 + 前缀警示，纳入抽检 |

触发阈值由 ``FLOWMASK_ALERT_MIN_LEVEL`` 控制（默认 L1，即所有敏感命中都进队列）。
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from .core.config import get_settings
from .core.db import Database, utcnow_iso
from .filters import parse_filter
from .identify import Level, SegmentFinding, entity_label, parse_entity_type

ALERT_STATUSES = ("open", "ack", "ignored")
ALERT_STATUS_LABELS = {"open": "待处理", "ack": "已认领", "ignored": "已忽略"}
ALERT_SEVERITY_LABELS = {Level.HIGH: "紧急", Level.MID: "重要", Level.LOW: "提示"}

# 建议动作（与 identify/masking.py 的四级模板同一口径，便于"告警里说的就是系统会做的"）
_ACTIONS = {
    Level.HIGH: "建议整段替换为「[已屏蔽:类别]」，禁止明文留存与跨域传输；确需查看时走动态脱敏授权并留审计。",
    Level.MID: "建议保留首字符、其余用星号掩码，只在脱敏视图中展示；导出前再做一次脱敏校验。",
    Level.LOW: "建议保留原文并加「[低危]」前缀警示，纳入月度抽检；确认外发时按中危口径掩码。",
}


def _type_labels(type_keys: Iterable[str]) -> list[str]:
    """英文类别键 → 中文类别名；认不出的键原样保留，避免建议里丢信息。"""
    labels: list[str] = []
    for key in type_keys:
        parsed = parse_entity_type(key)
        label = entity_label(parsed) if parsed else key
        if label and label not in labels:
            labels.append(label)
    return labels


def severity_label(level: Level) -> str:
    """检测级别 → 告警等级中文名（SAFE 不告警，退回级别名）。"""
    return ALERT_SEVERITY_LABELS.get(level, level.label)


def suggestion_for(level: Level, type_keys: Iterable[str]) -> str:
    """按级别 + 类别生成一条可执行的处置建议。"""
    labels = _type_labels(type_keys)
    subject = "、".join(labels) if labels else "敏感数据"
    action = _ACTIONS.get(level, "建议人工复核后按数据分级策略处置。")
    return f"命中{subject}（{level.label}）：{action}"


def title_for(level: Level, type_keys: Iterable[str], path: str) -> str:
    """告警标题：等级 + 类别 + 位置，便于列表里一眼定位。"""
    labels = _type_labels(type_keys)
    subject = "、".join(labels) if labels else "敏感数据"
    return f"{severity_label(level)}告警：{subject}（{path}）"


def should_alert(level: Level) -> bool:
    """检测级别是否达到告警阈值（阈值读配置，缺省 L1）。"""
    threshold = Level.parse(get_settings().alert_min_level)
    return level != Level.SAFE and level.severity >= threshold.severity


def build_alert(
    *,
    task_id: str,
    finding_id: int,
    finding: SegmentFinding,
    created_at: str | None = None,
) -> tuple[Any, ...]:
    """把一条 finding 转成 ``alerts`` 表的插入参数（顺序与 INSERT 对齐）。"""
    return (
        task_id,
        int(finding_id),
        finding.level.value,
        ",".join(finding.types),
        float(finding.score),
        finding.path,
        title_for(finding.level, finding.types, finding.path),
        suggestion_for(finding.level, finding.types),
        "open",
        created_at or utcnow_iso(),
    )


def insert_alerts(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    findings: Iterable[tuple[int, SegmentFinding]],
) -> int:
    """在调用方的事务里批量写告警（与 findings 同事务，避免"有结果没告警"）。"""
    count = 0
    for finding_id, finding in findings:
        if not should_alert(finding.level):
            continue
        conn.execute(
            """
            INSERT INTO alerts
              (task_id, finding_id, level, types, score, path, title, suggestion, status, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            build_alert(task_id=task_id, finding_id=finding_id, finding=finding),
        )
        count += 1
    return count


def _row_to_alert(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["types"] = [t for t in str(item["types"]).split(",") if t]
    item["level_label"] = Level.parse(item["level"]).label
    item["severity"] = severity_label(Level.parse(item["level"]))
    item["status_label"] = ALERT_STATUS_LABELS.get(item["status"], item["status"])
    # 与 findings 保持一致：类型中文名 + 任务来源/内容形态（LEFT JOIN 缺失时给空串）
    item["type_labels"] = _type_labels(item["types"])
    item["source"] = item.pop("task_source", None) or ""
    item["content_type"] = item.pop("task_content_type", None) or ""
    return item


def _alerts_base(clause: str) -> str:
    """alerts + tasks 的统一 FROM 片段（content_type 筛选依赖 tasks）。"""
    return f"FROM alerts f LEFT JOIN tasks t ON t.id=f.task_id {clause}"


def page_alerts(
    db: Database,
    *,
    task_id: str | None = None,
    level: str | None = None,
    status: str | None = None,
    types: str | None = None,
    content_type: str | None = None,
    start: str | None = None,
    end: str | None = None,
    days: int | None = None,
    tz_offset: int = 0,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    """分页查告警；级别/状态/时间/类型非法时抛 ``ValueError``（接口层转 400）。"""
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 100))
    try:
        flt = parse_filter(
            task_id=task_id,
            level=level,
            types=types,
            content_type=content_type,
            start=start,
            end=end,
            days=days,
            tz_offset=tz_offset,
            for_alerts=True,
        )
    except ValueError as exc:
        # 保持既有告警接口的级别错误文案，前端提示不回归
        if level and "级别" in str(exc):
            raise ValueError(f"告警级别非法：{level!r}（可选 L3/L2/L1/SAFE）") from exc
        raise
    clause, params = flt.where_sql()
    if status:
        normalized = status.strip().lower()
        if normalized not in ALERT_STATUSES:
            raise ValueError(f"告警状态非法：{status!r}（可选 {', '.join(ALERT_STATUSES)}）")
        clause = f"{clause} AND f.status=?" if clause else "WHERE f.status=?"
        params.append(normalized)
    with db.connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) {_alerts_base(clause)}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT f.*, t.source AS task_source, t.content_type AS task_content_type "
            f"{_alerts_base(clause)} ORDER BY f.id DESC LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    return [_row_to_alert(row) for row in rows], int(total)


def get_alert(db: Database, alert_id: int) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT f.*, t.source AS task_source, t.content_type AS task_content_type "
            "FROM alerts f LEFT JOIN tasks t ON t.id=f.task_id WHERE f.id=?",
            (int(alert_id),),
        ).fetchone()
    return None if row is None else _row_to_alert(row)


def handle_alert(
    db: Database,
    alert_id: int,
    *,
    status: str,
    operator_id: str = "system",
    note: str = "",
) -> dict[str, Any] | None:
    """认领 / 忽略 / 重置告警；不存在的 id 返回 ``None``。"""
    normalized = (status or "").strip().lower()
    if normalized not in ALERT_STATUSES:
        raise ValueError(f"告警状态非法：{status!r}（可选 {', '.join(ALERT_STATUSES)}）")
    handled = normalized in {"ack", "ignored"}
    with db.write() as conn:
        cursor = conn.execute(
            "UPDATE alerts SET status=?, handled_at=?, handled_by=?, note=? WHERE id=?",
            (
                normalized,
                utcnow_iso() if handled else None,
                operator_id if handled else None,
                note[:200],
                int(alert_id),
            ),
        )
        if cursor.rowcount == 0:
            return None
    return get_alert(db, alert_id)


def alert_summary(
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
    """看板/角标用的汇总：总数、待处理、按级别与按状态分布、已处理比例。

    与 /alerts 列表共用同一套筛选口径；``handled = ack + ignored``，
    注意「已认领」只是有人接手，并不代表处置完成，因此比率字段名用 handled。
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
        for_alerts=True,
    )
    clause, params = flt.where_sql()
    by_level = {level.value: 0 for level in Level if level != Level.SAFE}
    open_by_level = {level.value: 0 for level in Level if level != Level.SAFE}
    by_status = {status: 0 for status in ALERT_STATUSES}
    with db.connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) {_alerts_base(clause)}", params).fetchone()[0]
        for row in conn.execute(
            f"SELECT f.level AS level, f.status AS status, COUNT(*) AS n "
            f"{_alerts_base(clause)} GROUP BY f.level, f.status",
            params,
        ):
            count = int(row["n"])
            if row["level"] in by_level:
                by_level[row["level"]] += count
            if row["level"] in open_by_level and row["status"] == "open":
                open_by_level[row["level"]] += count
            if row["status"] in by_status:
                by_status[row["status"]] += count
    handled = by_status["ack"] + by_status["ignored"]
    return {
        "total": int(total),
        "open": by_status.get("open", 0),
        "by_level": by_level,
        "by_status": by_status,
        "task_id": task_id,
        "handled": handled,
        "handled_ratio": round(handled / total, 4) if total else 0.0,
        "open_by_level": open_by_level,
    }


def alerts_for_tasks(db: Database, task_ids: Iterable[str]) -> dict[str, Any]:
    """按一组任务聚合告警（实时旁路：一个会话的多个窗口任务）。

    用于 ``/traffic/live`` 面板展示「本次跟随产生了多少告警、最近一条是什么」，
    不返回明细列表；明细仍走 ``/alerts?task_id=...``。
    """
    ids = [str(task_id) for task_id in dict.fromkeys(task_ids) if task_id]
    by_level = {level.value: 0 for level in Level if level != Level.SAFE}
    summary: dict[str, Any] = {
        "total": 0,
        "open": 0,
        "by_level": by_level,
        "last_alert": None,
    }
    if not ids:
        return summary
    marks = ",".join("?" for _ in ids)
    with db.connect() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM alerts WHERE task_id IN ({marks})", ids
        ).fetchone()[0]
        for row in conn.execute(
            f"SELECT level, status, COUNT(*) AS n FROM alerts "
            f"WHERE task_id IN ({marks}) GROUP BY level, status",
            ids,
        ):
            by_level[row["level"]] = by_level.get(row["level"], 0) + int(row["n"])
            if row["status"] == "open":
                summary["open"] += int(row["n"])
        last = conn.execute(
            f"SELECT id, title, level, status, created_at FROM alerts "
            f"WHERE task_id IN ({marks}) ORDER BY id DESC LIMIT 1",
            ids,
        ).fetchone()
    summary["total"] = int(total)
    if last is not None:
        summary["last_alert"] = {
            "id": int(last["id"]),
            "title": last["title"],
            "level": last["level"],
            "status": last["status"],
            "created_at": last["created_at"],
        }
    return summary
