"""性能基准脚本的单元测试：只验证统计口径与流程，不依赖模型权重。

真实的 L2/L3 数字靠 ``python -m bench`` 现场跑（需要 ONNX 模型与推理服务），
这里只保证：
- 分位数/汇总的数学是对的；
- L1、进程内 /detect 两条链路能跑通并产出结构正确的行；
- markdown 报告的关键结构稳定（防止文档生成写坏）。
"""

from __future__ import annotations

import bench


def test_percentile_interpolation() -> None:
    assert bench.percentile([], 95) == 0.0
    assert bench.percentile([5.0], 95) == 5.0
    assert bench.percentile([1, 2, 3, 4], 50) == 2.5
    assert bench.percentile([1, 2, 3, 4], 0) == 1.0
    assert bench.percentile([1, 2, 3, 4], 100) == 4.0


def test_summarize_fields() -> None:
    row = bench.summarize("demo", [10.0, 20.0, 30.0])
    assert row["name"] == "demo"
    assert row["count"] == 3
    assert row["mean_ms"] == 20.0
    assert row["p50_ms"] == 20.0
    assert row["max_ms"] == 30.0
    assert row["throughput_per_s"] == 50.0


def test_bench_l1_runs_and_measures() -> None:
    row = bench.bench_l1(iterations=50)
    assert row["count"] == 50
    assert row["mean_ms"] > 0
    assert row["throughput_per_s"] > 0


def test_bench_detect_runs_small() -> None:
    row = bench.bench_detect(iterations=5)
    assert row["count"] == 5
    assert row["p95_ms"] > 0


def test_markdown_render_contains_table() -> None:
    rows = [bench.summarize("L1 demo", [1.0, 2.0])]
    markdown = bench.render_markdown(rows, bench._machine())
    assert "# FlowMask 性能报告" in markdown
    assert "| L1 demo |" in markdown
    assert "口径与边界" in markdown
