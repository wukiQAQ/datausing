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
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from . import __version__
from .api.deps import require_token
from .api.routes_alerts import router as alerts_router
from .api.routes_assistant import router as assistant_router
from .api.routes_audit import router as audit_router
from .api.routes_detect import router as detect_router
from .api.routes_findings import router as findings_router
from .api.routes_health import router as health_router
from .api.routes_keywords import router as keywords_router
from .api.routes_plugins import router as plugins_router
from .api.routes_dynamic import router as dynamic_router
from .api.routes_preferences import router as preferences_router
from .api.routes_rules import router as rules_router
from .api.routes_rules import sync_active_rules
from .api.routes_stats import router as stats_router
from .api.routes_tasks import router as tasks_router
from .api.routes_traffic import router as traffic_router
from .core.paths import frontend_dir, resource_root
from .core.staticfiles import FrontendStaticFiles
from .core.config import get_settings
from .core.db import get_database
from .plugins import load_plugins
from .security import SecurityError, security_error_response, validation_error_response
from .identify.snapshots import get_snapshot_store
from .traffic.live import get_live_manager

# 自带单页控制台随包分发：开发态是 server/app/webui，打包后是解包目录下的 app/webui
WEBUI_DIR = resource_root() / "app" / "webui"
# 正式前端（Vue3 + Element Plus + ECharts）：构建产物在 server/web/dist，
# 未构建时优雅降级到自带单页控制台，保证任何环境下都能演示。
FRONTEND_DIR = frontend_dir()

DESCRIPTION = """
FlowMask：网络与文档流量的分级脱敏与审计溯源系统（数据安全作品赛）。

- **自带控制台**：启动后打开 `/ui`（左右对照 + 任务/审计 + 统计看板 + 规则启停），无需前端构建

- **输入**：粘贴文本 / 上传 txt·json·csv·sql·docx·pdf·xlsx（图片 OCR 需可选依赖）/ pcap 离线检测（另一 CLI）
- **识别**：L1 规则（12 条 + 校验位）→ L2 BERT 语义（可选）→ L3 本地大模型复核（可选）
- **输出**：四级敏感度 + 脱敏副本 + 可复算的置信度向量 + HMAC 哈希链审计日志
- **增强**：6 种脱敏策略（block/partial/hash/generalize/pseudonym/encrypt）与脱敏文件导出；
  本地 AI 助手（场景模板 / 策略推荐 / 风险研判，默认纯规则，可选本机 Ollama，数据不出机）

评审提示：`/docs` 里可直接试调接口；`/api/v1/detect` 是不落库的即时检测，
`/api/v1/tasks` 是带留痕的后台任务。
"""


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """应用退出时停掉所有实时跟随线程，避免残留线程继续写库。"""
    get_snapshot_store().start()
    try:
        yield
    finally:
        get_live_manager().stop_all()
        get_snapshot_store().close()


def create_app() -> FastAPI:
    settings = get_settings()
    db = get_database()
    db.init()
    sync_active_rules(db)
    # 内置插件 + 第三方插件（FLOWMASK_PLUGINS）在启动时登记一次；失败只记警告
    load_plugins(settings)

    app = FastAPI(
        lifespan=_lifespan,
        title="FlowMask API",
        version=__version__,
        description=DESCRIPTION,
        openapi_tags=[
            {"name": "system", "description": "健康检查"},
            {"name": "detect", "description": "即时检测（不落库，用于左右对照演示）"},
            {"name": "tasks", "description": "后台任务：上传/粘贴 → 识别 → 落库 → 审计"},
            {"name": "traffic", "description": "流量接入：pcap 导入 / 外部推送 / 实时旁路跟随 → 同一套识别分级与审计"},
            {"name": "findings", "description": "检测结果查询"},
            {"name": "alerts", "description": "分级告警与脱敏建议（检测 → 告警 → 处置）"},
            {"name": "assistant", "description": "本地 AI 助手：场景模板、脱敏策略推荐与风险研判（默认纯规则，可选本机 Ollama）"},
            {"name": "plugins", "description": "可扩展：引擎 / 算子 / 解析器插件注册表"},
            {"name": "stats", "description": "看板统计"},
            {"name": "rules", "description": "内置/自定义敏感规则"},
            {"name": "audit", "description": "审计溯源与哈希链校验"},
            {"name": "preferences", "description": "用户偏好（主题）与多用户记忆"},
        ],
    )
    app.add_exception_handler(SecurityError, security_error_response)
    app.add_exception_handler(RequestValidationError, validation_error_response)
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
        keywords_router,
        detect_router,
        tasks_router,
        traffic_router,
        findings_router,
        alerts_router,
        assistant_router,
        plugins_router,
        stats_router,
        rules_router,
        audit_router,
        dynamic_router,
        preferences_router,
    ):
        router.include_router(sub)
    app.include_router(router)

    # 正式前端：Vue3 + Element Plus + ECharts，构建产物挂在 /app
    # （vite base=/app/，所以刷新/深链都不会 404；入口 HTML 走 no-cache，
    #  带哈希的 assets/ 走一年 immutable，重新构建后刷新即可见新版）
    if FRONTEND_DIR.is_dir():
        app.mount("/app", FrontendStaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

    # 自带控制台：纯静态单页（无构建步骤、无外网依赖），评委打开根路径即可用；
    # 打包时若单页资源缺失也不至于让进程起不来（/app 或 /docs 仍可用）
    if WEBUI_DIR.is_dir():
        app.mount("/ui", FrontendStaticFiles(directory=WEBUI_DIR, html=True), name="webui")

    @app.get("/", include_in_schema=False)
    def index() -> RedirectResponse:
        # 构建过正式前端就优先跳转，否则回到自带单页
        return RedirectResponse(url="/app/" if FRONTEND_DIR.is_dir() else "/ui")

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
