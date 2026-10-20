"""识别层单元测试：规则 → 评分分级 → 脱敏 → 隐私算子。"""

from __future__ import annotations

import random

from app.identify import (
    EntityType,
    Level,
    analyze,
    check_l_diversity,
    detect,
    entity_label,
    generalize_numeric,
    laplace_noise,
    mask,
    mask_value,
    parse_entity_type,
    perturb_numeric,
)

ID_CARD = "11010519491231002X"  # 校验位合法（GB 11643-1999）
BANK_CARD = "4111111111111111"  # Luhn 合法
PHONE = "13800138000"


def test_detect_id_card_and_dedupe() -> None:
    hits = detect(f"身份证号：{ID_CARD}")
    assert [hit.rule for hit in hits] == ["idcard18"]
    # 18 位数字同时"形似"银行卡，重叠时只保留证据最强的证件规则
    assert all(hit.type == EntityType.ID_CARD for hit in hits)


def test_detect_rejects_bad_checksum() -> None:
    bad = "110105194912310021"  # 校验位被改，MOD 11-2 不通过
    assert detect(f"身份证号：{bad}") == []


def test_detect_bank_card_requires_luhn() -> None:
    assert [hit.type for hit in detect(f"卡号 {BANK_CARD}")] == [EntityType.BANK_CARD]
    assert detect("卡号 4111111111111112") == []  # Luhn 不通过


def test_detect_secret_with_quoted_key() -> None:
    hits = detect('{"api_key":"sk-ABCDEFGH12345678"}')
    assert hits and hits[0].type == EntityType.SECRET


def test_medical_value_allows_inner_space() -> None:
    """病历里常见「2 型糖尿病」这种带空格的写法，规则要能抓到完整值。"""
    hits = detect("患者 陈静，诊断：2 型糖尿病，复诊医院：市第一人民医院。")
    medical = [hit for hit in hits if hit.type is EntityType.MEDICAL]
    assert medical, "带空格的就诊结论没有命中"
    assert medical[0].value == "2 型糖尿病"


def test_detect_name_kv_uses_group() -> None:
    hits = detect("姓名：张三")
    assert hits and hits[0].type == EntityType.NAME and hits[0].value == "张三"


def test_score_phone_alone_is_mid() -> None:
    result = analyze(f"联系电话 {PHONE}")
    assert result.level == Level.MID
    assert 0.55 <= result.score < 0.85


def test_score_email_alone_is_low() -> None:
    assert analyze("邮箱 zhang.san@example.com").level == Level.LOW


def test_score_ip_alone_is_safe() -> None:
    assert analyze("调试地址 192.168.1.1").level == Level.SAFE


def test_id_card_direct_to_high() -> None:
    result = analyze(f"自然人 {ID_CARD}")
    assert result.direct and result.level == Level.HIGH


def test_cooccurrence_raises_level() -> None:
    result = analyze("姓名：张三，电话：13800138000，地址：福建省福州市鼓楼区五四路100号")
    assert result.level == Level.HIGH  # 身份类 3 个：姓名 + 手机号 + 地址
    assert {EntityType.NAME, EntityType.PHONE, EntityType.ADDRESS} <= set(result.types)


def test_mask_templates() -> None:
    assert mask(f"身份证 {ID_CARD}", analyze(f"身份证 {ID_CARD}")) == "身份证 [已屏蔽:身份证号]"
    masked = mask(f"电话 {PHONE}", analyze(f"电话 {PHONE}"))
    assert masked.startswith("电话 1") and "*" in masked and PHONE not in masked
    assert mask("普通文本", analyze("普通文本")) == "普通文本"


def test_mask_level_override() -> None:
    text = f"电话 {PHONE}"
    forced = mask(text, analyze(text), Level.HIGH)
    assert "已屏蔽" in forced and PHONE not in forced


def test_mask_value_keeps_first_char() -> None:
    assert mask_value("张三", EntityType.NAME, Level.MID) == "张*"


def test_entity_type_roundtrip() -> None:
    assert parse_entity_type("身份证号") is EntityType.ID_CARD
    assert parse_entity_type("id_card") is EntityType.ID_CARD
    assert entity_label(EntityType.SECRET) == "密钥凭据"
    assert parse_entity_type("不存在的类别") is None


def test_laplace_noise_mean_and_reproducible() -> None:
    values = [laplace_noise(1.0, 10.0, random.Random(seed)) for seed in range(2000)]
    # 尺度 = sensitivity/epsilon = 10，2000 次采样的均值应明显小于尺度
    assert abs(sum(values) / len(values)) < 1.0
    assert laplace_noise(1.0, 1.0, random.Random(42)) == laplace_noise(1.0, 1.0, random.Random(42))


def test_perturb_and_generalize() -> None:
    assert perturb_numeric(100.0, epsilon=0.001, sensitivity=1000.0) != 100.0
    assert generalize_numeric(13800138000, 1000) == "13800138000-13800139000"


def test_l_diversity() -> None:
    rows = [
        {"age": "20", "zip": "100", "disease": "感冒"},
        {"age": "20", "zip": "100", "disease": "流感"},
    ]
    report = check_l_diversity(rows, ["age", "zip"], "disease", l=3)
    assert report.ok is False and report.groups == 1 and len(report.violations) == 1

    rows.append({"age": "20", "zip": "100", "disease": "胃炎"})
    report = check_l_diversity(rows, ["age", "zip"], "disease", l=3)
    assert report.ok is True and report.violations == []


def test_bankcard_rejects_non_bin_prefix() -> None:
    """19 位订单号即使碰巧通过 Luhn，也不能当银行卡（首位不是卡组织 BIN）。"""
    from app.identify.rules import valid_bankcard, valid_luhn

    prefix = "202610180001234567"
    order_like = next(prefix + digit for digit in "0123456789" if valid_luhn(prefix + digit))
    assert valid_luhn(order_like) is True  # 先确认它确实过了 Luhn
    assert valid_bankcard(order_like) is False
    assert valid_bankcard("6222021234567894") is True

    result = analyze(f"订单号 {order_like}，已发货。")
    assert result.level.value == "SAFE"
    assert result.type_keys == []

def test_bare_sk_api_key_is_l3() -> None:
    """裸 sk- Key（HTTP 头/URL 里没有 key=value 上下文）也要判 L3。

    这是扩样到 165 条评测集后暴露的盲区：secret_kv 依赖 `api_key=...` 这类键名，
    `Authorization: Bearer sk-...` 会被漏掉。
    """
    key = "sk-ABCDEFGH12345678IJKLMNOP"
    result = analyze(f"GET /v2/export HTTP/1.1\r\nAuthorization: Bearer {key}\r\n")
    assert result.level.value == "L3"
    assert "secret" in result.type_keys
    assert result.direct is True

    # 反例：普通短横线单词、短 token 不应误报
    benign = analyze("字段 sk-feature-flag 的默认值是 on，部署脚本见 docs。")
    assert "secret" not in benign.type_keys
