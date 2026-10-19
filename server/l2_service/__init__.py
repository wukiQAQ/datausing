"""L2 语义识别推理服务（独立进程）。

设计动机：模型（tokenizer + ONNX 权重）体积大、依赖重（onnxruntime/torch），
不适合塞进 FastAPI 一体机；把它拆成独立服务后：

- 一体机没有模型也能跑 L1-only，答辩现场可以把 L2 服务启起来当场演示升级链路；
- 服务挂了/超时，一体机按 ``degraded`` 降级，不影响主链路；
- 契约稳定（``POST /v1/classify``），换成真 RoBERTa 权重时一体机代码一行不改。

当前默认后端为 ``mock``（词表+启发式，"语义"的占位实现，用于跑通链路与做增益对比），
真实模型后端见 ``OnnxBackend`` 与 ``README.md`` 的训练/导出步骤。
"""

from __future__ import annotations

__version__ = "0.1.0"
