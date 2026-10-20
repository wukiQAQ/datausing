"""L3 护栏推理服务：``POST /v1/judge`` 把一段文本判成 Safe / Controversial / Unsafe。

与 ``l2_service`` 同构：独立进程、回环地址、失败不 500（返回 ``degraded=true``），
主服务（``app``）只把它当可降级的依赖。
"""

from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .backends import GuardBackend, build_backend

MAX_TEXT_CHARS = 8000


class JudgeIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS, description="待判定的文本")


class JudgeOut(BaseModel):
    risk: str = Field(description="Safe / Controversial / Unsafe")
    reason: str = ""
    model: str = ""
    latency_ms: float = 0.0
    degraded: bool = False
    # 敏感类别（护栏模型从 Categories 抽、指令模型直接给）：主服务据此做"类别感知"复核
    categories: list[str] = Field(default_factory=list)
    # 分级建议（只有指令模型给）：L3/L2/L1/SAFE，主服务只在"升级方向"采用
    level: str = ""


def create_app(backend: GuardBackend | None = None) -> FastAPI:
    active = backend or build_backend()
    app = FastAPI(title="FlowMask L3 Guard Service", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, object]:
        return {"status": "ok", "backend": active.describe()}

    @app.get("/v1/model")
    def model_info() -> dict[str, object]:
        return active.describe()

    @app.post("/v1/judge", response_model=JudgeOut)
    def judge(payload: JudgeIn) -> JudgeOut:
        try:
            result = active.judge(payload.text)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - 模型异常一律降级，不能让主服务 500
            return JudgeOut(
                risk="",
                reason=f"L3 后端异常：{type(exc).__name__}: {exc}"[:300],
                model=getattr(active, "name", ""),
                latency_ms=0.0,
                degraded=True,
            )
        return JudgeOut(
            risk=result.risk,
            reason=result.reason,
            model=result.model,
            latency_ms=result.latency_ms,
            degraded=False,
            categories=list(result.categories),
            level=result.level,
        )

    @app.get("/")
    def index() -> dict[str, object]:
        return {
            "service": "flowmask-l3",
            "endpoints": ["/health", "/v1/model", "/v1/judge"],
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    return app


app = create_app()


