"""看板统计：分级分布、类别 TopN、分数直方图、按日趋势、告警处置口径。

所有参数与 ``/findings``、``/alerts`` 共用同一套筛选语义（见 ``app/filters.py``），
保证同一筛选下三个接口的数字可以互相对照。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..core.db import Database
from ..tasks.service import stats as stats_service
from .deps import db_dep
from .schemas import StatsOut

router = APIRouter(tags=["stats"])


@router.get("/stats", response_model=StatsOut, summary="看板统计数据")
def dashboard_stats(
    task_id: str | None = None,
    level: str | None = Query(default=None, description="SAFE / L1 / L2 / L3"),
    types: str | None = Query(default=None, description="逗号分隔的类别键，多值 OR"),
    content_type: str | None = Query(default=None, description="text/json/csv/sql/…"),
    start: str | None = Query(default=None, description="起始时间（含），YYYY-MM-DD 或 ISO8601"),
    end: str | None = Query(default=None, description="结束时间（不含），YYYY-MM-DD 或 ISO8601"),
    days: int | None = Query(default=None, description="相对窗口天数（1~3650），start/end 优先"),
    tz_offset: int = Query(default=0, description="当地时区偏移分钟数，如 480"),
    db: Database = Depends(db_dep),
) -> StatsOut:
    try:
        data = stats_service(
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
    return StatsOut(**data)
