"""告警接口：按级别/状态/类别/时间查询、汇总、认领或忽略（检测 → 告警 → 处置闭环）。

告警在任务落库时由 ``app/alerts.py`` 自动生成；这里只负责查询与状态流转。
筛选语义与 ``/findings``、``/stats`` 共用 ``app/filters.py``。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..alerts import alert_summary, get_alert, handle_alert, page_alerts
from ..core.db import Database
from .deps import Operator, db_dep, operator_dep
from .schemas import AlertAckIn, AlertOut, AlertPage, AlertSummaryOut

router = APIRouter(tags=["alerts"])


@router.get("/alerts/summary", response_model=AlertSummaryOut, summary="告警汇总（看板/角标）")
def alerts_summary(
    task_id: str | None = None,
    level: str | None = Query(default=None, description="L1 / L2 / L3（SAFE 返回空集）"),
    types: str | None = Query(default=None, description="逗号分隔的类别键，多值 OR"),
    content_type: str | None = Query(default=None, description="text/json/csv/sql/…"),
    start: str | None = Query(default=None, description="起始时间（含），YYYY-MM-DD 或 ISO8601"),
    end: str | None = Query(default=None, description="结束时间（不含），YYYY-MM-DD 或 ISO8601"),
    days: int | None = Query(default=None, description="相对窗口天数（1~3650），start/end 优先"),
    tz_offset: int = Query(default=0, description="当地时区偏移分钟数，如 480"),
    db: Database = Depends(db_dep),
) -> AlertSummaryOut:
    """先于 ``/alerts/{alert_id}`` 注册，避免 ``summary`` 被当成路径参数。"""
    try:
        data = alert_summary(
            db,
            task_id,
            level=level,
            types=types,
            content_type=content_type,
            start=start,
            end=end,
            days=days,
            tz_offset=tz_offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return AlertSummaryOut(**data)


@router.get("/alerts", response_model=AlertPage, summary="告警列表（按级别/状态过滤）")
def list_alert(
    task_id: str | None = None,
    level: str | None = None,
    status_filter: str | None = Query(default=None, alias="status", description="open / ack / ignored"),
    types: str | None = Query(default=None, description="逗号分隔的类别键，多值 OR"),
    content_type: str | None = Query(default=None, description="text/json/csv/sql/…"),
    start: str | None = Query(default=None, description="起始时间（含），YYYY-MM-DD 或 ISO8601"),
    end: str | None = Query(default=None, description="结束时间（不含），YYYY-MM-DD 或 ISO8601"),
    days: int | None = Query(default=None, description="相对窗口天数（1~3650），start/end 优先"),
    tz_offset: int = Query(default=0, description="当地时区偏移分钟数，如 480"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Database = Depends(db_dep),
) -> AlertPage:
    try:
        items, total = page_alerts(
            db,
            task_id=task_id,
            level=level,
            status=status_filter,
            types=types,
            content_type=content_type,
            start=start,
            end=end,
            days=days,
            tz_offset=tz_offset,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return AlertPage(
        items=[AlertOut(**item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/alerts/{alert_id}", response_model=AlertOut, summary="告警详情")
def alert_detail(alert_id: int, db: Database = Depends(db_dep)) -> AlertOut:
    alert = get_alert(db, alert_id)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="告警不存在")
    return AlertOut(**alert)


@router.post("/alerts/{alert_id}/ack", response_model=AlertOut, summary="认领 / 忽略 / 重置告警")
def ack_alert(
    alert_id: int,
    payload: AlertAckIn | None = None,
    operator: Operator = Depends(operator_dep),
    db: Database = Depends(db_dep),
) -> AlertOut:
    body = payload or AlertAckIn()
    try:
        alert = handle_alert(
            db,
            alert_id,
            status=body.status,
            operator_id=operator.operator_id,
            note=body.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="告警不存在")
    return AlertOut(**alert)
