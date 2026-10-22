"""L2 训练侧：标签契约、弱标注、自举数据、划分与训练前置检查。"""

from __future__ import annotations

import importlib.util
import json
import random
from pathlib import Path

import pytest

from app.identify.rules import valid_idcard18, valid_luhn
from l2_training import (
    BENIGN,
    LABELS,
    SENSITIVE_LABELS,
    dry_run,
    from_eval_dataset,
    from_weibo_ner,
    load_jsonl,
    make_bank_card,
    make_id_card,
    split_examples,
    synthetic_examples,
    train,
    weak_label,
    write_jsonl,
)
from l2_training.__main__ import main as cli_main
from l2_training.dataset import make_email, make_ip, make_phone, make_secret


def test_labels_contract() -> None:
    assert LABELS[-1] == BENIGN
    assert len(SENSITIVE_LABELS) == 10
    assert set(LABELS) == {*SENSITIVE_LABELS, BENIGN}
    assert "id_card" in SENSITIVE_LABELS and "medical" in SENSITIVE_LABELS


def test_generated_identifiers_are_valid() -> None:
    rng = random.Random(7)
    for _ in range(20):
        assert valid_idcard18(make_id_card(rng)), "生成的身份证校验位不合法"
        assert valid_luhn(make_bank_card(rng)), "生成的银行卡 Luhn 不合法"
        phone = make_phone(rng)
        assert len(phone) == 11 and phone.isdigit() and phone.startswith("1")
        assert "@" in make_email(rng)
        assert make_ip(rng).count(".") == 3
        assert make_secret(rng).strip()


def test_weak_label_detects_l1_types() -> None:
    weak = weak_label("客户张三，手机 13800138000，身份证 11010519491231002X")
    assert weak.labels["phone"] is True
    assert weak.labels["id_card"] is True
    assert weak.labels[BENIGN] is False
    assert any(item.startswith("l1:") for item in weak.evidence)


def test_weak_label_marks_benign_even_with_numbers() -> None:
    weak = weak_label("示例数据：手机号 13800138000（虚构，仅用于联调）")
    assert weak.labels[BENIGN] is True
    # 硬负例：benign 与敏感特征可以同时为真，模型要学的是"benign 压过敏感"
    assert weak.labels["phone"] is True


def test_synthetic_dataset_deterministic_and_covered() -> None:
    first = synthetic_examples(count=120, seed=11)
    second = synthetic_examples(count=120, seed=11)
    assert len(first) == 120
    assert [example.text for example in first] == [example.text for example in second]
    positives = {name for example in first for name in example.positives()}
    assert set(SENSITIVE_LABELS) <= positives
    assert BENIGN in positives
    assert all(example.text.strip() for example in first)


def test_synthetic_rejects_bad_count() -> None:
    with pytest.raises(ValueError):
        synthetic_examples(count=0)


