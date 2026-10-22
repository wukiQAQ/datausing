"""``python -m l2_training``：造数据 → 体检 → 训练。"""

from __future__ import annotations

import argparse
import json
import sys

from .dataset import (
    from_eval_dataset,
    from_weibo_ner,
    load_jsonl,
    synthetic_examples,
    write_jsonl,
)
from .train import dry_run, train


def _cmd_dataset(args: argparse.Namespace) -> int:
    examples = synthetic_examples(count=args.count, seed=args.seed)
    sources = {"synthetic": len(examples)}
    if args.with_eval:
        extra = from_eval_dataset(args.with_eval)
        examples.extend(extra)
        sources["eval"] = len(extra)
    if args.with_weibo:
        weibo = from_weibo_ner(args.with_weibo, limit=args.weibo_limit, seed=args.seed)
        examples.extend(weibo)
        sources["weibo-ner"] = len(weibo)
    path = write_jsonl(args.out, examples)
    print(json.dumps({"out": str(path), "total": len(examples), "sources": sources}, ensure_ascii=False))
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    stats = dry_run(args.dataset, min_rows=args.min_rows)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0 if stats["ready"] else 1


def _cmd_train(args: argparse.Namespace) -> int:
    try:
        report = train(
            args.dataset,
            out_dir=args.out,
            base_model=args.base_model,
            epochs=args.epochs,
            batch_size=args.batch_size,
            lr=args.lr,
            max_length=args.max_length,
            val_ratio=args.val_ratio,
            seed=args.seed,
        )
    except RuntimeError as exc:  # 缺依赖时给出可照做的提示，而不是堆栈
        print(f"[l2_training] {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report["best"], ensure_ascii=False, indent=2))
    print(f"模型已保存到 {args.out}；下一步：python -m l2_service.export_onnx --model-dir {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="l2_training", description="FlowMask L2 语义分类器训练工具")
    sub = parser.add_subparsers(dest="command", required=True)

    dataset = sub.add_parser("dataset", help="生成自举训练集（可选合并评测集）")
    dataset.add_argument("--out", default="data/l2_training.jsonl")
    dataset.add_argument("--count", type=int, default=800)
    dataset.add_argument("--seed", type=int, default=42)
    dataset.add_argument(
        "--with-eval",
        default="",
        help="合并人工评测集（仅用于调参实验）；默认不合并，避免评测集泄漏进训练集",
    )
    dataset.add_argument(
        "--with-weibo",
        default="",
        help="合并公开语料 weibo NER（JSON 数组，含 tokens/tags）；用它补叙述型自由文本的 name/address 正例",
    )
    dataset.add_argument(
        "--weibo-limit", type=int, default=400, help="从公开语料里最多取多少句（默认 400）"
    )
    dataset.set_defaults(func=_cmd_dataset)

    inspect = sub.add_parser("inspect", help="不依赖 torch 的数据体检")
    inspect.add_argument("--dataset", default="data/l2_training.jsonl")
    inspect.add_argument("--min-rows", type=int, default=20)
    inspect.set_defaults(func=_cmd_inspect)

    train_cmd = sub.add_parser("train", help="微调 BERT/RoBERTa（需要 torch + transformers）")
    train_cmd.add_argument("--dataset", default="data/l2_training.jsonl")
    train_cmd.add_argument("--out", default="l2_models/roberta-wwm-sens")
    train_cmd.add_argument("--base-model", default="bert-base-chinese")
    train_cmd.add_argument("--epochs", type=int, default=3)
    train_cmd.add_argument("--batch-size", type=int, default=16)
    train_cmd.add_argument("--lr", type=float, default=2e-5)
    train_cmd.add_argument("--max-length", type=int, default=128)
    train_cmd.add_argument("--val-ratio", type=float, default=0.15)
    train_cmd.add_argument("--seed", type=int, default=42)
    train_cmd.set_defaults(func=_cmd_train)
    return parser


def _normalize(argv: list[str]) -> list[str]:
    """允许 ``--with-eval ""`` 表示不合并评测集。"""
    return ["" if item == '""' else item for item in argv]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(_normalize(list(sys.argv[1:] if argv is None else argv)))
    if args.command == "dataset" and not args.with_eval:
        args.with_eval = None
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
