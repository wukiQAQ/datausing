"""FlowMask FastAPI 入口。

启动即做三件事：建库建表（幂等）→ 应用数据库里的规则启停 → 挂载全部路由。
鉴权可选：配置 ``FLOWMASK_API_TOKEN`` 后所有 ``/api/v1/*`` 需要 Bearer Token，
本机演示默认不鉴权（评委打开就能点）。

本地启动：

    cd server
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8090 --reload

或直接 ``python -m app.main``。
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api.deps import require_token
from .api.routes_audit import router as audit_router
from .api.routes_detect import router as detect_router
from .api.routes_findings import router as findings_router
from .api.routes_health import router as health_router
from .api.routes_dynamic import router as dynamic_router
from .api.routes_rules import router as rules_router
from .api.routes_rules import sync_active_rules
from .api.routes_stats import router as stats_router
from .api.routes_tasks import router as tasks_router
from .core.config import get_settings
from .core.db import get_database

WEBUI_DIR = Path(__file__).resolve().parent / "webui"

DESCRIPTION = """
FlowMask：网络与文档流量的分级脱敏与审计溯源系统（数据安全作品赛）。

- **自带控制台**：启动后打开 `/ui`（左右对照 + 任务/审计 + 统计看板 + 规则启停），无需前端构建

- **输入**：粘贴文本 / 上传 txt·json·csv·sql（Word·PDF 待接入）/ pcap 离线检测（另一 CLI）
- **识别**：L1 规则（12 条 + 校验位）→ L2 BERT 语义（可选）→ L3 本地大模型复核（可选）
- **输出**：四级敏感度 + 脱敏副本 + 可复算的置信度向量 + HMAC 哈希链审计日志

评审提示：`/docs` 里可直接试调接口；`/api/v1/detect` 是不落库的即时检测，
`/api/v1/tasks` 是带留痕的后台任务。
"""


def create_app() -> FastAPI:
    settings = get_settings()
    db = get_database()
    db.init()
    sync_active_rules(db)

    app = FastAPI(
        title="FlowMask API",
        version=__version__,
        description=DESCRIPTION,
        openapi_tags=[
            {"name": "system", "description": "健康检查"},
            {"name": "detect", "description": "即时检测（不落库，用于左右对照演示）"},
            {"name": "tasks", "description": "后台任务：上传/粘贴 → 识别 → 落库 → 审计"},
            {"name": "findings", "description": "检测结果查询"},
            {"name": "stats", "description": "看板统计"},
            {"name": "rules", "description": "内置/自定义敏感规则"},
            {"name": "audit", "description": "审计溯源与哈希链校验"},
        ],
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])
    for sub in (
        health_router,
        detect_router,
        tasks_router,
        findings_router,
        stats_router,
        rules_router,
        audit_router,
        dynamic_router,
    ):
        router.include_router(sub)
    app.include_router(router)

    # 自带控制台：纯静态单页（无构建步骤、无外网依赖），评委打开根路径即可用
    app.mount("/ui", StaticFiles(directory=WEBUI_DIR, html=True), name="webui")

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        return RedirectResponse(url="/ui")

    return app


app = create_app()


def main() -> None:
    """``python -m app.main`` 的入口：默认只监听回环地址。"""
    import uvicorn

    host = os.environ.get("FLOWMASK_HOST", "127.0.0.1")
    port = int(os.environ.get("FLOWMASK_PORT", "8090"))
    uvicorn.run("app.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
