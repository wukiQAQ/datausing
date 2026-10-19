"""L2 训练侧：标签体系、数据构造、RoBERTa/BERT 多标签微调。

与运行时 ``l2_service`` 的分工：
- 这里产出 HuggingFace 格式模型目录（含 ``labels.json``）；
- ``python -m l2_service.export_onnx`` 把它压成 int8 ONNX；
- ``l2_service`` 以 ``FLOWMASK_L2_MODE=onnx`` 加载，其余调用方无感。
"""

from .dataset import (
    Example,
    from_eval_dataset,
    load_jsonl,
    make_bank_card,
    make_id_card,
    split_examples,
    synthetic_examples,
    write_jsonl,
)
from .labels import BENIGN, LABELS, SENSITIVE_LABELS, WeakLabel, weak_label
from .train import dry_run, train

__all__ = [
    "BENIGN",
    "LABELS",
    "SENSITIVE_LABELS",
    "Example",
    "WeakLabel",
    "dry_run",
    "from_eval_dataset",
    "load_jsonl",
    "make_bank_card",
    "make_id_card",
    "split_examples",
    "synthetic_examples",
    "train",
    "weak_label",
    "write_jsonl",
]
