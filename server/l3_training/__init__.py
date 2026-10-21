"""L3 裁判指令微调：把评测集金标变成指令样本，LoRA 微调 Qwen2.5-Instruct。

与 ``l2_training`` 的分工：
- L2 训练的是"这段文本有没有敏感信息、属于哪几类"的多标签分类器；
- L3 训练的是"分级 + 类别 + 理由"的裁判模型，输出一行 JSON，
  与 ``l3_service`` 的 instruct 模式共用同一段 prompt 与同一套字段。
"""

from .dataset import build_dataset, build_samples, sample_from_row, split_samples, summarize
from .labels import (
    CATEGORIES,
    CATEGORY_LABELS,
    LEVELS,
    LEVEL_HINTS,
    categories_from_row,
    level_from_row,
    parse_target,
    reason_for,
    target_json,
)
from .train import dry_run, load_samples, merge_adapter, train

__all__ = [
    "CATEGORIES",
    "CATEGORY_LABELS",
    "LEVELS",
    "LEVEL_HINTS",
    "build_dataset",
    "build_samples",
    "categories_from_row",
    "dry_run",
    "level_from_row",
    "load_samples",
    "merge_adapter",
    "parse_target",
    "reason_for",
    "sample_from_row",
    "split_samples",
    "summarize",
    "target_json",
    "train",
]
