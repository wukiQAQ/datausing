"""L3 护栏推理服务（Qwen3Guard-Gen 系列）：本地回环、可降级。

对外只有一个判定接口 ``POST /v1/judge``，主服务（``server/app``）把它当可降级依赖：
超时/异常时保留 L1/L2 结论并标记 ``degraded``。

启动：

.. code-block:: powershell

   cd server
   python -m l3_service            # 默认 127.0.0.1:8092
   # 然后在主服务侧打开 L3：
   $env:FLOWMASK_ENABLE_L3 = "1"
   $env:FLOWMASK_L3_ENDPOINT = "http://127.0.0.1:8092/v1/judge"
   python -m uvicorn app.main:app --port 8090
"""

from __future__ import annotations

from .backends import (
    EchoGuardBackend,
    GuardBackend,
    JudgeResult,
    MockGuardBackend,
    TransformersGuardBackend,
    build_backend,
    parse_guard_text,
)

__all__ = [
    "EchoGuardBackend",
    "GuardBackend",
    "JudgeResult",
    "MockGuardBackend",
    "TransformersGuardBackend",
    "build_backend",
    "parse_guard_text",
]
