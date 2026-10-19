"""``python -m l3_service``：用 uvicorn 起 L3 护栏服务。

环境变量：
- ``FLOWMASK_L3_MODE``：``mock`` / ``hf`` / ``echo`` / ``auto``（默认 auto：有本地权重就用真模型）
- ``FLOWMASK_L3_MODEL_DIR``：Qwen3Guard 权重目录（默认 ``server/models/qwen3guard-gen-0.6b``）
- ``FLOWMASK_L3_DEVICE``：``auto`` / ``cpu`` / ``cuda``
- ``FLOWMASK_L3_HOST`` / ``FLOWMASK_L3_PORT``：监听地址（默认 127.0.0.1:8092）
"""

from __future__ import annotations

import os

import uvicorn

from .app import app


def main() -> None:
    host = os.environ.get("FLOWMASK_L3_HOST", "127.0.0.1")
    port = int(os.environ.get("FLOWMASK_L3_PORT", "8092"))
    uvicorn.run(app, host=host, port=port, log_level=os.environ.get("FLOWMASK_L3_LOG_LEVEL", "info"))


if __name__ == "__main__":
    main()
