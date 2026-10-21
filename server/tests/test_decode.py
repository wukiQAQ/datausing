"""编码载荷识别的测试：base64 / 百分号编码的敏感数据要能在解码后被抓住。"""

from __future__ import annotations

import base64

import pytest

from app.identify.decode import DECODE_CONFIDENCE_PENALTY, iter_decoded_spans
from app.identify.masking import mask
from app.identify.scoring import analyze

PHONE_B64 = base64.b64encode(b'{"phone":"13800138000"}').decode()
ID_CARD_B64 = base64.b64encode("身份证 11010519491231002X 请勿外传".encode()).decode()


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


def test_percent_encoded_phone_is_detected() -> None:
    # “手机号 13800138000”整段做百分号编码
    encoded = "".join(f"%{byte:02X}" for byte in "手机号 13800138000".encode())
    result = analyze(f"payload={encoded}")
    assert "phone" in [typ.value for typ in result.types]
    assert result.hits[0].rule.startswith("decoded-percent:")


def test_plain_base64_without_sensitive_data_is_safe() -> None:
    assert base64.b64encode(b"hello world, nothing special here").decode()
    result = analyze("HEAD " + base64.b64encode(b"hello world, nothing special here").decode())
    assert result.level.name == "SAFE"
    assert result.hits == []


def test_binary_base64_is_skipped() -> None:
    blob = base64.b64encode(bytes(range(0, 48))).decode()  # 大量不可打印字节
    spans = iter_decoded_spans(blob)
    assert spans == []


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
