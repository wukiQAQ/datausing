"""健康检查：给客户端/评测脚本一个"服务与依赖是否就绪"的入口。"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from .. import __version__
from ..core.config import Settings
from ..core.db import Database
from ..identify.keywords import get_keyword_index
from ..identify.ner import build_ner_detector
from .deps import db_dep, settings_dep
from .schemas import HealthOut

router = APIRouter(tags=["system"])

STARTED_AT = time.time()


@router.get("/health", response_model=HealthOut, summary="健康检查")
def health(settings: Settings = Depends(settings_dep), db: Database = Depends(db_dep)) -> HealthOut:
    keywords = get_keyword_index()
    ner = build_ner_detector(settings)
    ner_ready = bool(settings.enable_ner and ner is not None and ner.available())
    return HealthOut(
        status="ok",
        version=__version__,
        db_path=str(db.path),
        enable_l2=settings.enable_l2,
        enable_l3=settings.enable_l3,
        keywords=len(keywords.terms),
        keyword_tiers=keywords.count_by_tier(),
        ner=ner_ready,
        ner_backend=getattr(ner, "name", "") if ner_ready else "",
        uptime_s=int(time.time() - STARTED_AT),
    )
