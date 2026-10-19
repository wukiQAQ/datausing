"""看板统计：分级分布、类别 TopN、分数直方图、任务计数。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..core.db import Database
from ..tasks.service import stats as stats_service
from .deps import db_dep
from .schemas import StatsOut

router = APIRouter(tags=["stats"])


@router.get("/stats", response_model=StatsOut, summary="看板统计数据")
def dashboard_stats(task_id: str | None = None, db: Database = Depends(db_dep)) -> StatsOut:
    return StatsOut(**stats_service(db, task_id))
