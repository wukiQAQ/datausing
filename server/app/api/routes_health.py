"""健康检查：给客户端/评测脚本一个"服务与依赖是否就绪"的入口。"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from .. import __version__
from ..core.config import Settings
from ..core.db import Database
from .deps import db_dep, settings_dep
from .schemas import HealthOut

router = APIRouter(tags=["system"])

STARTED_AT = time.time()


@router.get("/health", response_model=HealthOut, summary="健康检查")
def health(settings: Settings = Depends(settings_dep), db: Database = Depends(db_dep)) -> HealthOut:
    return HealthOut(
        status="ok",
        version=__version__,
        db_path=str(db.path),
        enable_l2=settings.enable_l2,
        enable_l3=settings.enable_l3,
        uptime_s=int(time.time() - STARTED_AT),
    )
