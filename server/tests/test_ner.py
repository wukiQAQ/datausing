"""L1.5 实体识别（NER）接入的测试：标签映射、区间并集、失败降级、装配开关。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.identify.ner import (
    NerSpan,
    RaNerDetector,
    TokenNerDetector,
    build_ner_detector,
    map_ner_label,
    spans_to_hits,
)
from app.identify.pipeline import DetectOptions, DetectionPipeline
from app.identify.types import EntityType, Level


# --------------------------------------------------------------- 纯函数：标签映射
@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("B-PER", EntityType.NAME),
        ("I-PER", EntityType.NAME),
        ("S-NR", EntityType.NAME),
        ("PER", EntityType.NAME),
        ("B-LOC", EntityType.ADDRESS),
        ("LOC", EntityType.ADDRESS),
        ("B-ORG", EntityType.ADDRESS),
        ("O", None),
        ("B-MISC", None),
        ("", None),
    ],
)
def test_map_ner_label(label: str, expected: EntityType | None) -> None:
    assert map_ner_label(label) is expected


def test_spans_to_hits_keeps_offsets_and_weights() -> None:
    text = "客户张三住在鼓楼区五四路 100 号"
    spans = [NerSpan(start=2, end=4, label="B-PER"), NerSpan(start=6, end=12, label="B-LOC")]
    hits = spans_to_hits(spans, text)
    assert [hit.type for hit in hits] == [EntityType.NAME, EntityType.ADDRESS]
    assert [hit.value for hit in hits] == ["张三", "鼓楼区五四路"]
    assert hits[0].weight == pytest.approx(0.40) and hits[0].confidence == pytest.approx(0.70)
    assert hits[1].weight == pytest.approx(0.50)
    assert hits[0].rule == "ner-name"


def test_spans_to_hits_drops_invalid_and_unknown() -> None:
    text = "张三"
    spans = [
        NerSpan(start=-1, end=2, label="B-PER"),  # 越界
        NerSpan(start=0, end=9, label="B-PER"),  # 超长
        NerSpan(start=0, end=2, label="B-MISC"),  # 未知标签
        NerSpan(start=1, end=1, label="B-LOC"),  # 空区间
        NerSpan(start=0, end=2, label="B-PER"),
    ]
    hits = spans_to_hits(spans, text)
    assert len(hits) == 1 and hits[0].value == "张三"


# --------------------------------------------------------------- 流水线并集
class _FakeNer:
    name = "fake-ner"

    def __init__(self, spans: list[NerSpan], *, fail: bool = False) -> None:
        self._spans = spans
        self._fail = fail

    def available(self) -> bool:
        return True

    def detect(self, text: str) -> list[NerSpan]:
        if self._fail:
            raise RuntimeError("ner exploded")
        return list(self._spans)


def test_pipeline_merges_ner_hits() -> None:
    """没有字段名时也能识别出人名（L1 规则抓不到"客户张三"里的人民）。"""
    text = "客户张三，电话 13800138000"
    ner = _FakeNer([NerSpan(start=2, end=4, label="B-PER")])
    pipeline = DetectionPipeline(ner=ner)  # type: ignore[arg-type]
    finding = pipeline.analyze_text(text, DetectOptions(enable_ner=True))
    assert finding is not None
    assert "name" in finding.types and "phone" in finding.types
    assert "ner" in finding.model_chain
    # 人名权重低，靠与手机号共现升到 L2；手机号脱敏为保留首字符
    assert finding.level in {Level.LOW, Level.MID}
    assert "张*" in finding.masked and "1******" in finding.masked
    # spans 的区间必须能切回原文命中值（前端高亮依赖它）
    name_span = next(span for span in finding.spans if span["type"] == "name")
    assert text[name_span["start"] : name_span["end"]] == "张三"


def test_pipeline_ner_overlap_does_not_duplicate() -> None:
    """NER 粗区间覆盖到 L1 已命中的数字时，不能重复计算（L1 更精确）。"""
    text = "电话 13800138000"
    ner = _FakeNer([NerSpan(start=3, end=14, label="B-PER")])
    pipeline = DetectionPipeline(ner=ner)  # type: ignore[arg-type]
    finding = pipeline.analyze_text(text, DetectOptions(enable_ner=True))
    assert finding is not None
    assert finding.types == ["phone"]
    assert finding.hits == 1
    assert "ner" not in finding.model_chain


def test_pipeline_degrades_when_ner_fails() -> None:
    """NER 崩了只标记 degraded，L1 结论必须照常返回。"""
    text = "电话 13800138000"
    pipeline = DetectionPipeline(ner=_FakeNer([], fail=True))  # type: ignore[arg-type]
    finding = pipeline.analyze_text(text, DetectOptions(enable_ner=True))
    assert finding is not None
    assert finding.degraded is True
    assert finding.types == ["phone"]


# --------------------------------------------------------------- 装配开关
def test_ner_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLOWMASK_ENABLE_NER", raising=False)
    assert build_ner_detector() is None


def test_build_ner_detector_token_backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("FLOWMASK_ENABLE_NER", "1")
    monkeypatch.setenv("FLOWMASK_NER_BACKEND", "token")
    monkeypatch.setenv("FLOWMASK_NER_MODEL_DIR", str(tmp_path))
    detector = build_ner_detector()
    assert isinstance(detector, TokenNerDetector)


def test_build_ner_detector_raner_backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FLOWMASK_ENABLE_NER", "1")
    monkeypatch.setenv("FLOWMASK_NER_BACKEND", "raner")
    monkeypatch.setenv("FLOWMASK_NER_MODEL_DIR", str(tmp_path))
    detector = build_ner_detector()
    assert isinstance(detector, RaNerDetector)


def test_detector_unavailable_when_model_dir_missing(tmp_path: Path) -> None:
    assert TokenNerDetector(tmp_path / "missing").available() is False
    assert RaNerDetector(tmp_path / "missing").available() is False
