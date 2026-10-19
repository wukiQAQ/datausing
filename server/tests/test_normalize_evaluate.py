"""归一化与评测指标的单测：这两块是"数字可信"的地基，必须自己先被测。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.evaluate import DatasetItem, Prediction, load_dataset, summarize
from app.identify.normalize import map_hit_span, normalize, normalize_with_map

# ---------------- 归一化 ----------------


def test_normalize_fullwidth_digits_and_at() -> None:
    assert normalize("１３８００１３８０００") == "13800138000"
    assert normalize("zhang＠example.com") == "zhang@example.com"
    assert normalize("ＡＢＣ－１２３") == "ABC-123"  # 连字符两侧不是数字，保留


def test_normalize_strips_separators_between_digits_only() -> None:
    assert normalize("138-0013-8000") == "13800138000"
    assert normalize("6222 0212 3456 7894") == "6222021234567894"
    assert normalize("110105 19491231 002X") == "11010519491231002X"
    # 词内连字符与正常空格不能被吃掉
    assert normalize("zhang-san 您好") == "zhang-san 您好"


def test_normalize_map_points_back_to_original_span() -> None:
    text = "手机 138-0013-8000 请联系"
    normalized, index_map = normalize_with_map(text)
    assert normalized == "手机 13800138000 请联系"
    start = normalized.index("13800138000")
    span = map_hit_span(index_map, start, start + len("13800138000"), len(text))
    assert span is not None
    original_start, original_end = span
    assert text[original_start:original_end] == "138-0013-8000"  # 含分隔符，整体可脱敏


def test_normalize_no_change_keeps_identity_map() -> None:
    text = "普通文本 abc"
    normalized, index_map = normalize_with_map(text)
    assert normalized == text
    assert index_map == list(range(len(text)))


def test_map_hit_span_rejects_out_of_range() -> None:
    _, index_map = normalize_with_map("abc")
    assert map_hit_span(index_map, 5, 8, 3) is None
    assert map_hit_span(index_map, 2, 2, 3) is None


# ---------------- 评测度量 ----------------


def _item(id_: str, level: str, types: tuple[str, ...] = ()) -> DatasetItem:
    return DatasetItem(id=id_, format="text", content="x", level=level, types=types)


def _pred(level: str, types: tuple[str, ...] = (), latency: float = 1.0) -> Prediction:
    return Prediction(level=level, types=types, latency_ms=latency)


def test_summarize_confusion_and_rates() -> None:
    items = [
        _item("a", "L3", ("phone",)),
        _item("b", "L3", ("phone",)),
        _item("c", "L2", ("email",)),
        _item("d", "SAFE"),
    ]
    preds = [
        _pred("L3", ("phone",)),
        _pred("SAFE"),  # 高危漏检
        _pred("L2", ("email",)),
        _pred("L1", ("ip",)),  # 安全误报
    ]
    summary = summarize(items, preds)
    assert summary["total"] == 4
    assert summary["correct"] == 2
    assert summary["accuracy"] == 0.5
    assert summary["critical_missed"] == 1
    assert summary["critical_miss_rate"] == 0.5
    assert summary["safe_alarmed"] == 1
    assert summary["false_positive_rate"] == 1.0
    # 类别集合级：phone 命中 1/2，email 全中，另有 ip 误报
    assert summary["type_micro"]["tp"] == 2
    assert summary["type_micro"]["fp"] == 1
    assert summary["type_micro"]["fn"] == 1  # 第二条的 phone 漏检


def test_summarize_latency_percentiles() -> None:
    items = [_item(str(i), "SAFE") for i in range(5)]
    preds = [_pred("SAFE", latency=value) for value in (1.0, 2.0, 3.0, 4.0, 10.0)]
    summary = summarize(items, preds)
    assert summary["latency_ms"]["mean"] == 4.0
    assert summary["latency_ms"]["p50"] == 3.0
    assert summary["latency_ms"]["p95"] == 8.8
    assert summary["latency_ms"]["max"] == 10.0


def test_summarize_rejects_length_mismatch() -> None:
    with pytest.raises(ValueError):
        summarize([_item("a", "SAFE")], [])


def test_load_dataset_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "dataset.jsonl"
    records = [
        {"id": "x-1", "format": "json", "content": '{"a":"b"}', "level": "L1", "types": ["email"], "note": "n"},
        {"id": "x-2", "format": "text", "content": "hello", "level": "SAFE"},
    ]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
    items = load_dataset(path)
    assert [item.id for item in items] == ["x-1", "x-2"]
    assert items[0].types == ("email",)
    assert items[1].types == ()


def test_load_dataset_rejects_bad_level(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"id":"x","format":"text","content":"x","level":"L9"}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_dataset(path)
