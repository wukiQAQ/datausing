"""脱敏导出接口测试：策略清单 / 即时导出 / 密文还原 / 文件导出（HTTP 层）。

全部走 ``TestClient``（``client`` 夹具按临时配置现建应用），不联网、不落库；
断言同时覆盖"下载响应头是否正确"与"导出内容零残留"。
"""

from __future__ import annotations

import json
from dataclasses import replace

from app.api.deps import settings_dep
from app.core.config import get_settings

PHONE = "13812345678"
ID_CARD = "11010519491231002X"
EMAIL = "zhangsan@example.com"
NAME = "张三"
TEXT = f"姓名：{NAME}，手机：{PHONE}，身份证：{ID_CARD}，邮箱：{EMAIL}。"

EXPORT_FLAGS = {"enable_l2": True, "enable_l3": True, "enable_ner": True}


def _export_body(**overrides) -> dict:
    body = {"text": TEXT, "content_type": "text", "strategy": "partial", "fmt": "auto"}
    body.update(EXPORT_FLAGS)
    body.update(overrides)
    return body


# ---- 策略清单 ---------------------------------------------------------------


def test_mask_strategies_endpoint(client) -> None:
    response = client.get("/api/v1/mask-strategies")
    assert response.status_code == 200
    body = response.json()
    assert body["default"] == "partial"
    keys = [item["key"] for item in body["strategies"]]
    assert keys == ["block", "partial", "hash", "generalize", "pseudonym", "encrypt"]
    for item in body["strategies"]:
        assert item["label"] and item["description"]
        assert item["reversible"] is (item["key"] == "encrypt")
        categories = {example["category"] for example in item["examples"]}
        assert categories == {"phone", "id_card", "email"}
        assert all({"category", "raw", "masked"} == set(example) for example in item["examples"])


# ---- 粘贴文本导出 -----------------------------------------------------------


def test_export_text_returns_download(client) -> None:
    response = client.post("/api/v1/detect/export", json=_export_body(strategy="block", fmt="txt"))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="flowmask-export-')
    assert disposition.endswith('.txt"')
    content = response.content.decode("utf-8")
    # L2 语义证据会给整段打上 requires_full_mask，block 策略按「整段 → 类别占位」处理，
    # 不再按 span 逐值替换（见 app/tasks/document_export.py 的 _masked_texts）；
    # 整段没有单一实体类别，类别占位回落到「其他」。
    assert content.strip() == "[已屏蔽:其他]"
    for value in (NAME, PHONE, ID_CARD, EMAIL):
        assert value not in content


