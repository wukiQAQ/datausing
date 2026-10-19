"""识别流水线测试：L1/L2/L3 协同、降级、脱敏级别覆盖。"""

from __future__ import annotations

from app.identify import DetectionPipeline, DetectOptions, EntityType, Level
from app.identify.l2 import SemanticHit
from app.identify.l3 import GuardVerdict
from app.identify.pipeline import sha256_text
from app.parsers import parse_json, parse_text

ID_CARD = "11010519491231002X"
PHONE = "13800138000"


class FakeSemantic:
    """假的 L2：固定返回"医疗信息"类别，用来验证类别合并与模型链路。"""

    name = "fake-l2"

    def available(self) -> bool:
        return True

    def predict(self, text: str) -> list[SemanticHit]:
        return [SemanticHit(type=EntityType.MEDICAL, confidence=0.92)]


class BrokenSemantic:
    name = "broken-l2"

    def available(self) -> bool:
        return True

    def predict(self, text: str) -> list[SemanticHit]:
        raise TimeoutError("推理服务超时")


class FakeGuard:
    name = "fake-guard"

    def available(self) -> bool:
        return True

    def judge(self, text: str) -> GuardVerdict | None:
        return GuardVerdict(risk="Unsafe", reason="检测到疑似身份证号，需按高危处理", model="fake-qwen")


class DownGuard:
    name = "down-guard"

    def available(self) -> bool:
        return True

    def judge(self, text: str) -> GuardVerdict | None:
        return None  # 服务不可用


def test_l1_only_json_document() -> None:
    document = parse_json('{"name":"张三","id_card":"%s"}' % ID_CARD)
    report = DetectionPipeline().analyze_document(document, DetectOptions())
    assert report.segments == 2
    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.level == Level.HIGH and finding.model_chain == "l1"
    assert "已屏蔽:身份证号" in finding.masked
    assert ID_CARD not in finding.masked
    assert report.by_level["L3"] == 1


def test_l2_merges_types_and_chain() -> None:
    document = parse_text("邮箱 zhang.san@example.com")
    report = DetectionPipeline(semantic=FakeSemantic()).analyze_document(
        document, DetectOptions(enable_l2=True)
    )
    finding = report.findings[0]
    assert finding.model_chain == "l1>l2"
    assert "medical" in finding.types  # L2 补出来的类别
    assert finding.score > 0.405


def test_l2_exception_marks_degraded() -> None:
    document = parse_text("邮箱 zhang.san@example.com")
    report = DetectionPipeline(semantic=BrokenSemantic()).analyze_document(
        document, DetectOptions(enable_l2=True)
    )
    assert report.degraded is True
    assert report.findings[0].model_chain == "l1"


def test_l3_upgrades_ambiguous_and_records_reason() -> None:
    document = parse_text(f"联系电话 {PHONE}")
    report = DetectionPipeline(guard=FakeGuard()).analyze_document(document, DetectOptions(enable_l3=True))
    finding = report.findings[0]
    assert finding.level == Level.HIGH
    assert finding.model_chain == "l1>l3"
    assert finding.reason and "身份证" in finding.reason


def test_l3_unavailable_degrades_without_blocking() -> None:
    document = parse_text(f"联系电话 {PHONE}")
    report = DetectionPipeline(guard=DownGuard()).analyze_document(document, DetectOptions(enable_l3=True))
    assert report.degraded is True
    assert report.findings[0].level == Level.MID  # 保留 L1 结论


def test_mask_level_override_in_options() -> None:
    document = parse_text(f"联系电话 {PHONE}")
    report = DetectionPipeline().analyze_document(document, DetectOptions(mask_level=Level.HIGH))
    assert "[已屏蔽:手机号]" in report.findings[0].masked


def test_min_level_filters_low_findings() -> None:
    document = parse_text("邮箱 zhang.san@example.com")
    report = DetectionPipeline().analyze_document(document, DetectOptions(min_level=Level.MID))
    assert report.findings == []
    assert report.by_level["L1"] == 1  # 统计仍然算到


def test_safe_text_yields_no_finding() -> None:
    assert DetectionPipeline().analyze_text("今天天气不错", DetectOptions()) is None


def test_sha256_text_is_stable() -> None:
    assert sha256_text("abc") == sha256_text("abc") and len(sha256_text("abc")) == 64
