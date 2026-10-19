"""评测：在自建标注集上算分级准确率、类别 P/R/F1、高危漏检率与时延。

用法（在 `server/` 目录下）::

    python -m app.evaluate --dataset eval/dataset.jsonl --report docs/评测报告.md

设计说明：

* 评测集是**自建标注集**（`eval/dataset.jsonl`），标签按需求 v2 的分级策略人工标注，
  用来衡量"实现与策略的一致性"以及"对困难样本（校验位错误、上下文干扰）的判别力"，
  不代表公开数据集上的绝对水平——报告里必须如实写清楚这一点。
* 默认只跑 L1（毫秒级、无需模型），保证评测在任何机器上可复现；加 ``--enable-l2/--enable-l3``
  才会升级到语义/大模型，用于对比"多模型协同"的增益与代价。
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .core.config import get_settings
from .identify.pipeline import DetectOptions, DetectionPipeline
from .identify.types import EntityType, Level
from .parsers import ParseError, parse_csv, parse_json, parse_sql, parse_text

LEVELS: tuple[str, ...] = ("L3", "L2", "L1", "SAFE")

_PARSERS: dict[str, Callable[[str], Any]] = {
    "text": parse_text,
    "json": parse_json,
    "csv": parse_csv,
    "sql": parse_sql,
}


@dataclass(frozen=True)
class DatasetItem:
    """评测集里的一条样本。"""

    id: str
    format: str
    content: str
    level: str
    types: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class Prediction:
    """一条样本的预测结果。"""

    level: str
    types: tuple[str, ...]
    latency_ms: float
    masked: str = ""
    # 解析失败（如纯 DDL 的 SQL）时记为 parsed=False：没有可检测内容，按 SAFE 计
    parsed: bool = True


def load_dataset(path: Path) -> list[DatasetItem]:
    """读取 JSONL 评测集；空行跳过，缺字段直接报错（避免静默少算样本）。"""
    items: list[DatasetItem] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:  # pragma: no cover - 数据文件坏了才走到
            raise ValueError(f"{path}:{line_no} 不是合法 JSON：{exc}") from exc
        level = str(record.get("level", "")).upper()
        if level not in LEVELS:
            raise ValueError(f"{path}:{line_no} 的 level 非法：{level!r}")
        fmt = str(record.get("format", "text")).lower()
        if fmt not in _PARSERS:
            raise ValueError(f"{path}:{line_no} 的 format 非法：{fmt!r}")
        items.append(
            DatasetItem(
                id=str(record.get("id", f"row-{line_no}")),
                format=fmt,
                content=str(record["content"]),
                level=level,
                types=tuple(str(t) for t in record.get("types", [])),
                note=str(record.get("note", "")),
            )
        )
    if not items:
        raise ValueError(f"评测集为空：{path}")
    return items


def predict(pipeline: DetectionPipeline, item: DatasetItem, options: DetectOptions) -> Prediction:
    """对单条样本跑一次检测，返回级别、类别集合与耗时。"""
    parser = _PARSERS[item.format]
    started = time.perf_counter()
    try:
        document = parser(item.content)
    except ParseError:
        # 解析不出可检测片段（例如只有 DDL 的 SQL）：不是检测失败，按"无敏感内容"计。
        return Prediction(level="SAFE", types=(), latency_ms=(time.perf_counter() - started) * 1000, parsed=False)
    report = pipeline.analyze_document(document, options)
    elapsed_ms = (time.perf_counter() - started) * 1000

    level = Level.SAFE
    types: set[str] = set()
    masked = ""
    for finding in report.findings:
        finding_level = Level.parse(finding.level.value if hasattr(finding.level, "value") else finding.level)
        if finding_level.severity > level.severity:
            level = finding_level
        for typ in finding.types:
            types.add(typ.value if hasattr(typ, "value") else str(typ))
        if not masked and finding.masked:
            masked = finding.masked
    return Prediction(level=level.value, types=tuple(sorted(types)), latency_ms=elapsed_ms, masked=masked)


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def _percentile(values: list[float], ratio: float) -> float:
    """线性插值分位数（样本量小，不引 numpy）。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * ratio
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def summarize(items: list[DatasetItem], predictions: list[Prediction]) -> dict[str, Any]:
    """把逐条预测汇总成指标（纯函数，便于单测）。"""
    if len(items) != len(predictions):
        raise ValueError("样本数与预测数不一致")

    confusion: dict[str, dict[str, int]] = {gold: {pred: 0 for pred in LEVELS} for gold in LEVELS}
    tp_v = fp_v = fn_v = 0
    type_stats: dict[str, dict[str, int]] = {
        typ.value: {"tp": 0, "fp": 0, "fn": 0} for typ in EntityType
    }
    critical_missed = 0
    safe_alarmed = 0
    latencies: list[float] = []

    for item, prediction in zip(items, predictions):
        confusion[item.level][prediction.level] += 1
        latencies.append(prediction.latency_ms)

        gold_types = set(item.types)
        predicted_types = set(prediction.types)
        tp_v += len(gold_types & predicted_types)
        fp_v += len(predicted_types - gold_types)
        fn_v += len(gold_types - predicted_types)
        for typ in EntityType:
            key = typ.value
            in_gold = key in gold_types
            in_pred = key in predicted_types
            if in_gold and in_pred:
                type_stats[key]["tp"] += 1
            elif in_pred:
                type_stats[key]["fp"] += 1
            elif in_gold:
                type_stats[key]["fn"] += 1

        if item.level == "L3" and Level.parse(prediction.level).severity < Level.MID.severity:
            critical_missed += 1
        if item.level == "SAFE" and prediction.level != "SAFE":
            safe_alarmed += 1

    total = len(items)
    correct = sum(confusion[level][level] for level in LEVELS)

    per_level: dict[str, dict[str, float]] = {}
    for level in LEVELS:
        tp = confusion[level][level]
        support = sum(confusion[level].values())
        predicted = sum(confusion[gold][level] for gold in LEVELS)
        precision, recall, f1 = _prf(tp, predicted - tp, support - tp)
        per_level[level] = {
            "support": support,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }
    macro_f1 = round(sum(per_level[level]["f1"] for level in LEVELS) / len(LEVELS), 4)

    precision_v, recall_v, f1_v = _prf(tp_v, fp_v, fn_v)
    per_type: dict[str, dict[str, float]] = {}
    for key, stats in type_stats.items():
        if stats["tp"] + stats["fp"] + stats["fn"] == 0:
            continue
        precision_t, recall_t, f1_t = _prf(stats["tp"], stats["fp"], stats["fn"])
        per_type[key] = {
            "support": stats["tp"] + stats["fn"],
            "precision": round(precision_t, 4),
            "recall": round(recall_t, 4),
            "f1": round(f1_t, 4),
        }

    parse_failed = sum(1 for prediction in predictions if not prediction.parsed)
    l3_support = sum(confusion["L3"].values()) or 1
    safe_support = sum(confusion["SAFE"].values()) or 1
    return {
        "total": total,
        "correct": correct,
        "accuracy": round(correct / total, 4),
        "macro_f1": macro_f1,
        "per_level": per_level,
        "confusion": confusion,
        "parse_failed": parse_failed,
        "type_micro": {
            "precision": round(precision_v, 4),
            "recall": round(recall_v, 4),
            "f1": round(f1_v, 4),
            "tp": tp_v,
            "fp": fp_v,
            "fn": fn_v,
        },
        "per_type": per_type,
        "critical_missed": critical_missed,
        "critical_miss_rate": round(critical_missed / l3_support, 4),
        "safe_alarmed": safe_alarmed,
        "false_positive_rate": round(safe_alarmed / safe_support, 4),
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 3) if latencies else 0.0,
            "p50": round(_percentile(latencies, 0.50), 3),
            "p95": round(_percentile(latencies, 0.95), 3),
            "max": round(max(latencies), 3) if latencies else 0.0,
        },
    }


