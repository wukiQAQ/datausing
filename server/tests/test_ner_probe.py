"""``ner_probe`` 的单元测试：用假检测器验证汇总/渲染/主流程，不依赖真权重。"""

from __future__ import annotations

import pytest

from ner_probe import CASES, ProbeRow, render, run_probe, summarize
from app.identify.ner import NerSpan

# 探针样本里会出现的人名/地名，假检测器按“字面出现”返回区间
_NAMES = ("张三", "李四", "王五", "王小明", "陈建国", "赵敏", "老刘", "周杰", "王芳")
_PLACES = ("福州", "鼓楼区", "五四路", "厦门大学", "泉州市", "丰泽区", "东海街道", "南京市", "厦门市", "思明区", "上海", "浦东新区")


class FakeNer:
    """不加载任何模型：按字面扫人名/地名，模拟 NER 在自由文本上的输出。"""

    name = "fake-ner"

    def available(self) -> bool:
        return True

    def detect(self, text: str) -> list[NerSpan]:
        spans: list[NerSpan] = []
        for token in (*_NAMES, *_PLACES):
            start = text.find(token)
            if start >= 0:
                label = "B-PER" if token in _NAMES else "B-LOC"
                spans.append(NerSpan(start=start, end=start + len(token), label=label))
        return spans


def test_run_probe_with_fake_detector_improves_recall() -> None:
    """假 NER 应该把「规则抓不到的人名/地址」补上，召回明显提升。"""
    rows = run_probe(ner_factory=lambda: FakeNer())

    assert len(rows) == len(CASES)
    summary = summarize(rows)
    assert summary["samples"] == len(CASES)
    assert summary["total_expected"] == sum(len(gold) for _, gold in CASES)
    assert summary["with_ner_hits"] > summary["without_ner_hits"]
    assert summary["with_ner_recall"] > summary["without_ner_recall"]

    # 纯叙述、没有字段名的样本必须被 NER 救回来
    first = rows[0]
    assert "name" in first.with_ner and "name" not in first.without_ner
    assert first.improved is True


def test_run_probe_without_detector_exits() -> None:
    """没有可用检测器时给出可照做的退出，而不是跑出一堆空结果。"""
    with pytest.raises(SystemExit) as excinfo:
        run_probe(ner_factory=lambda: None)

    assert "NER" in str(excinfo.value.code)


def test_summarize_math() -> None:
    """汇总口径：召回 = 命中标注数 / 标注总数，四舍五入到 4 位。"""
    rows = [
        ProbeRow(
            text="t1",
            gold=frozenset({"name", "address"}),
            without_ner=frozenset(),
            with_ner=frozenset({"name", "address"}),
        ),
        ProbeRow(
            text="t2",
            gold=frozenset({"name"}),
            without_ner=frozenset({"name"}),
            with_ner=frozenset({"name"}),
        ),
    ]

    summary = summarize(rows)

    assert summary["total_expected"] == 3
    assert summary["without_ner_hits"] == 1
    assert summary["with_ner_hits"] == 3
    assert summary["without_ner_recall"] == round(1 / 3, 4)
    assert summary["with_ner_recall"] == 1.0
    assert summary["improved_samples"] == 1


def test_render_contains_table_and_conclusion() -> None:
    """渲染结果要能直接粘进报告：有表头、有召回结论、结论里说明默认关闭。"""
    rows = run_probe(ner_factory=lambda: FakeNer())
    table = render(rows, summarize(rows))

    assert table.startswith("# L1.5 NER 自由文本探针")
    assert "| # | 文本（截断） | 标注类别 | 无 NER | 有 NER |" in table
    assert "类型召回" in table
    assert "FLOWMASK_ENABLE_NER=0" in table


def test_cases_cover_name_and_place() -> None:
    """样本集本身要覆盖人名与地址两类缺口，否则探针失去意义。"""
    golds = set().union(*(gold for _, gold in CASES))

    assert {"name", "address"} <= golds
