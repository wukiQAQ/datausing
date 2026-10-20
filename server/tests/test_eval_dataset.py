"""评测集自身的完整性测试：数据集是"考卷"，考卷本身不能有错题号、错级别。

这些用例不依赖任何模型权重，只校验 `eval/*.jsonl` 的结构与标注口径，
避免以后有人手改数据集时把重复 id、错误级别或训练集泄漏带进来。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.identify.types import EntityType
from eval.build_dataset import _dedupe_ids, count_overlap, dataset_stats, gold_level, render_stats

SERVER_DIR = Path(__file__).resolve().parents[1]
DATASET = SERVER_DIR / "eval" / "dataset.jsonl"
STRESS = SERVER_DIR / "eval" / "ner_stress.jsonl"

VALID_LEVELS = {"L3", "L2", "L1", "SAFE"}
VALID_TYPES = {typ.value for typ in EntityType}
LEVEL_NAMES = ("L3", "L2", "L1", "SAFE")


def _load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="module")
def dataset() -> list[dict]:
    rows = _load(DATASET)
    assert rows, "评测集不应为空"
    return rows


@pytest.fixture(scope="module")
def stress() -> list[dict]:
    return _load(STRESS)


def test_dataset_shape(dataset: list[dict]) -> None:
    """规模与来源构成：策展 75 条 + 生成 225 条 = 300 条。"""
    assert len(dataset) == 300
    curated = [row for row in dataset if not row.get("source")]
    generated = [row for row in dataset if row.get("source") == "generated"]
    assert (len(curated), len(generated)) == (75, 225)


def test_ids_are_unique(dataset: list[dict], stress: list[dict]) -> None:
    ids = [row["id"] for row in dataset]
    assert len(ids) == len(set(ids)), "评测集 id 重复会让报告里的判错定位不到唯一行"
    stress_ids = [row["id"] for row in stress]
    assert len(stress_ids) == len(set(stress_ids))
    assert all(ident.startswith("STRESS-") for ident in stress_ids)


def test_contents_are_unique(dataset: list[dict], stress: list[dict]) -> None:
    contents = [row["content"] for row in dataset]
    assert len(contents) == len(set(contents))
    assert not {row["content"] for row in dataset} & {row["content"] for row in stress}


def test_levels_and_types_are_valid(dataset: list[dict], stress: list[dict]) -> None:
    for row in dataset + stress:
        assert row["level"] in VALID_LEVELS, row["id"]
        assert row["format"] in {"text", "json", "csv", "sql"}, row["id"]
        assert isinstance(row["types"], list)
        assert set(row["types"]) <= VALID_TYPES, (row["id"], row["types"])
        assert row["content"].strip(), row["id"]


def test_gold_level_matches_scoring_policy(dataset: list[dict]) -> None:
    """标注级别必须与评分模型折算出来的一致，否则等于把"考卷答案"写错。"""
    mismatches = []
    for row in dataset:
        expected, _direct = gold_level([EntityType(typ) for typ in row["types"]])
        if expected != row["level"]:
            mismatches.append((row["id"], row["level"], expected))
    assert not mismatches, f"级别与评分口径不一致：{mismatches[:5]}"


def test_no_training_leakage(dataset: list[dict]) -> None:
    """评测集不能与 L2 训练集有内容完全相同的行（训练集本地不存在时跳过）。"""
    training = SERVER_DIR / "data" / "l2_training.jsonl"
    if not training.is_file():
        pytest.skip("本地没有 L2 训练集，跳过泄漏检查")
    hits, total = count_overlap(dataset, training)
    assert hits == 0, f"{hits}/{total} 条评测样本同时出现在训练集里"


def test_stress_set_targets_ner_only(stress: list[dict]) -> None:
    """压力集是"规则抓不到、只能靠 NER"的自由文本，因此全部标 L1 且只含姓名/地址。"""
    assert len(stress) == 200
    for row in stress:
        assert row["level"] == "L1", row["id"]
        assert set(row["types"]) <= {"name", "address"}, (row["id"], row["types"])
        assert "weibo_ner" in row.get("source", "")
        assert "公开语料" in row.get("note", "")
    assert sum("name" in row["types"] for row in stress) >= 100
    assert sum("address" in row["types"] for row in stress) >= 60


def test_dedupe_ids_renumbers_collisions() -> None:
    """撞号时按前缀顺延，不覆盖已有编号，也不产生新的重复。"""
    rows = [
        {"id": "L2-001"},
        {"id": "L2-002"},
        {"id": "L2-001"},
        {"id": "GEN-001"},
        {"id": "GEN-001"},
        {"id": "STRESS-001"},
        {"id": "STRESS-001"},
    ]
    fixed = _dedupe_ids(rows)
    ids = [row["id"] for row in fixed]
    assert len(ids) == len(set(ids))
    assert ids[:2] == ["L2-001", "L2-002"]
    assert ids[3:5] == ["GEN-001", "GEN-002"]
    assert ids[5:] == ["STRESS-001", "STRESS-002"]


def test_every_level_and_core_type_is_covered(dataset: list[dict]) -> None:
    levels = {row["level"] for row in dataset}
    assert levels == VALID_LEVELS
    covered = {typ for row in dataset for typ in row["types"]}
    core = {"id_card", "phone", "bank_card", "email", "name", "address", "secret"}
    assert core <= covered, f"缺类别：{core - covered}"


def test_level_distribution_is_reasonable(dataset: list[dict]) -> None:
    """扩到 300 条后，每一级都不少于 60 条，避免某一级样本太少让指标失真。"""
    for level in LEVEL_NAMES:
        count = sum(1 for row in dataset if row["level"] == level)
        assert count >= 60, f"{level} 只有 {count} 条"


def test_type_support_is_reported_and_sufficient(dataset: list[dict]) -> None:
    """类别支持数：核心类别每个至少 15 条，且 dataset_stats 的口径与逐行统计一致。"""
    stats = dataset_stats(dataset)
    for level, count in stats["by_level"].items():
        assert count == sum(1 for row in dataset if row["level"] == level), level
    core = {"id_card", "phone", "bank_card", "email", "name", "address", "secret"}
    thin = {typ: stats["by_type"].get(typ, 0) for typ in core if stats["by_type"].get(typ, 0) < 15}
    assert not thin, f"以下核心类别支持数不足 15：{thin}"
    table = render_stats(stats)
    assert "类别支持数" in table and "唯一 id 300" in table
