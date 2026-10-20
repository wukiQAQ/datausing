"""接口层端到端测试：检测、任务、导出、统计、规则、审计、鉴权。"""

from __future__ import annotations

import json
import time

ID_CARD = "11010519491231002X"
PHONE = "13800138000"
CUSTOM_PATTERN = r"工号[:：]\s*([A-Z]{2}\d{6})"


def _wait_done(client, task_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/v1/tasks/{task_id}").json()
        if body["status"] in {"done", "failed"}:
            return body
        time.sleep(0.05)
    raise AssertionError("任务在超时时间内未完成")


def test_health(client) -> None:
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok" and body["version"]


def test_detect_text_returns_masked_only(client) -> None:
    response = client.post("/api/v1/detect", json={"text": f"身份证号：{ID_CARD}"})
    assert response.status_code == 200
    body = response.json()
    finding = body["findings"][0]
    assert finding["level"] == "L3" and "已屏蔽" in finding["masked"]
    # 整个响应里不能出现原文敏感值（只允许哈希与脱敏副本）
    assert ID_CARD not in json.dumps(body, ensure_ascii=False)


def test_detect_returns_highlight_spans(client) -> None:
    text = f"客户张三，手机 {PHONE}，身份证 {ID_CARD}"
    body = client.post("/api/v1/detect", json={"text": text}).json()
    spans = body["findings"][0]["spans"]
    assert spans, "应返回命中区间供前端高亮"
    # 区间必须能切回原文命中值，且类别键/中文名都在
    types = {span["type"] for span in spans}
    assert {"phone", "id_card"} <= types
    cut = {text[span["start"]:span["end"]] for span in spans}
    assert PHONE in cut and ID_CARD in cut
    assert all(span["label"] and span["rule"] for span in spans)


def test_ui_page_and_root_redirect(client) -> None:
    page = client.get("/ui/")
    assert page.status_code == 200
    assert "FlowMask" in page.text and "左右对照" in page.text
    redirect = client.get("/", follow_redirects=False)
    assert redirect.status_code in {302, 307}
    # 构建过正式前端（web/dist）时优先跳 /app/，否则回落到自带单页 /ui
    assert redirect.headers["location"] in {"/ui", "/app/"}


def test_frontend_dist_is_optional(monkeypatch) -> None:
    """未构建正式前端时必须优雅降级：没有 /app 挂载，根路径回落到 /ui。"""
    import app.main as main_module
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main_module, "FRONTEND_DIR", main_module.Path("/nonexistent-flowmask-dist"))
    fallback = TestClient(main_module.create_app())
    assert fallback.get("/app/").status_code == 404
    redirect = fallback.get("/", follow_redirects=False)
    assert redirect.headers["location"] == "/ui"


def test_frontend_serves_built_assets() -> None:
    """构建过正式前端时 /app/ 能直接打开（Vue3 页面），无构建环境则跳过。"""
    from pathlib import Path

    import pytest
    from fastapi.testclient import TestClient

    import app.main as main_module

    dist = Path(main_module.__file__).resolve().parents[2] / "web" / "dist"
    if not dist.is_dir():
        pytest.skip("未构建 web/dist，跳过正式前端静态资源用例")
    page = TestClient(main_module.create_app()).get("/app/")
    assert page.status_code == 200
    assert "<div id=\"app\">" in page.text


def test_detect_file(client) -> None:
    response = client.post(
        "/api/v1/detect/file",
        files={"file": ("a.txt", f"联系电话 {PHONE}\n".encode(), "text/plain")},
    )
    assert response.status_code == 200
    assert response.json()["findings"][0]["level"] == "L2"


def test_detect_invalid_level_is_400(client) -> None:
    response = client.post("/api/v1/detect", json={"text": "abc", "min_level": "L9"})
    assert response.status_code == 400


def test_detect_unsupported_upload_is_415(client) -> None:
    response = client.post("/api/v1/detect/file", files={"file": ("a.bin", b"\x00\x01")})
    assert response.status_code == 415


def test_task_flow_findings_export_and_stats(client) -> None:
    files = {"file": ("a.txt", f"姓名：张三\n联系电话 {PHONE}\n".encode(), "text/plain")}
    task_id = client.post("/api/v1/tasks", files=files).json()["task_id"]
    task = _wait_done(client, task_id)
    assert task["status"] == "done" and task["total_findings"] >= 1

    findings = client.get(f"/api/v1/tasks/{task_id}/findings").json()
    assert findings["total"] >= 1
    assert findings["items"][0]["masked"]

    cases = {
        "jsonl": '"type": "meta"',
        "json": '"findings"',
        "csv": "path,level",
        "sql": "INSERT INTO findings",
    }
    for fmt, marker in cases.items():
        response = client.get(f"/api/v1/tasks/{task_id}/export", params={"format": fmt})
        assert response.status_code == 200, fmt
        assert marker in response.text, fmt

    stats = client.get("/api/v1/stats").json()
    assert stats["total_findings"] >= 1
    assert stats["by_level"]["L2"] >= 1 or stats["by_level"]["L3"] >= 1

    # 审计记录在任务完成后应已写入，且链条可校验
    audit = client.get("/api/v1/audit").json()
    assert audit["total"] >= 1
    verify = client.get("/api/v1/audit/verify").json()
    assert verify["ok"] is True

    exported = client.get("/api/v1/audit/export", params={"format": "jsonl"})
    assert exported.status_code == 200 and exported.text.strip()


