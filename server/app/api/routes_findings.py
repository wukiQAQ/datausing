"""检测结果查询：全局分页 + 单条详情。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..core.db import Database
from ..tasks.service import get_finding, page_findings
from .deps import db_dep
from .schemas import FindingOut, FindingPage

router = APIRouter(tags=["findings"])


@router.get("/findings", response_model=FindingPage, summary="检测结果分页查询")
def list_findings(
    task_id: str | None = None,
    level: str | None = Query(default=None, description="SAFE / L1 / L2 / L3"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Database = Depends(db_dep),
) -> FindingPage:
    items, total = page_findings(db, task_id=task_id, level=level, page=page, page_size=page_size)
    return FindingPage(items=[FindingOut(**item) for item in items], total=total, page=page, page_size=page_size)


@router.get("/findings/{finding_id}", response_model=FindingOut, summary="单条结果详情")
def finding_detail(finding_id: int, db: Database = Depends(db_dep)) -> FindingOut:
    item = get_finding(db, finding_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="检测结果不存在")
    return FindingOut(**item)
