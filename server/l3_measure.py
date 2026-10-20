"""L3 复核策略对比工具：同一批评测样本、同一个 L3 端点，只换策略口径。

背景：Qwen3Guard 这类"内容安全护栏"对真实 PII 只给 ``Controversial``，
按"只认 Unsafe 升级"的保守口径，L3 对分级没有任何贡献。本工具用来量化
三种新口径的收益与代价，避免凭感觉上线：

1. ``baseline``     —— 只认 Unsafe（原口径）；
2. ``category``     —— 模型点出 PII 类别时，把低危抬到中危；
3. ``hint``         —— 采信指令模型直接给出的分级建议（只升不降、默认封顶 L2）；
4. ``hint+category``—— 两者叠加；
5. ``hint-l3``      —— 允许指令模型把样本抬到高危（``FLOWMASK_L3_MAX_TARGET=L3``）。

用法（先起 L3 服务）：

    python -m l3_measure --endpoint http://127.0.0.1:8092/v1/judge
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from app.evaluate import HttpJudgeAdapter, HttpSemanticAdapter, evaluate, load_dataset
from app.identify.pipeline import DetectOptions, DetectionPipeline

VARIANTS: tuple[tuple[str, dict[str, str]], ...] = (
    ("baseline（只认 Unsafe）", {"FLOWMASK_L3_CATEGORY_RAISE": "0", "FLOWMASK_L3_TRUST_LEVEL_HINT": "0"}),
    ("类别感知（PII → 至少中危）", {"FLOWMASK_L3_CATEGORY_RAISE": "1", "FLOWMASK_L3_TRUST_LEVEL_HINT": "0"}),
    ("采信分级建议（封顶 L2）", {"FLOWMASK_L3_CATEGORY_RAISE": "0", "FLOWMASK_L3_TRUST_LEVEL_HINT": "1"}),
    ("类别感知 + 分级建议", {"FLOWMASK_L3_CATEGORY_RAISE": "1", "FLOWMASK_L3_TRUST_LEVEL_HINT": "1"}),
    ("分级建议 · 允许抬到 L3", {"FLOWMASK_L3_CATEGORY_RAISE": "1", "FLOWMASK_L3_TRUST_LEVEL_HINT": "1", "FLOWMASK_L3_MAX_TARGET": "L3"}),
)


def _apply_env(overrides: dict[str, str]) -> dict[str, str | None]:
    """设置策略开关，返回旧值以便恢复。"""
    saved: dict[str, str | None] = {}
    for key in ("FLOWMASK_L3_CATEGORY_RAISE", "FLOWMASK_L3_TRUST_LEVEL_HINT", "FLOWMASK_L3_MAX_TARGET"):
        saved[key] = os.environ.get(key)
        if key in overrides:
            os.environ[key] = overrides[key]
        else:
            os.environ.pop(key, None)
    for key, value in overrides.items():
        os.environ[key] = value
    return saved


def _restore(saved: dict[str, str | None]) -> None:
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def run_variant(
    dataset: Path, endpoint: str, l2_endpoint: str, overrides: dict[str, str]
) -> dict[str, Any]:
    """按一组策略开关跑完整评测集，返回汇总与判错样本。"""
    saved = _apply_env(overrides)
    try:
        pipeline = DetectionPipeline(
            guard=HttpJudgeAdapter(endpoint),
            semantic=HttpSemanticAdapter(l2_endpoint) if l2_endpoint else None,
        )
        summary, details = evaluate(
            dataset,
            options=DetectOptions(enable_l2=bool(l2_endpoint), enable_l3=True),
            pipeline=pipeline,
        )
    finally:
        _restore(saved)
    wrong = [row for row in details if not row["ok_level"]]
    levels = [row["pred_level"] for row in details]
    return {
        "accuracy": summary["accuracy"],
        "macro_f1": summary["macro_f1"],
        "type_micro_f1": summary["type_micro"]["f1"],
        "false_positive_rate": summary["false_positive_rate"],
        "critical_miss_rate": summary["critical_miss_rate"],
        "wrong": wrong,
        "raised_to_l3": sum(1 for level in levels if level == "L3"),
    }


def render(results: list[tuple[str, dict[str, Any]]]) -> str:
    lines = [
        "| 口径 | 准确率 | Macro-F1 | 类别 Micro-F1 | 安全误报率 | 高危漏检率 | 判错 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, row in results:
        lines.append(
            f"| {name} | {row['accuracy'] * 100:.1f}% | {row['macro_f1']:.3f} | "
            f"{row['type_micro_f1']:.3f} | {row['false_positive_rate'] * 100:.1f}% | "
            f"{row['critical_miss_rate'] * 100:.1f}% | {len(row['wrong'])} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    server_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="L3 复核策略对比（同一端点、同一数据集）")
    parser.add_argument("--dataset", type=Path, default=server_dir / "eval/dataset.jsonl")
    parser.add_argument(
        "--endpoint", default="http://127.0.0.1:8092/v1/judge", help="L3 服务地址"
    )
    parser.add_argument(
        "--l2-endpoint",
        default="http://127.0.0.1:8091/v1/classify",
        help="L2 服务地址；传空串表示只用 L1+L3",
    )
    args = parser.parse_args(argv)

    rows = load_dataset(args.dataset)
    print(f"数据集：{args.dataset}（{len(rows)} 条）")
    print(f"L2 端点：{args.l2_endpoint or "（不用 L2）"}　L3 端点：{args.endpoint}")
    results = []
    for name, overrides in VARIANTS:
        result = run_variant(args.dataset, args.endpoint, args.l2_endpoint, overrides)
        results.append((name, result))
        wrong_ids = ", ".join(row["id"] for row in result["wrong"]) or "—"
        print(f"- {name}: 准确率 {result['accuracy'] * 100:.1f}%，判错：{wrong_ids}")
    print()
    print(render(results))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
