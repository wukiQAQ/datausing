"""``python -m l2_service`` 启动入口：读环境变量后跑 uvicorn。

    FLOWMASK_L2_HOST    监听地址，默认 127.0.0.1（只对本机开放，数据不出机）
    FLOWMASK_L2_PORT    监听端口，默认 8091
    FLOWMASK_L2_MODE    mock（默认）| onnx | auto | echo
    FLOWMASK_L2_MODEL_DIR  onnx 模式下的模型目录，默认 models/roberta-wwm-sens
"""

from __future__ import annotations

import os


def main() -> int:
    import uvicorn  # 延迟导入：--help/单测环境不需要

    host = os.environ.get("FLOWMASK_L2_HOST", "127.0.0.1")
    port = int(os.environ.get("FLOWMASK_L2_PORT", "8091"))
    uvicorn.run("l2_service.app:app", host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
