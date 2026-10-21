"""地名库（gazetteer）补白的单元测试与回归防护。"""

from __future__ import annotations

import pytest

from app.identify.gazetteer import (
    CITIES,
    PROVINCES,
    gazetteer_hits,
    iter_place_mentions,
    known_places,
)
from app.identify.scoring import analyze


def _values(text: str) -> list[str]:
    return [hit.value for hit in gazetteer_hits(text)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("他昨天从湖南过来，住了一晚就走了", "湖南"),
        ("这批货是从四川发出来的", "四川"),
        ("客户在杭州开会，晚点回公司", "杭州"),
        ("我们公司的服务器部署在上海", "上海"),
    ],
)
def test_direction_mentions_are_detected(text: str, expected: str) -> None:
    assert expected in _values(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("收货地址：福建省福州市鼓楼区五四路 100 号", "福建"),
        ("仓库在东莞虎门镇", "东莞"),
        ("联系人来自深圳市南山区", "深圳"),
    ],
)
def test_suffixed_mentions_are_detected(text: str, expected: str) -> None:
    assert expected in _values(text)


@pytest.mark.parametrize(
    "text",
    [
        "今天天气不错，系统运行正常",
        "北京银行 2026 年第三季度报告",
        "接口文档示例：张三，手机 13800138000",
        "这是一条健康检查日志",
    ],
)
def test_plain_text_has_no_place_hits(text: str) -> None:
    assert gazetteer_hits(text) == []


def test_bare_place_names_need_evidence() -> None:
    # 只有地名、既无后缀也无方位词时不应命中（避免把普通词当地址）
    assert gazetteer_hits("湖南好风光") == []
    assert gazetteer_hits("看到四川的消息") == []


def test_mentions_do_not_overlap_and_are_sorted() -> None:
    mentions = iter_place_mentions("他从湖南长沙出发，先到上海开会")
    spans = [(m.start, m.end) for m in mentions]
    assert spans == sorted(spans)
    for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
        assert e1 <= s2


def test_hit_carries_rule_and_level_weights() -> None:
    hit = gazetteer_hits("他从四川过来")[0]
    assert hit.type.value == "address"
    assert hit.rule.startswith("gazetteer-")
    assert hit.weight == 0.50
    assert hit.confidence == 0.65


def test_analyze_marks_place_only_text_as_low_risk() -> None:
    result = analyze("他昨天从湖南过来，住了一晚就走了")
    assert result.level.name == "LOW"
    assert [typ.value for typ in result.types] == ["address"]


def test_analyze_can_disable_gazetteer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWMASK_DISABLE_GAZETTEER", "1")
    result = analyze("他昨天从湖南过来，住了一晚就走了")
    assert result.level.name == "SAFE"
    assert result.types == []


def test_known_places_cover_provinces_and_major_cities() -> None:
    places = set(known_places())
    assert set(PROVINCES) <= places
    assert set(CITIES) <= places
    assert "内蒙古" in places
    assert "乌鲁木齐" in places
