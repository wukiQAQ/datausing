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
    # 字段名作为上下文：{"name":"张三"} 命中姓名字段规则，{"id_card":...} 命中身份证
    findings = {finding.path: finding for finding in report.findings}
    assert set(findings) == {"$.name", "$.id_card"}
    id_finding = findings["$.id_card"]
    assert id_finding.level == Level.HIGH and id_finding.model_chain == "l1"
    assert "已屏蔽:身份证号" in id_finding.masked
    assert ID_CARD not in id_finding.masked
    # 脱敏只作用在值上：区间从 0 开始，不把字段名算进去
    assert id_finding.spans[0]["start"] == 0
    name_finding = findings["$.name"]
    # L1 是"前置警示 + 保留原文"，L2 才做首字符 + 星号\n    assert name_finding.level == Level.LOW and name_finding.masked == "[低危]张三"
    assert report.by_level["L3"] == 1 and report.by_level["L1"] == 1


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

class BenignSemantic:
    """假的 L2：判"这是示例数据"，置信度可配。"""

    name = "benign-l2"

    def __init__(self, confidence: float) -> None:
        self.confidence = confidence

    def available(self) -> bool:
        return True

    def predict(self, text: str) -> list[SemanticHit]:
        return [SemanticHit(type=None, confidence=self.confidence, benign=True)]


def test_l2_benign_suppresses_phone_level() -> None:
    """只有手机号时 L1 判 L2；L2 确认为示例数据后应压回 SAFE。"""
    text = "示例号码 13800138000 仅用于联调测试"
    l1 = DetectionPipeline().analyze_text(text, DetectOptions())
    assert l1 is not None and l1.level == Level.MID

    suppressed = DetectionPipeline(semantic=BenignSemantic(0.9)).analyze_text(
        text, DetectOptions(enable_l2=True)
    )
    assert suppressed is not None
    assert suppressed.level == Level.SAFE
    assert suppressed.model_chain == "l1>l2"
    assert any(item["type"] == "l2_benign" for item in suppressed.confidence_vector)


def test_l2_benign_does_not_suppress_direct_with_low_confidence() -> None:
    """证件号是直达高危：L2 置信度不够高时不许压制（宁误报勿漏报）。"""
    text = "身份证 11010519491231002X"
    finding = DetectionPipeline(semantic=BenignSemantic(0.8)).analyze_text(
        text, DetectOptions(enable_l2=True)
    )
    assert finding is not None
    assert finding.level == Level.HIGH


def test_l2_benign_suppresses_direct_when_very_confident() -> None:
    """L2 非常确信"这是示例"时，直达高危也允许压下去，并取消 direct 标记。"""
    text = "示例：身份证 11010519491231002X 仅为演示数据"
    finding = DetectionPipeline(semantic=BenignSemantic(0.95)).analyze_text(
        text, DetectOptions(enable_l2=True)
    )
    assert finding is not None
    assert finding.level.severity < Level.HIGH.severity
    assert finding.direct is False


def test_l2_context_uses_document_text() -> None:
    """JSON 里"示例"线索在别的字段，L2 应拿到文档级上下文而不是只看单个值。"""

    class ContextAware:
        name = "ctx-l2"

        def available(self) -> bool:
            return True

        def predict(self, text: str) -> list[SemanticHit]:
            return [SemanticHit(type=None, confidence=0.95, benign=True)] if "示例" in text else []

    document = parse_json('{"说明":"以下为示例数据","phone":"13800138000"}')
    report = DetectionPipeline(semantic=ContextAware()).analyze_document(
        document, DetectOptions(enable_l2=True)
    )
    assert all(finding.level == Level.SAFE for finding in report.findings)
