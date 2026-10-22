"""敏感词表（三级知识库）只读接口。

知识库在启动时从 `app/identify/knowledge/*.yaml` 加载并编译成 Aho-Corasick
自动机；这里只是把当前生效的条目暴露出来，方便前端/运维确认「到底命中哪套词表」。
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from ..identify.keywords import knowledge_summary
from .schemas import KeywordTermOut, KeywordsOut

router = APIRouter(prefix="/keywords", tags=["keywords"])

TIERS = ("regulatory", "industry", "enterprise")


@router.get("", response_model=KeywordsOut, summary="查看当前生效的敏感词表")
def list_keywords(
    tier: str | None = Query(default=None, description="只返回某一级：regulatory / industry / enterprise"),
    limit: int = Query(default=200, ge=1, le=2000, description="最多返回多少条"),
) -> KeywordsOut:
    """返回词表总览与条目明细。

    `total` 是本次过滤后的条数，`all_total` 是词表总条目数，`tiers` 是三级分布。
    """
    if tier is not None and tier not in TIERS:
        # 复用 FastAPI 的 422 语义不直观，这里直接 400 说清楚可选值
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="tier 仅支持 regulatory / industry / enterprise")

    summary = knowledge_summary()
    terms = [item for item in summary["terms"] if tier is None or item["tier"] == tier]
    return KeywordsOut(
        total=len(terms),
        all_total=summary["total"],
        tiers=summary["tiers"],
        terms=[KeywordTermOut(**item) for item in terms[:limit]],
    )