def test_from_eval_dataset_marks_safe_rows_benign(tmp_path: Path) -> None:
    dataset = tmp_path / "eval.jsonl"
    dataset.write_text(
        json.dumps({"id": "S1", "content": "项目周报：本周完成联调。", "level": "SAFE", "types": []}, ensure_ascii=False)
        + "\n"
        + json.dumps(
            {"id": "L3-001", "content": "客户张三，身份证 11010519491231002X", "level": "L3", "types": ["id_card"]},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    examples = from_eval_dataset(dataset)
    assert len(examples) == 2
    safe, sensitive = examples
    assert safe.labels[BENIGN] is True
    assert sensitive.labels["id_card"] is True
    assert sensitive.source == "eval:L3-001"


def test_split_examples_deterministic_and_disjoint() -> None:
    examples = synthetic_examples(count=60, seed=3)
    train_a, val_a = split_examples(examples, val_ratio=0.2, seed=42)
    train_b, val_b = split_examples(examples, val_ratio=0.2, seed=42)
    assert [example.text for example in val_a] == [example.text for example in val_b]
    total = len(train_a) + len(val_a)
    assert total == len({example.text for example in examples})
    assert len(val_a) == max(1, int(total * 0.2))
    assert not {example.text for example in train_a} & {example.text for example in val_a}


def test_write_and_load_roundtrip(tmp_path: Path) -> None:
    examples = synthetic_examples(count=30, seed=5)
    path = write_jsonl(tmp_path / "train.jsonl", examples)
    loaded = load_jsonl(path)
    assert len(loaded) == len(examples)
    assert loaded[0].labels == examples[0].labels
    assert loaded[0].text == examples[0].text


def test_load_rejects_unknown_label(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps({"text": "x", "labels": {"nope": True}}), encoding="utf-8")
    with pytest.raises(ValueError, match="未知标签"):
        load_jsonl(path)


def test_dry_run_stats(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "train.jsonl", synthetic_examples(count=80, seed=9))
    stats = dry_run(path, min_rows=20)
    assert stats["total"] == 80
    assert stats["ready"] is True
    assert stats["missing_labels"] == []
    assert stats["positives"][BENIGN] > 0
    assert stats["avg_chars"] > 0


def test_dry_run_rejects_too_few_rows(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "tiny.jsonl", synthetic_examples(count=5, seed=9))
    with pytest.raises(ValueError, match="样本太少"):
        dry_run(path, min_rows=20)


def test_train_without_torch_has_helpful_hint(tmp_path: Path) -> None:
    if importlib.util.find_spec("torch") is not None:  # pragma: no cover - 装了 torch 的机器跳过
        pytest.skip("本机已装 torch，跳过缺依赖分支")
    path = write_jsonl(tmp_path / "train.jsonl", synthetic_examples(count=30, seed=1))
    with pytest.raises(RuntimeError, match="torch"):
        train(path, out_dir=tmp_path / "model")


def test_cli_dataset_and_inspect(tmp_path: Path) -> None:
    target = tmp_path / "cli.jsonl"
    assert cli_main(["dataset", "--out", str(target), "--count", "40", "--with-eval", ""]) == 0
    assert target.exists()
    assert cli_main(["inspect", "--dataset", str(target), "--min-rows", "20"]) == 0


def test_synthetic_hard_negatives() -> None:
    """benign 里必须有「带示例标记 + 真敏感形态」的困难负例，且覆盖银行卡/密钥类别。"""
    from l2_training.dataset import synthetic_examples

    examples = synthetic_examples(200, seed=7)
    benign = [ex for ex in examples if ex.labels.get("benign")]
    assert benign, "没有 benign 样本"
    ratio = len(benign) / len(examples)
    assert 0.2 <= ratio <= 0.4, f"benign 占比 {ratio:.2f} 应在 20%~40%"
    hard = [
        ex
        for ex in benign
        if ex.labels.get("bank_card") or ex.labels.get("secret") or ex.labels.get("id_card")
    ]
    assert hard, "缺少带敏感形态的困难负例"


def _write_weibo_fixture(path: Path) -> None:
    """造一份 mini weibo NER（OntoNotes 标注）：专名、代称、纯 O 各一条。"""
    rows = [
        {
            "tokens": list("张伟在福州软件园上班"),
            "tags": ["B-PER.NAM", "E-PER.NAM", "O", "B-GPE.NAM", "I-GPE.NAM", "E-GPE.NAM", "O", "O", "O", "O"],
            "entities": [
                {"start": 0, "end": 2, "type": "per.nam"},
                {"start": 3, "end": 6, "type": "gpe.nam"},
            ],
        },
        {
            "tokens": list("老百姓都挺开心的"),
            "tags": ["S-PER.NOM", "O", "O", "O", "O", "O", "O", "O"],
            "entities": [{"start": 0, "end": 1, "type": "per.nom"}],
        },
        {
            "tokens": list("今天天气不错适合出门"),
            "tags": ["O"] * 9,
            "entities": [],
        },
    ]
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def test_from_weibo_ner_maps_proper_nouns_only(tmp_path: Path) -> None:
    """公开语料：只把专名映射成 name/address，代称（per.nom）与纯 O 句子不进训练集。"""
    cache = tmp_path / "weibo.json"
    _write_weibo_fixture(cache)
    examples = from_weibo_ner(cache, limit=10)
    assert len(examples) == 1, [ex.text for ex in examples]
    sample = examples[0]
    assert sample.source == "weibo-ner"
    assert sample.labels["name"] is True
    assert sample.labels["address"] is True
    assert "老百姓" not in sample.text


def test_from_weibo_ner_limit_and_missing_file(tmp_path: Path) -> None:
    """条数上限生效；文件缺失时明确报错。"""
    cache = tmp_path / "weibo.json"
    _write_weibo_fixture(cache)
    assert len(from_weibo_ner(cache, limit=0)) == 0
    with pytest.raises(FileNotFoundError):
        from_weibo_ner(tmp_path / "missing.json")
