"""L1.5 NER 效果探针：量化 NER 在“没有字段名提示”的自由文本上的召回增益。

为什么单独做探针而不是塞进 ``eval/dataset.jsonl``：结构化评测集里姓名/地址大多带字段名
上下文（``name=``、``地址：``），L1 的 ``name_kv``/``address_kv`` 规则本来就能命中；
NER 的价值恰好在纯叙述文本（“张三昨天从福州赶过来……”）——这类样本进主评测集会把
“级别准确率”的语义搅乱，所以本脚本单独用人工叙述样本做**类型召回**对比。

用法（需要 NER 权重，模型不入库，见 server/models/ 说明）::

    cd server
    python -m ner_probe --model-dir models/bert4ner
    python -m ner_probe --model-dir models/bert4ner --markdown-out ../docs/ner探针报告.md

退出码：0 正常，2 没有可用 NER 检测器（缺权重/缺 torch）。
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

# 每条样本都是“人名/地址裸奔”的叙述文本；gold 只标 NER 应该补出来的类别
CASES: tuple[tuple[str, frozenset[str]], ...] = (
    ("张三昨天从福州赶过来，住在鼓楼区五四路附近，商量下季度的方案。", frozenset({"name", "address"})),
    ("李四让我转告王五，下周三在厦门大学开会，材料先发到 lisi@example.com。", frozenset({"name", "address", "email"})),
    ("客户王小明来电反馈，说他在泉州市丰泽区东海街道的仓库还没收到货。", frozenset({"name", "address"})),
    ("这份病历属于陈建国，诊断结果是 2 型糖尿病，联系电话 13800138000。", frozenset({"name", "medical", "phone"})),
    ("赵敏的身份证复印件落在家了，地址写的是南京市鼓楼区中山北路 88 号。", frozenset({"name", "address"})),
    ("周末和老刘约在福州鼓山爬山，他手机 13900139000。", frozenset({"name", "phone"})),
    ("快递收件人是周杰，地址福建省厦门市思明区环岛东路 1201 号。", frozenset({"name", "address"})),
    ("王芳在上海浦东新区张江高科上班，工号 8848。", frozenset({"name", "address"})),
)


@dataclass(frozen=True, slots=True)
class ProbeRow:
    """一条探针结果：开/关 NER 各自命中的类别与得分。"""

    text: str
    gold: frozenset[str]
    without_ner: frozenset[str]
    with_ner: frozenset[str]

    @property
    def without_hits(self) -> int:
        return len(self.gold & self.without_ner)

    @property
    def with_hits(self) -> int:
        return len(self.gold & self.with_ner)

    @property
    def improved(self) -> bool:
        return self.with_hits > self.without_hits


def _types_of(pipeline, text: str) -> frozenset[str]:
    """跑一次检测并取类别集合；没有命中（finding 为 None）就是空集。"""
    finding = pipeline.analyze_text(text, _options())
    if finding is None:
        return frozenset()
    return frozenset(finding.types)


def _options():
    from app.identify.pipeline import DetectOptions

    return DetectOptions()


def run_probe(
    *,
    ner_factory: Callable[[], object] | None = None,
) -> list[ProbeRow]:
    """跑全部探针样本。

    ``ner_factory`` 可注入（测试用假检测器，避免依赖真权重）；为空时按配置构建 NER。
    """
    from app.identify.pipeline import DetectionPipeline

    if ner_factory is None:
        from app.identify.ner import build_ner_detector

        def ner_factory():
            return build_ner_detector()

    detector = ner_factory()
    if detector is None:
        raise SystemExit("没有可用的 NER 检测器：请先设置 FLOWMASK_NER_MODEL_DIR 或传 --model-dir")

    plain = DetectionPipeline()
    with_ner = DetectionPipeline(ner=detector)
    rows: list[ProbeRow] = []
    for text, gold in CASES:
        rows.append(
            ProbeRow(
                text=text,
                gold=gold,
                without_ner=_types_of(plain, text),
                with_ner=_types_of(with_ner, text),
            )
        )
    return rows


def summarize(rows: Sequence[ProbeRow]) -> dict[str, object]:
    """汇总召回率：总命中/总标注，以及被 NER 提升的样本数。"""
    total = sum(len(row.gold) for row in rows)
    without = sum(row.without_hits for row in rows)
    with_ner = sum(row.with_hits for row in rows)
    return {
        "samples": len(rows),
        "total_expected": total,
        "without_ner_hits": without,
        "with_ner_hits": with_ner,
        "without_ner_recall": round(without / total, 4) if total else 0.0,
        "with_ner_recall": round(with_ner / total, 4) if total else 0.0,
        "improved_samples": sum(1 for row in rows if row.improved),
    }


def render(rows: Sequence[ProbeRow], summary: dict[str, object]) -> str:
    """输出可粘进报告/README 的 markdown 表。"""
    lines = [
        "# L1.5 NER 自由文本探针",
        "",
        f"> 样本 {summary['samples']} 条，标注类别 {summary['total_expected']} 个；"
        f"类型召回 **{summary['without_ner_recall'] * 100:.1f}% → {summary['with_ner_recall'] * 100:.1f}%**"
        f"（{summary['without_ner_hits']}/{summary['total_expected']} → "
        f"{summary['with_ner_hits']}/{summary['total_expected']}），"
        f"被 NER 提升的样本 {summary['improved_samples']}/{summary['samples']}。",
        "",
        "| # | 文本（截断） | 标注类别 | 无 NER | 有 NER |",
        "| --- | --- | --- | --- | --- |",
    ]
    for index, row in enumerate(rows, start=1):
        text = row.text if len(row.text) <= 24 else row.text[:24] + "…"
        lines.append(
            f"| {index} | {text} | {', '.join(sorted(row.gold))} | "
            f"{', '.join(sorted(row.without_ner)) or '—'} | {', '.join(sorted(row.with_ner)) or '—'} |"
        )
    lines.append("")
    lines.append("> 结论：字段名上下文缺失时，规则几乎抓不到人名/地址；NER 补的就是这块。")
    lines.append("> 默认仍关闭 NER（`FLOWMASK_ENABLE_NER=0`）：结构化/带字段名的数据上它只会引入多余类别。")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L1.5 NER 自由文本召回探针")
    parser.add_argument("--model-dir", default="", help="NER 模型目录（默认取 FLOWMASK_NER_MODEL_DIR）")
    parser.add_argument("--backend", default="token", help="NER 后端：token / raner")
    parser.add_argument("--device", default="cpu", help="推理设备：cpu / cuda")
    parser.add_argument("--markdown-out", default="", help="把 markdown 报告写到该路径")
    args = parser.parse_args(argv)

    if args.model_dir:
        import os

        os.environ["FLOWMASK_ENABLE_NER"] = "1"
        os.environ["FLOWMASK_NER_MODEL_DIR"] = args.model_dir
        os.environ["FLOWMASK_NER_BACKEND"] = args.backend
        os.environ["FLOWMASK_NER_DEVICE"] = args.device

    rows = run_probe()
    summary = summarize(rows)
    report = render(rows, summary)
    sys.stdout.write(report)
    if args.markdown_out:
        out = Path(args.markdown_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        sys.stdout.write(f"\n报告：{out}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
