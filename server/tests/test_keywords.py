"""敏感词表（Aho-Corasick + 三级 YAML 知识库）的单元测试。

覆盖四块：
1. 知识库加载：三级 tier、去重、非法条目报错；
2. 自动机：中文/英文混排、重叠命中、英文词边界（token 不命中 tokenize）；
3. 命中映射：类别/规则名/权重区间，以及「单命中不直接升级」的口径；
4. 接口：`/api/v1/keywords`、`/api/v1/health` 的词表计数、与 `analyze()` 的开关。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.identify.keywords import (
    DEFAULT_CONFIDENCE,
    DEFAULT_WEIGHT,
    KeywordIndex,
    KeywordTerm,
    get_keyword_index,
    keyword_hits,
    keywords_enabled_by_default,
    knowledge_summary,
    load_knowledge,
    reset_keyword_index,
)
from app.identify.scoring import analyze
from app.identify.types import EntityType, Level
from app.main import create_app

KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "app" / "identify" / "knowledge"


def _write_yaml(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_builtin_knowledge_covers_three_tiers() -> None:
    """内置词表必须三级齐全，且每条都能落到合法类别。"""
    terms = load_knowledge()
    tiers = {term.tier for term in terms}
    assert {"regulatory", "industry", "enterprise"} <= tiers
    assert len(terms) >= 50
    assert all(isinstance(term.entity, EntityType) for term in terms)
    # 同一 tier 内词条不能重复（重复会让 AC 命中两次、证据翻倍）
    keys = [(term.term, term.tier) for term in terms]
    assert len(keys) == len(set(keys))


def test_load_knowledge_from_custom_dir(tmp_path: Path) -> None:
    _write_yaml(
        tmp_path,
        "base.yaml",
        """
tier: regulatory
terms:
  - term: 身份证
    category: id_card
    weight: 0.35
    confidence: 0.6
  - term: 病历号
    category: medical
""",
    )
    terms = load_knowledge(tmp_path)
    assert [term.term for term in terms] == ["身份证", "病历号"]
    assert terms[0].weight == pytest.approx(0.35)
    # 未写权重时用默认值，避免每个词都要填一遍
    assert terms[1].weight == pytest.approx(DEFAULT_WEIGHT)
    assert terms[1].confidence == pytest.approx(DEFAULT_CONFIDENCE)


def test_load_knowledge_rejects_bad_category(tmp_path: Path) -> None:
    _write_yaml(
        tmp_path,
        "bad.yaml",
        """
tier: industry
terms:
  - term: 不存在的类别
    category: not_a_type
""",
    )
    with pytest.raises(ValueError):
        load_knowledge(tmp_path)


def test_keyword_index_finds_chinese_and_overlapping_terms() -> None:
    terms = [
        KeywordTerm(term="身份证", entity=EntityType.ID_CARD, tier="regulatory"),
        KeywordTerm(term="身份证号", entity=EntityType.ID_CARD, tier="regulatory"),
        KeywordTerm(term="手机号", entity=EntityType.PHONE, tier="regulatory"),
    ]
    index = KeywordIndex(terms)
    found = {(start, end) for start, end, _ in index.find("请提供身份证号码和手机号")}
    # 「身份证」与「身份证号」重叠在同一个位置，两者都要报出来（由 dedupe 决定保留谁）
    assert (3, 6) in found
    assert (3, 7) in found
    assert (9, 12) in found


def test_keyword_index_respects_ascii_word_boundary() -> None:
    """英文词要按词边界匹配：token 不能命中 tokenize，但能命中 a token。"""
    index = KeywordIndex([KeywordTerm(term="token", entity=EntityType.SECRET, tier="industry")])
    assert [t[2].term for t in index.find("leaked token here")] == ["token"]
    assert index.find("tokenize everything") == []


def test_keyword_hits_map_to_hits() -> None:
    index = KeywordIndex([KeywordTerm(term="病历号", entity=EntityType.MEDICAL, tier="industry", weight=0.4, confidence=0.7)])
    hits = keyword_hits("患者病历号为 A-1024", index=index)
    assert len(hits) == 1
    hit = hits[0]
    assert hit.type is EntityType.MEDICAL
    assert hit.rule == "kw-industry"
    assert hit.weight == pytest.approx(0.4)
    assert hit.value == "病历号"


def test_keyword_hit_alone_does_not_escalate_to_l3() -> None:
    """敏感词表是「主题线索」：没有真实敏感值时，只提到类别词仍是 SAFE。"""
    result = analyze("这里只提到身份证三个字", enable_decode=False, enable_gazetteer=False)
    assert result.level is Level.SAFE, result.level
    # 词表命中留在 hits 里（rule=kw-*）作为解释证据，但不进 types——
    # types 的语义是「命中了哪些敏感类别」，光提到类别词不算命中。
    assert any(hit.rule.startswith("kw-") for hit in result.hits)
    assert result.types == []
    # 关键词加分有上限，任何情况下都不该把纯主题文本顶到 L1 以上
    assert result.score <= 0.10


def test_keyword_bonus_is_capped() -> None:
    """多个类别词同时出现时，关键词加分封顶 0.10，不能靠堆词把级别堆上去。"""
    result = analyze(
        "身份证 银行卡 手机号 邮箱 地址 密钥 病历 合同 审计",
        enable_decode=False,
        enable_gazetteer=False,
    )
    assert result.level is Level.SAFE, result.level
    assert result.score <= 0.10


def test_analyze_hit_includes_keyword_rule() -> None:
    result = analyze("客户投诉：该员工泄露了病历号", enable_keywords=True, enable_decode=False, enable_gazetteer=False)
    rules = {hit.rule for hit in result.hits}
    assert any(rule.startswith("kw-") for rule in rules), rules


def test_keywords_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWMASK_DISABLE_KEYWORDS", "1")
    assert keywords_enabled_by_default() is False
    result = analyze("客户投诉：该员工泄露了病历号", enable_decode=False, enable_gazetteer=False)
    assert not any(hit.rule.startswith("kw-") for hit in result.hits)


def test_knowledge_summary_counts() -> None:
    summary = knowledge_summary()
    assert summary["total"] == len(get_keyword_index().terms)
    assert sum(summary["tiers"].values()) == summary["total"]
    first = summary["terms"][0]
    assert set(first) == {"term", "category", "tier", "weight", "confidence"}


def test_keywords_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWMASK_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FLOWMASK_DB_PATH", str(tmp_path / "data" / "flowmask.db"))
    reset_keyword_index()
    client = TestClient(create_app())

    health = client.get("/api/v1/health").json()
    assert health["keywords"] > 0
    assert set(health["keyword_tiers"]) == {"regulatory", "industry", "enterprise"}

    listing = client.get("/api/v1/keywords", params={"limit": 5}).json()
    assert listing["total"] == listing["all_total"]
    assert len(listing["terms"]) == 5
    assert client.get("/api/v1/keywords", params={"tier": "regulatory"}).status_code == 200
    assert client.get("/api/v1/keywords", params={"tier": "bogus"}).status_code == 400
