"""L3 训练侧测试：标签契约、样本构建、划分、体检与「缺依赖」提示。

这些用例**不依赖 torch/peft**：训练循环本身只做静态检查，
真正跑一遍微调由 ``l3_training dry-run`` 与人工训练负责。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import importlib

import pytest

# 注意：包里把 ``train`` 导成了函数，``import l3_training.train as m`` 会拿到函数，
# 所以统一走 importlib 取真正的子模块。
dataset_mod = importlib.import_module("l3_training.dataset")
labels_mod = importlib.import_module("l3_training.labels")
train_mod = importlib.import_module("l3_training.train")

SERVER_DIR = Path(__file__).resolve().parents[1]
EVAL_PATH = SERVER_DIR / "eval" / "dataset.jsonl"


def test_levels_and_categories_are_fixed():
    assert labels_mod.LEVELS == ("L3", "L2", "L1", "SAFE")
    assert len(labels_mod.CATEGORIES) == 10
    assert set(labels_mod.CATEGORY_LABELS) == set(labels_mod.CATEGORIES)


def test_target_json_orders_categories_and_validates():
    line = labels_mod.target_json("L2", ["address", "phone"], "命中地址、手机号")
    assert line == '{"level":"L2","categories":["phone","address"],"reason":"命中地址、手机号"}'
    with pytest.raises(ValueError):
        labels_mod.target_json("L9", ["phone"], "x")
    with pytest.raises(ValueError):
        labels_mod.target_json("L1", ["face"], "x")


def test_parse_target_roundtrip_and_garbage():
    line = labels_mod.target_json("L3", ["id_card"], "含身份证号")
    parsed = labels_mod.parse_target(line)
    assert parsed == {"level": "L3", "categories": ["id_card"], "reason": "含身份证号"}
    # 允许被代码块/解释包裹
    assert labels_mod.parse_target("```json\n" + line + "\n```")["level"] == "L3"
    # 类别大小写、字符串写法都能归一
    parsed = labels_mod.parse_target('{"level":"l1","categories":"Name, address","reason":"r"}')
    assert parsed == {"level": "L1", "categories": ["name", "address"], "reason": "r"}
    assert labels_mod.parse_target("我觉得这是 L3") is None
    assert labels_mod.parse_target('{"level":"X","categories":[]}') is None


def test_reason_for_mentions_categories():
    reason = labels_mod.reason_for("L2", ["phone", "address"])
    assert "手机号" in reason and "地址" in reason
    assert labels_mod.reason_for("SAFE", []) != ""


def test_sample_from_row_builds_valid_sample():
    row = {
        "id": "T-1",
        "content": "客户张三，手机 13800138000",
        "level": "L2",
        "types": ["phone", "name", "unknown"],
    }
    sample = dataset_mod.sample_from_row(row)
    assert sample is not None
    assert sample["level"] == "L2"
    assert sample["categories"] == ["phone", "name"]
    assert labels_mod.parse_target(str(sample["target"])) is not None
    assert dataset_mod.sample_from_row({"content": "x", "level": "L9"}) is None
    assert dataset_mod.sample_from_row({"content": "", "level": "L1"}) is None


def test_build_samples_covers_levels_and_dedupes():
    if not EVAL_PATH.is_file():
        pytest.skip("评测集不存在，跳过")
    samples = dataset_mod.build_samples(EVAL_PATH)
    assert len(samples) >= 300
    assert len({sample["id"] for sample in samples}) == len(samples)
    assert len({sample["text_sha1"] for sample in samples}) == len(samples)
    assert {sample["level"] for sample in samples} == set(labels_mod.LEVELS)


def test_split_is_deterministic_and_disjoint():
    samples = [{"id": f"x{i}", "text_sha1": f"h{i}"} for i in range(20)]
    first = dataset_mod.split_samples(samples, val_ratio=0.2, seed=7)
    second = dataset_mod.split_samples(samples, val_ratio=0.2, seed=7)
    assert [row["id"] for row in first[0]] == [row["id"] for row in second[0]]
    train, val = first
    assert len(val) == 4
    assert not ({row["id"] for row in train} & {row["id"] for row in val})
    other = dataset_mod.split_samples(samples, val_ratio=0.2, seed=8)
    assert [row["id"] for row in other[1]] != [row["id"] for row in val]


def test_build_dataset_writes_jsonl(tmp_path):
    if not EVAL_PATH.is_file():
        pytest.skip("评测集不存在，跳过")
    out = tmp_path / "l3_training.jsonl"
    stats = dataset_mod.build_dataset(out, eval_path=EVAL_PATH)
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == stats["total"] == stats["train"] + stats["val"]
    assert {row["split"] for row in rows} == {"train", "val"}
    assert all(labels_mod.parse_target(row["target"]) is not None for row in rows)
    assert stats["by_level"]["L3"] > 0


def test_dry_run_reports_ready(tmp_path):
    good = tmp_path / "good.jsonl"
    good.write_text(
        "\n".join(
            json.dumps(
                {
                    "id": f"s{i}",
                    "text": "客户手机 13800138000",
                    "target": labels_mod.target_json("L2", ["phone"], "含手机号"),
                    "level": level,
                    "split": "train",
                },
                ensure_ascii=False,
            )
            for i, level in enumerate(labels_mod.LEVELS)
        )
        + "\n",
        encoding="utf-8",
    )
    stats = train_mod.dry_run(good, min_rows=4)
    assert stats["ready"] is True
    assert stats["invalid_targets"] == 0
    assert stats["missing_levels"] == []

    bad = tmp_path / "bad.jsonl"
    bad.write_text(
        json.dumps({"id": "b", "text": "x", "target": "not json", "level": "L3"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    stats = train_mod.dry_run(bad, min_rows=1)
    assert stats["ready"] is False
    assert stats["invalid_targets"] == 1
    assert stats["missing_levels"]


def test_train_requires_optional_deps(monkeypatch, tmp_path):
    def boom():
        raise RuntimeError("需要 torch/transformers/peft")

    monkeypatch.setattr(train_mod, "_require", boom)
    with pytest.raises(RuntimeError, match="torch"):
        train_mod.train(tmp_path / "missing.jsonl", tmp_path / "out")


def test_merge_rejects_non_adapter_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(train_mod, "_require", lambda: (None, None, None, None, None))
    with pytest.raises(SystemExit, match="adapter_config"):
        train_mod.merge_adapter(adapter_dir=tmp_path)