def evaluate(
    dataset_path: Path,
    pipeline: DetectionPipeline | None = None,
    options: DetectOptions | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """跑完整评测，返回（汇总指标，逐条明细）。"""
    items = load_dataset(dataset_path)
    active_pipeline = pipeline or DetectionPipeline.from_settings(get_settings())
    active_options = options or DetectOptions()
    predictions = [predict(active_pipeline, item, active_options) for item in items]
    details = [
        {
            "id": item.id,
            "format": item.format,
            "note": item.note,
            "gold_level": item.level,
            "gold_types": list(item.types),
            "pred_level": prediction.level,
            "pred_types": list(prediction.types),
            "latency_ms": round(prediction.latency_ms, 3),
            "ok_level": item.level == prediction.level,
            "ok_types": set(item.types) == set(prediction.types),
            "parsed": prediction.parsed,
        }
        for item, prediction in zip(items, predictions)
    ]
    return summarize(items, predictions), details


def render_markdown(summary: dict[str, Any], details: list[dict[str, Any]], meta: dict[str, Any]) -> str:
    """把指标渲染成 Markdown 报告（写进 docs/ 供报告与 PPT 引用）。"""
    lines: list[str] = []
    lines.append("# FlowMask 评测报告（自建标注集）")
    lines.append("")
    lines.append(f"> 生成时间：{meta['generated_at']}　|　评测集：`{meta['dataset']}`（{summary['total']} 条）")
    lines.append(f"> 模式：{meta['mode']}　|　命令：`{meta['command']}`")
    lines.append("")
    lines.append("## 1. 总体指标")
    lines.append("")
    lines.append("| 指标 | 数值 | 说明 |")
    lines.append("| --- | ---: | --- |")
    lines.append(f"| 分级准确率 | **{summary['accuracy'] * 100:.1f}%** | {summary['correct']}/{summary['total']} 条级别完全一致 |")
    if summary.get("parse_failed"):
        lines.append(f"| 无可检测内容 | {summary['parse_failed']} 条 | 解析器未产出片段（如纯 DDL），按 SAFE 计 |")
    lines.append(f"| 分级 Macro-F1 | {summary['macro_f1']:.3f} | 四级各自 F1 的算术平均（类别不均衡时更保守） |")
    lines.append(
        f"| 类别 Micro-F1 | {summary['type_micro']['f1']:.3f} | 类别集合级：P={summary['type_micro']['precision']:.3f} / "
        f"R={summary['type_micro']['recall']:.3f} |"
    )
    lines.append(
        f"| 高危漏检率 | {summary['critical_miss_rate'] * 100:.1f}% | 标注 L3 却被判 SAFE/L1 的条数（{summary['critical_missed']} 条），安全产品最关键指标 |"
    )
    lines.append(
        f"| 安全误报率 | {summary['false_positive_rate'] * 100:.1f}% | 标注 SAFE 却被判 L1 及以上（{summary['safe_alarmed']} 条） |"
    )
    latency = summary["latency_ms"]
    lines.append(
        f"| 单条时延（L1） | p50 {latency['p50']:.2f} ms / p95 {latency['p95']:.2f} ms / max {latency['max']:.2f} ms | "
        f"均值 {latency['mean']:.2f} ms，含解析 + 识别 + 评分 + 脱敏 |"
    )
    lines.append("")
    lines.append("## 2. 四级混淆矩阵")
    lines.append("")
    header = "| 标注 \\ 预测 | " + " | ".join(LEVELS) + " |"
    lines.append(header)
    lines.append("| --- | " + " | ".join("---:" for _ in LEVELS) + " |")
    for gold in LEVELS:
        row = [str(summary["confusion"][gold][pred]) for pred in LEVELS]
        lines.append(f"| **{gold}** | " + " | ".join(row) + " |")
    lines.append("")
    lines.append("## 3. 分级 P/R/F1")
    lines.append("")
    lines.append("| 级别 | 支持数 | Precision | Recall | F1 |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for level in LEVELS:
        stats = summary["per_level"][level]
        lines.append(
            f"| {level} | {stats['support']} | {stats['precision']:.3f} | {stats['recall']:.3f} | {stats['f1']:.3f} |"
        )
    lines.append("")
    lines.append("## 4. 类别识别 P/R/F1")
    lines.append("")
    lines.append("| 类别 | 支持数 | Precision | Recall | F1 |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for key, stats in sorted(summary["per_type"].items(), key=lambda kv: (-kv[1]["support"], kv[0])):
        lines.append(
            f"| `{key}` | {stats['support']} | {stats['precision']:.3f} | {stats['recall']:.3f} | {stats['f1']:.3f} |"
        )
    lines.append("")
    lines.append("## 5. 判错的样本（供答辩与迭代）")
    lines.append("")
    wrong = [row for row in details if not row["ok_level"]]
    if not wrong:
        lines.append("本轮没有级别判错的样本。")
    else:
        lines.append("| 编号 | 标注 | 预测 | 标注类别 | 预测类别 | 样本说明 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for row in wrong:
            lines.append(
                f"| {row['id']} | {row['gold_level']} | {row['pred_level']} | "
                f"{', '.join(row['gold_types']) or '—'} | {', '.join(row['pred_types']) or '—'} | {row['note']} |"
            )
    lines.append("")
    lines.append("## 6. 口径与局限（如实说明）")
    lines.append("")
    lines.append(
        "- 评测集为**自建标注集**（`eval/dataset.jsonl`，人工标注 + 校验位生成的真实格式号码），"
        "标签遵循需求 v2 的分级策略；指标反映「实现与策略的一致性」与「困难样本判别力」，不等同于公开数据集上的绝对水平。"
    )
    lines.append(
        "- 默认只跑 **L1（规则 + 校验算法）**：不需要模型、任何机器可复现；L2/L3 的增益需用 "
        "`--enable-l2/--enable-l3` 在接入推理服务后单独对比。"
    )
    lines.append(
        "- 目前语料以中文业务文本/JSON/CSV/SQL 为主，样本量适中；扫描件 PDF、图片里的文字不在本轮范围。"
    )
    lines.append(
        "- 下一步：引入公开/半公开语料（政务公开数据、合成语料）扩充到千条级，并补 L2/L3 对照实验与消融。"
    )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FlowMask 自建评测集跑分")
    # 默认值按仓库布局推导：数据集在 server/eval/，报告放仓库根的 docs/（与设计文档同处）
    server_dir = Path(__file__).resolve().parents[1]
    repo_root = server_dir.parent
    parser.add_argument("--dataset", default=server_dir / "eval/dataset.jsonl", type=Path)
    parser.add_argument("--report", default=repo_root / "docs/评测报告.md", type=Path)
    parser.add_argument("--json-out", default=server_dir / "eval/latest.json", type=Path)
    parser.add_argument("--enable-l2", action="store_true")
    parser.add_argument("--enable-l3", action="store_true")
    args = parser.parse_args(argv)

    options = DetectOptions(enable_l2=args.enable_l2, enable_l3=args.enable_l3)
    summary, details = evaluate(args.dataset, options=options)

    from datetime import datetime

    meta = {
        "generated_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M"),
        "dataset": str(args.dataset),
        "mode": "L1 + L2 + L3" if args.enable_l2 and args.enable_l3 else ("L1 + L2" if args.enable_l2 else "L1 only"),
        "command": "cd server && python -m app.evaluate",
    }
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps({"meta": meta, "summary": summary, "details": details}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_markdown(summary, details, meta), encoding="utf-8")

    print(f"样本 {summary['total']} 条 | 准确率 {summary['accuracy'] * 100:.1f}% | "
          f"类别 Micro-F1 {summary['type_micro']['f1']:.3f} | "
          f"高危漏检率 {summary['critical_miss_rate'] * 100:.1f}% | "
          f"L1 p95 {summary['latency_ms']['p95']:.2f} ms")
    print(f"报告：{args.report}　明细：{args.json_out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
