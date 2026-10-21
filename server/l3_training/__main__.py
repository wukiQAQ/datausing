"""命令行入口：数据集构建 / 体检 / LoRA 训练 / 权重合并。

用法（在 ``server/`` 目录下）：
    python -m l3_training dataset          # 评测集 → data/l3_training.jsonl
    python -m l3_training inspect          # 样本画像（不需要 torch）
    python -m l3_training dry-run          # 数据体检（不需要 torch）
    python -m l3_training train --epochs 2 # LoRA 微调（需要 torch/peft）
    python -m l3_training merge            # adapter → 完整模型目录，给 l3_service 用
"""

from __future__ import annotations

import argparse
import json
import sys

from .dataset import DEFAULT_EVAL, DEFAULT_OUT
from .train import DEFAULT_BASE, DEFAULT_DATASET, DEFAULT_OUT as DEFAULT_ADAPTER, dry_run, merge_adapter, train


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="l3_training", description="L3 裁判指令微调")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("dataset", help="从评测集构建指令微调样本")
    build.add_argument("--eval", default=str(DEFAULT_EVAL), help="评测集 JSONL")
    build.add_argument("--out", default=str(DEFAULT_OUT), help="输出 JSONL")
    build.add_argument("--val-ratio", type=float, default=0.1)
    build.add_argument("--seed", type=int, default=42)

    inspect = sub.add_parser("inspect", help="打印样本画像")
    inspect.add_argument("--dataset", default=str(DEFAULT_OUT))

    check = sub.add_parser("dry-run", help="不依赖 torch 的数据体检")
    check.add_argument("--dataset", default=str(DEFAULT_OUT))
    check.add_argument("--min-rows", type=int, default=50)

    fit = sub.add_parser("train", help="LoRA 微调（需要 torch/transformers/peft）")
    fit.add_argument("--dataset", default=str(DEFAULT_OUT))
    fit.add_argument("--base-model", default=DEFAULT_BASE)
    fit.add_argument("--out", default=str(DEFAULT_ADAPTER))
    fit.add_argument("--epochs", type=int, default=2)
    fit.add_argument("--lr", type=float, default=1e-4)
    fit.add_argument("--batch-size", type=int, default=1)
    fit.add_argument("--grad-accum", type=int, default=8)
    fit.add_argument("--max-length", type=int, default=1024)
    fit.add_argument("--lora-r", type=int, default=8)
    fit.add_argument("--lora-alpha", type=int, default=16)
    fit.add_argument("--lora-dropout", type=float, default=0.05)
    fit.add_argument("--device", default="cuda", help="cuda / cpu")
    fit.add_argument("--seed", type=int, default=42)

    merge = sub.add_parser("merge", help="合并 LoRA adapter 成完整模型目录")
    merge.add_argument("--base-model", default=DEFAULT_BASE)
    merge.add_argument("--adapter", default=str(DEFAULT_ADAPTER))
    merge.add_argument("--out", default="", help="默认 <adapter>-merged")

    args = parser.parse_args(argv)

    if args.command == "dataset":
        from .dataset import build_dataset

        stats = build_dataset(
            args.out, eval_path=args.eval, val_ratio=args.val_ratio, seed=args.seed
        )
        _print(stats)
        return 0
    if args.command == "inspect":
        from .dataset import summarize

        _print(summarize(_load(args.dataset)))
        return 0
    if args.command == "dry-run":
        stats = dry_run(args.dataset, min_rows=args.min_rows)
        _print(stats)
        return 0 if stats["ready"] else 1
    if args.command == "train":
        metrics = train(
            args.dataset,
            args.out,
            base_model=args.base_model,
            epochs=args.epochs,
            lr=args.lr,
            batch_size=args.batch_size,
            grad_accum=args.grad_accum,
            max_length=args.max_length,
            lora_r=args.lora_r,
            lora_alpha=args.lora_alpha,
            lora_dropout=args.lora_dropout,
            device=args.device,
            seed=args.seed,
        )
        _print(metrics)
        return 0
    if args.command == "merge":
        target = merge_adapter(args.base_model, args.adapter, args.out or None)
        _print({"merged_dir": str(target)})
        return 0
    parser.error(f"未知命令：{args.command}")
    return 2


def _load(path: str) -> list[dict]:
    """inspect 用：复用 train.load_samples 的读取逻辑。"""
    from .train import load_samples

    return load_samples(path)


if __name__ == "__main__":
    sys.exit(main())
