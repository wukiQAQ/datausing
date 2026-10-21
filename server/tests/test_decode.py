"""编码载荷识别的测试：base64 / URL-safe base64 / hex / 百分号编码 / 两层嵌套。"""

from __future__ import annotations

import base64
import urllib.parse

import pytest

from app.identify.decode import (
    DECODE_CONFIDENCE_PENALTY,
    MAX_DECODE_DEPTH,
    iter_decoded_spans,
)
from app.identify.masking import mask
from app.identify.scoring import analyze

PHONE_JSON = '{"phone":"13800138000"}'
PHONE_B64 = base64.b64encode(PHONE_JSON.encode()).decode()
ID_CARD_TEXT = "身份证 11010519491231002X 请勿外传"
ID_CARD_B64 = base64.b64encode(ID_CARD_TEXT.encode()).decode()


def test_base64_phone_is_mid_level() -> None:
    text = f"{PHONE_B64} 这段 base64 里是联系方式"
    result = analyze(text)
    assert result.level.name == "MID"
    assert "phone" in [typ.value for typ in result.types]
    # 命中区间落在编码片段上，不是凭空插入
    hit = result.hits[0]
    assert text[hit.start : hit.end] == PHONE_B64
    assert hit.rule.startswith("decoded-base64:")
    assert hit.confidence == pytest.approx(0.95 * DECODE_CONFIDENCE_PENALTY, abs=0.002)


def test_base64_id_card_is_high_level_and_direct() -> None:
    result = analyze(f"data: {ID_CARD_B64}")
    assert result.level.name == "HIGH"
    assert result.direct is True
    assert [typ.value for typ in result.types] == ["id_card"]


def test_base64_masking_replaces_whole_encoded_span() -> None:
    text = f"前文 {PHONE_B64} 后文"
    masked = mask(text, analyze(text))
    # L2 模板是「保留首字符 + 星号」，重点是整段编码都被替换、原文不残留
    assert PHONE_B64 not in masked
    assert "*" in masked
    assert masked.startswith("前文 ") and masked.endswith(" 后文")


def test_base64_id_card_masking_uses_placeholder() -> None:
    text = f"前文 {ID_CARD_B64} 后文"
    masked = mask(text, analyze(text))
    assert ID_CARD_B64 not in masked
    assert "已屏蔽" in masked


def test_url_safe_base64_is_decoded() -> None:
    encoded = base64.urlsafe_b64encode(b"phone:13800138000").decode().rstrip("=")
    result = analyze(f"token {encoded}")
    assert "phone" in [typ.value for typ in result.types]
    assert result.hits[0].rule.startswith("decoded-base64:")


def test_hex_encoded_payload_is_detected() -> None:
    encoded = PHONE_JSON.encode().hex()
    result = analyze(f"blob {encoded}")
    assert result.level.name == "MID"
    assert result.hits[0].rule.startswith("decoded-hex:")
    assert result.hits[0].confidence == pytest.approx(0.95 * DECODE_CONFIDENCE_PENALTY, abs=0.002)


def test_hex_escape_style_payload_is_detected() -> None:
    encoded = "".join("\\x%02x" % byte for byte in ID_CARD_TEXT.encode())
    result = analyze(encoded)
    assert result.level.name == "HIGH"
    assert "id_card" in [typ.value for typ in result.types]


def test_double_base64_keeps_high_severity() -> None:
    double = base64.b64encode(ID_CARD_B64.encode()).decode()
    spans = iter_decoded_spans(double)
    assert spans and spans[0].encoding == "base64>base64"
    assert spans[0].layers == 2 == MAX_DECODE_DEPTH
    result = analyze(double)
    # 层数只削弱证据强度，不改变「一票高危」的严重度
    assert result.level.name == "HIGH"
    assert result.direct is True


def test_base64_of_percent_encoded_payload() -> None:
    inner = urllib.parse.quote(PHONE_JSON)
    encoded = base64.b64encode(inner.encode()).decode()
    result = analyze(f"payload={encoded}")
    assert "phone" in [typ.value for typ in result.types]
    assert result.hits[0].rule.startswith("decoded-base64>percent:")


def test_percent_encoded_phone_is_detected() -> None:
    encoded = "".join(f"%{byte:02X}" for byte in "手机号 13800138000".encode())
    result = analyze(f"payload={encoded}")
    assert "phone" in [typ.value for typ in result.types]
    assert result.hits[0].rule.startswith("decoded-percent:")


def test_plain_base64_without_sensitive_data_is_safe() -> None:
    result = analyze("HEAD " + base64.b64encode(b"hello world, nothing special here").decode())
    assert result.level.name == "SAFE"
    assert result.hits == []


def test_binary_base64_is_skipped() -> None:
    blob = base64.b64encode(bytes(range(0, 48))).decode()  # 大量不可打印字节
    assert iter_decoded_spans(blob) == []


def test_short_hex_is_not_treated_as_encoding() -> None:
    # 长度门槛在候选扫描阶段（<20 个 hex 字符不当作编码），函数本身可解任意长度
    assert iter_decoded_spans("31333830303133") == []
    assert analyze("订单号 31333830303133 已发货").level.name == "SAFE"


def test_analyze_can_disable_decode() -> None:
    text = f"{PHONE_B64} 这段 base64 里是联系方式"
    assert analyze(text, enable_decode=False).level.name == "SAFE"


def test_decode_env_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    text = f"{PHONE_B64} 这段 base64 里是联系方式"
    monkeypatch.setenv("FLOWMASK_DISABLE_DECODE", "1")
    assert analyze(text).level.name == "SAFE"
    monkeypatch.delenv("FLOWMASK_DISABLE_DECODE")
    assert analyze(text).level.name == "MID"


def test_multiple_encoded_spans_are_returned_in_order() -> None:
    text = f"{PHONE_B64} 中间 {ID_CARD_B64}"
    spans = iter_decoded_spans(text)
    assert len(spans) == 2
    assert spans[0].start < spans[1].start
    assert spans[0].encoding == spans[1].encoding == "base64"
    assert "13800138000" in spans[0].text