def test_task_not_found_and_bad_export_format(client) -> None:
    assert client.get("/api/v1/tasks/nope").status_code == 404
    assert client.get("/api/v1/tasks/nope/export").status_code == 404
    files = {"file": ("a.txt", b"plain text", "text/plain")}
    task_id = client.post("/api/v1/tasks", files=files).json()["task_id"]
    _wait_done(client, task_id)
    assert client.get(f"/api/v1/tasks/{task_id}/export", params={"format": "xml"}).status_code == 400


def test_custom_rule_is_effective_and_reversible(client) -> None:
    assert len(client.get("/api/v1/rules").json()) == 12
    created = client.post(
        "/api/v1/rules",
        json={
            "id": "employee_no",
            "name": "工号",
            "type": "secret",
            "pattern": CUSTOM_PATTERN,
            "weight": 1.0,
            "confidence": 0.95,
        },
    )
    assert created.status_code == 201

    hit = client.post("/api/v1/detect", json={"text": "工号：AB123456"}).json()
    assert hit["findings"][0]["level"] == "L3"

    client.patch("/api/v1/rules/employee_no", json={"enabled": False})
    assert client.post("/api/v1/detect", json={"text": "工号：AB123456"}).json()["findings"] == []

    # 停用内置规则：手机号规则下线后，纯手机号不再命中
    assert client.patch("/api/v1/rules/mobile", json={"enabled": False}).status_code == 200
    assert client.post("/api/v1/detect", json={"text": f"电话 {PHONE}"}).json()["findings"] == []

    # 删除覆盖行即恢复内置规则
    assert client.delete("/api/v1/rules/mobile").status_code == 200
    assert client.post("/api/v1/detect", json={"text": f"电话 {PHONE}"}).json()["findings"]
    assert client.delete("/api/v1/rules/employee_no").status_code == 200
    assert len(client.get("/api/v1/rules").json()) == 12


def test_rule_validation_errors(client) -> None:
    bad_type = client.post(
        "/api/v1/rules",
        json={"id": "x_rule", "name": "x", "type": "unknown", "pattern": "abc", "weight": 0.5},
    )
    assert bad_type.status_code == 422
    bad_regex = client.post(
        "/api/v1/rules",
        json={"id": "x_rule", "name": "x", "type": "secret", "pattern": "([", "weight": 0.5},
    )
    assert bad_regex.status_code == 422
    duplicate = client.post(
        "/api/v1/rules",
        json={"id": "mobile", "name": "x", "type": "secret", "pattern": "abc", "weight": 0.5},
    )
    assert duplicate.status_code == 409


def test_bearer_token_enforced(tmp_env, monkeypatch) -> None:
    monkeypatch.setenv("FLOWMASK_API_TOKEN", "s3cret")
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    with TestClient(create_app()) as protected:
        assert protected.get("/api/v1/health").status_code == 401
        assert protected.get("/api/v1/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
        ok = protected.get("/api/v1/health", headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200

def test_health_reports_ner_availability(client) -> None:
    """默认没开 NER 时，/health 明确告诉前端“不可用”，前端据此隐藏开关。"""
    body = client.get("/api/v1/health").json()
    assert body["ner"] is False
    assert body["ner_backend"] == ""


def test_detect_enable_ner_switch(client, monkeypatch) -> None:
    """enable_ner 只在显式打开时生效：打开后补出人名，关闭时保持纯 L1 结果。"""
    from app.api import deps
    from app.identify.ner import NerSpan
    from app.identify.pipeline import DetectionPipeline

    class FakeNer:
        name = "fake-ner"

        def available(self) -> bool:
            return True

        def detect(self, text: str) -> list[NerSpan]:
            start = text.find("张三")
            return [NerSpan(start=start, end=start + 2, label="B-PER")] if start >= 0 else []

    monkeypatch.setattr(deps, "_pipeline", DetectionPipeline(ner=FakeNer()))  # type: ignore[arg-type]
    text = "张三昨天从福州赶过来，商量下季度的方案。"

    plain = client.post("/api/v1/detect", json={"text": text}).json()
    assert plain["findings"] == []

    with_ner = client.post("/api/v1/detect", json={"text": text, "enable_ner": True}).json()
    assert with_ner["findings"], "开 NER 后应该补出姓名"
    finding = with_ner["findings"][0]
    assert "name" in finding["types"]
    span = next(item for item in finding["spans"] if item["type"] == "name")
    assert text[span["start"] : span["end"]] == "张三"
    assert "ner" in finding["model_chain"]
