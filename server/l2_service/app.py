"""L2 语义识别服务 HTTP 接口。

契约（一体机 ``HttpSemanticClassifier`` 与它对话）：

    POST /v1/classify
    {"items": [{"id": "seg-1", "content": "……"}]}          # 批量 ≤ 32
    ->
    {"model": "mock-lexicon", "degraded": false, "latency_ms": 0.4,
     "items": [{"id": "seg-1",
                "types": [{"type": "address", "confidence": 0.85}],
                "benign": 0.0,
                "notes": []}]}

``benign`` 是"这段是非敏感/示例数据"的置信度：一体机用它压制 L1 的形似误报。
任何内部异常都不会让服务 500：返回该条 ``degraded=true`` 的空结果，让一体机降级继续。
"""

from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from . import __version__
from .backends import MAX_BATCH, Backend, build_backend, health_payload

STARTED_AT = time.time()


class ClassifyItem(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    content: str = Field(default="", max_length=20000)


class ClassifyRequest(BaseModel):
    items: list[ClassifyItem] = Field(min_length=1)


class ClassifyTypeOut(BaseModel):
    type: str
    confidence: float


class ClassifyItemOut(BaseModel):
    id: str
    types: list[ClassifyTypeOut] = Field(default_factory=list)
    benign: float = 0.0
    notes: list[str] = Field(default_factory=list)
    degraded: bool = False


class ClassifyResponse(BaseModel):
    model: str
    degraded: bool = False
    latency_ms: float
    items: list[ClassifyItemOut]


def create_app(backend: Backend | None = None) -> FastAPI:
    """构建应用；``backend`` 可注入（测试用 echo，生产用环境变量选 mock/onnx）。"""
    chosen = backend or build_backend()
    app = FastAPI(
        title="FlowMask L2 语义识别服务",
        version=__version__,
        description="多标签语义识别（类别级置信度 + 非敏感判定），供 FlowMask 一体机按需调用",
    )
    app.state.backend = chosen
    app.state.started_at = STARTED_AT

    @app.get("/health")
    def health() -> dict[str, object]:
        return health_payload(app.state.backend, app.state.started_at)

    @app.get("/v1/model")
    def model_info() -> dict[str, object]:
        return {"version": __version__, "detail": app.state.backend.describe()}

    @app.post("/v1/classify", response_model=ClassifyResponse)
    def classify(payload: ClassifyRequest, request: Request) -> ClassifyResponse:
        if len(payload.items) > MAX_BATCH:
            raise HTTPException(status_code=413, detail=f"单批最多 {MAX_BATCH} 条")
        began = time.perf_counter()
        texts = [item.content for item in payload.items]
        backend_now: Backend = request.app.state.backend
        try:
            predictions = backend_now.predict(texts)
            degraded = False
        except Exception as exc:  # 模型异常不拖垮一体机：返回降级结果
            predictions = []
            degraded = True
            request.app.state.last_error = f"{type(exc).__name__}: {exc}"

        items: list[ClassifyItemOut] = []
        for index, item in enumerate(payload.items):
            if degraded or index >= len(predictions):
                items.append(ClassifyItemOut(id=item.id, degraded=True))
                continue
            pred = predictions[index]
            items.append(
                ClassifyItemOut(
                    id=item.id,
                    types=[ClassifyTypeOut(type=t.type, confidence=t.confidence) for t in pred.types],
                    benign=pred.benign,
                    notes=pred.notes,
                )
            )
        latency_ms = round((time.perf_counter() - began) * 1000, 3)
        return ClassifyResponse(
            model=backend_now.name, degraded=degraded, latency_ms=latency_ms, items=items
        )

    return app


app = create_app()