def test_export_json_is_valid_and_clean(client) -> None:
    document = json.dumps(
        {"user": {"phone": PHONE, "email": EMAIL}, "id_card": ID_CARD},
        ensure_ascii=False,
    )
    response = client.post(
        "/api/v1/detect/export",
        json=_export_body(text=document, content_type="json", strategy="partial", fmt="json"),
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    text = response.content.decode("utf-8")
    for value in (PHONE, ID_CARD, EMAIL):
        assert value not in text
    parsed = json.loads(text)
    assert set(parsed) == {"user", "id_card"}
    assert parsed["user"]["phone"] == "1******"


def test_export_encrypt_then_decrypt_roundtrip(client) -> None:
    encrypted = client.post("/api/v1/detect/export", json=_export_body(strategy="encrypt", fmt="txt"))
    assert encrypted.status_code == 200
    ciphertext = encrypted.content.decode("utf-8")
    assert "enc:" in ciphertext
    assert PHONE not in ciphertext and ID_CARD not in ciphertext

    restored = client.post("/api/v1/detect/decrypt", json={"ciphertext": ciphertext})
    assert restored.status_code == 200
    body = restored.json()
    # 整段 requires_full_mask 时只有一个密文 token，解密只替换一次
    assert body["replaced"] >= 1
    assert body["strategy"] == "encrypt"
    assert PHONE in body["text"] and ID_CARD in body["text"]


def test_export_hash_is_deterministic(client) -> None:
    first = client.post("/api/v1/detect/export", json=_export_body(strategy="hash", fmt="txt"))
    second = client.post("/api/v1/detect/export", json=_export_body(strategy="hash", fmt="txt"))
    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    assert b"h_" in first.content


def test_export_unknown_strategy_is_400(client) -> None:
    response = client.post("/api/v1/detect/export", json=_export_body(strategy="rot13"))
    assert response.status_code == 400
    assert response.json()["detail"] == "不支持的脱敏策略"


def test_export_unknown_fmt_is_400(client) -> None:
    response = client.post("/api/v1/detect/export", json=_export_body(fmt="xml"))
    assert response.status_code == 400
    assert response.json()["detail"] == "不支持的输出格式"


def test_export_text_to_structured_fmt_is_422(client) -> None:
    """纯文本显式导成 csv/sql/json 会被拒绝：结构化格式必须与原文形态一致。

    否则按目标格式重解析原文拿不到命中定位，会把未脱敏原文原样写回（历史实现曾如此）。
    """
    for fmt in ("csv", "sql", "json"):
        response = client.post("/api/v1/detect/export", json=_export_body(fmt=fmt))
        assert response.status_code == 422, fmt
        assert PHONE not in response.text, fmt


def test_export_invalid_content_type_is_400(client) -> None:
    # content_type=docx 只能走上传入口；这里正文不是 docx 二进制
    response = client.post("/api/v1/detect/export", json=_export_body(content_type="docx"))
    assert response.status_code == 400
    assert response.json()["detail"] == "请求内容无法处理（格式与 content_type 不一致或为空）"


def test_export_bad_json_body_is_400_without_echo(client) -> None:
    response = client.post(
        "/api/v1/detect/export",
        json=_export_body(text="{不是合法 JSON", content_type="json"),
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail == "请求内容无法处理（格式与 content_type 不一致或为空）"
    assert PHONE not in detail


def test_export_missing_text_is_422(client) -> None:
    response = client.post("/api/v1/detect/export", json={"content_type": "text"})
    assert response.status_code == 422


def test_encrypt_export_without_secret_is_400(client) -> None:
    settings = get_settings()
    client.app.dependency_overrides[settings_dep] = lambda: replace(settings, export_secret="")
    try:
        response = client.post("/api/v1/detect/export", json=_export_body(strategy="encrypt", fmt="txt"))
        assert response.status_code == 400
        assert response.json()["detail"] == "脱敏导出密钥未配置"
    finally:
        client.app.dependency_overrides.pop(settings_dep, None)


# ---- 密文还原 ---------------------------------------------------------------


def test_decrypt_without_ciphertext_is_400(client) -> None:
    response = client.post("/api/v1/detect/decrypt", json={"ciphertext": "这里没有任何密文"})
    assert response.status_code == 400
    assert response.json()["detail"] == "没有可还原的密文片段"


def test_decrypt_invalid_ciphertext_is_400(client) -> None:
    response = client.post("/api/v1/detect/decrypt", json={"ciphertext": "enc:abc"})
    assert response.status_code == 400
    assert response.json()["detail"] == "密文片段无效"


# ---- 上传文件导出 -----------------------------------------------------------


def test_file_export_txt(client) -> None:
    response = client.post(
        "/api/v1/detect/file/export",
        files={"file": ("customers.txt", TEXT.encode("utf-8"), "text/plain")},
        data={"strategy": "partial", "fmt": "txt", "enable_l2": "true", "enable_l3": "true", "enable_ner": "true"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["content-disposition"].startswith('attachment; filename="flowmask-export-')
    content = response.content.decode("utf-8")
    for value in (PHONE, ID_CARD, EMAIL):
        assert value not in content


def test_file_export_json_auto(client) -> None:
    raw = json.dumps({"phone": PHONE}, ensure_ascii=False).encode("utf-8")
    response = client.post(
        "/api/v1/detect/file/export",
        files={"file": ("data.json", raw, "application/json")},
        data={"strategy": "partial", "fmt": "auto", "enable_l2": "true"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert PHONE not in response.content.decode("utf-8")


def test_file_export_png_is_415(client) -> None:
    response = client.post(
        "/api/v1/detect/file/export",
        files={"file": ("photo.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        data={"strategy": "partial", "fmt": "auto"},
    )
    assert response.status_code == 415
    assert response.json()["detail"] == "图片文件暂不支持导出（需先安装 OCR 可选依赖）"


def test_file_export_unsupported_type_is_415(client) -> None:
    response = client.post(
        "/api/v1/detect/file/export",
        files={"file": ("a.bin", b"\x00\x01")},
        data={"strategy": "partial", "fmt": "auto"},
    )
    assert response.status_code == 415
    assert response.json()["detail"] == "不支持的文件类型"


def test_file_export_unknown_strategy_is_400(client) -> None:
    response = client.post(
        "/api/v1/detect/file/export",
        files={"file": ("a.txt", TEXT.encode("utf-8"), "text/plain")},
        data={"strategy": "rot13", "fmt": "txt"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "不支持的脱敏策略"


# ---- 既有接口行为不变 -------------------------------------------------------


def test_existing_detect_endpoints_unchanged(client) -> None:
    detected = client.post("/api/v1/detect", json={"text": f"身份证号：{ID_CARD}"})
    assert detected.status_code == 200
    body = detected.json()
    assert set(body) >= {
        "content_type",
        "segments",
        "findings",
        "by_level",
        "duration_ms",
        "degraded",
        "masked_document",
    }
    assert body['result_id'] and body['policy_version']
    assert ID_CARD not in json.dumps(body, ensure_ascii=False)

    uploaded = client.post(
        "/api/v1/detect/file",
        files={"file": ("a.txt", f"身份证号：{ID_CARD}".encode("utf-8"), "text/plain")},
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["content_type"] == "text"

    assert client.get("/api/v1/formats").status_code == 200
