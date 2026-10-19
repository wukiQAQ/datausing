"""评测脚本测试：数据集读取、比对模式、报告小节生成。"""

from __future__ import annotations

import json

from app.evaluate import build_pipeline, evaluate, load_dataset, main
from app.identify.pipeline import DetectOptions


def _write_dataset(tmp_path) -> "object":
    rows = [
        {"id": "REAL-1", "format": "text", "level": "L3", "types": ["id_card"],
         "content": "身份证 11010519491231002X", "note": "真实证件"},
        {"id": "DEMO-1", "format": "text", "level": "SAFE", "types": [],
         "content": "示例：手机号 13800138000 仅用于联调测试", "note": "文档示例"},
    ]
    path = tmp_path / "dataset.jsonl"
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    return path


def test_load_dataset_and_predict(tmp_path) -> None:
    path = _write_dataset(tmp_path)
    items = load_dataset(path)
    assert [item.id for item in items] == ["REAL-1", "DEMO-1"]
    summary, details = evaluate(path, options=DetectOptions())
    assert summary["total"] == 2
    assert len(details) == 2
    # 纯 L1 会被"文档示例里的号码"骗到：这是加入 L2 前应有的表现
    assert summary["false_positive_rate"] > 0


def test_mock_l2_pipeline_suppresses_benign_examples(tmp_path) -> None:
    path = _write_dataset(tmp_path)
    summary, _ = evaluate(
        path, options=DetectOptions(enable_l2=True), pipeline=build_pipeline(use_mock_l2=True)
    )
    assert summary["false_positive_rate"] == 0
    assert summary["critical_miss_rate"] == 0


def test_compare_l2_writes_comparison_section(tmp_path) -> None:
    dataset = _write_dataset(tmp_path)
    report = tmp_path / "report.md"
    json_out = tmp_path / "latest.json"
    code = main([
        "--dataset", str(dataset),
        "--report", str(report),
        "--json-out", str(json_out),
        "--compare-l2",
    ])
    assert code == 0
    text = report.read_text(encoding="utf-8")
    assert "多模型协同对比" in text
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    compare = payload["meta"]["compare"]
    assert compare["l2"]["false_positive_rate"] <= compare["l1"]["false_positive_rate"]
