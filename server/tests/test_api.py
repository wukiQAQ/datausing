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


def test_detection_model_switches_override_defaults(tmp_env, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.identify.l2 import HttpSemanticClassifier
    from app.identify.l3 import GuardVerdict, HttpJudgeClassifier
    from app.main import create_app

    calls = []

    def predict(self, text):
        calls.append("l2")
        return []

    def judge(self, text):
        calls.append("l3")
        return GuardVerdict(risk="Safe", reason="", model="test")

    monkeypatch.setenv("FLOWMASK_ENABLE_L2", "1")
    monkeypatch.setenv("FLOWMASK_ENABLE_L3", "0")
    get_settings.cache_clear()
    monkeypatch.setattr(HttpSemanticClassifier, "predict", predict)
    monkeypatch.setattr(HttpJudgeClassifier, "judge", judge)
    text = "联系 13800138000"
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/detect", json={
            "text": text, "enable_l2": False, "enable_l3": True,
        })
        assert response.status_code == 200
        result = response.json()
        assert calls == ["l3"]
        assert result["effective_options"]["enable_l2"] is False
        assert result["effective_options"]["enable_l3"] is True
        assert result["engine_runs"] == {"l2": {"disabled": 1}, "l3": {"executed": 1}}
        assert result["findings"][0]["engine_runs"] == {"l2": "disabled", "l3": "executed"}
        assert result["coverage"] == "complete"

        calls.clear()
        result = client.post("/api/v1/detect", json={"text": text}).json()
        assert calls == ["l2"]
        assert result["effective_options"]["enable_l2"] is True

        calls.clear()
        result = client.post(
            "/api/v1/detect/file", params={"enable_l2": "false"},
            files={"file": ("sample.txt", text.encode())},
        ).json()
        assert result["effective_options"]["enable_l2"] is False
        assert calls == []

        response = client.post("/api/v1/tasks/text", json={"text": text, "enable_l2": False})
        task = _wait_done(client, response.json()["task_id"])
        assert task["options"]["enable_l2"] is False
        assert calls == []


def test_health(client) -> None:
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok" and body["version"]
    # /health 不暴露服务器绝对路径（只回文件名）
    assert body["db_path"] == "flowmask.db"


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


def test_masked_document_keeps_safe_text(client) -> None:
    """安全文本没有命中时，脱敏副本应回退原文，而不是空串。"""
    text = "这是一段没有任何敏感信息的普通说明文本。"
    body = client.post("/api/v1/detect", json={"text": text}).json()
    assert body["findings"] == []
    masked = body["masked_document"]
    assert masked["content_type"] == "text"
    assert masked["masked"] == text


def test_masked_document_ignores_min_level_filter(client) -> None:
    """min_level 只筛展示的 findings，脱敏副本仍要覆盖低危命中。"""
    body = client.post("/api/v1/detect", json={"text": f"联系电话 {PHONE}", "min_level": "L3"}).json()
    assert body["findings"] == []
    masked = body["masked_document"]["masked"]
    assert PHONE not in masked, "低危命中被筛掉后不能在脱敏副本里回显原文"


def test_task_text_rejects_safe_mask_level(client) -> None:
    """落库任务不能选 SAFE：库里只存脱敏副本，原文不允许落库。"""
    response = client.post("/api/v1/tasks/text", json={"text": f"电话 {PHONE}", "mask_level": "SAFE"})
    assert response.status_code == 400
    assert "mask_level" in response.json()["detail"]
    # 即时检测仍然允许"不脱敏"（不落库）
    assert client.post("/api/v1/detect", json={"text": "普通文本", "mask_level": "SAFE"}).status_code == 200


def test_task_text_validates_payload_types_and_size(client) -> None:
    """粘贴文本任务走 Pydantic 模型：类型错误/超长是 422，不再 500 或静默转串。"""
    assert client.post("/api/v1/tasks/text", json={"text": {"a": 1}}).status_code == 422
    assert client.post("/api/v1/tasks/text", json={"text": "x", "mask_level": 123}).status_code == 422
    big = "a" * 200_001
    assert client.post("/api/v1/tasks/text", json={"text": big}).status_code == 422


def test_operator_header_rejects_overlong_value(client) -> None:
    response = client.post(
        "/api/v1/traffic/ingest",
        json={"source": "agent", "content_type": "text", "segments": [{"path": "$", "text": "普通文本"}]},
        headers={"X-Operator-Id": "a" * 65},
    )
    assert response.status_code == 400
    assert "64" in response.json()["detail"]


def test_csv_cell_sanitizer_blocks_formulas() -> None:
    from app.tasks.export import sanitize_csv_cell

    assert sanitize_csv_cell("=1+1") == "'=1+1"
    assert sanitize_csv_cell("+66") == "'+66"
    assert sanitize_csv_cell("-2") == "'-2"
    assert sanitize_csv_cell("@SUM(A1)") == "'@SUM(A1)"
    assert sanitize_csv_cell("正常文本") == "正常文本"
    assert sanitize_csv_cell(12) == 12


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
    from pathlib import Path

    import app.main as main_module
    from fastapi.testclient import TestClient

    monkeypatch.setattr(main_module, "FRONTEND_DIR", Path("/nonexistent-flowmask-dist"))
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


def test_detect_xlsx_upload(client) -> None:
    from xlsx_fixtures import make_xlsx, row, text_cell

    data = make_xlsx([("客户表", row(1, text_cell("A1", "手机号")) + row(2, text_cell("A2", PHONE)))])
    response = client.post(
        "/api/v1/detect/file",
        files={
            "file": (
                "客户表.xlsx",
                data,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["content_type"] == "xlsx"
    assert body["findings"]
    assert body["masked_document"]["content_type"] == "xlsx"
    assert body["masked_document"]["segments"][0]["path"] == "客户表!A2"
    # 响应里不能出现原文手机号（只允许脱敏副本）
    assert PHONE not in json.dumps(body, ensure_ascii=False)


def test_detect_xlsx_export_download(client) -> None:
    from app.parsers import parse_xlsx
    from xlsx_fixtures import make_xlsx, row, text_cell

    data = make_xlsx([("客户表", row(1, text_cell("A1", "手机号")) + row(2, text_cell("A2", PHONE)))])
    response = client.post(
        "/api/v1/detect/file/export",
        files={"file": ("客户表.xlsx", data, "application/octet-stream")},
        data={"strategy": "partial", "fmt": "auto"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert "attachment" in response.headers["content-disposition"]
    parsed = parse_xlsx(response.content, source="masked.xlsx")
    assert [segment.path for segment in parsed.segments] == ["客户表!A2"]
    assert PHONE not in parsed.segments[0].text


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
    # 内置规则条数会随迭代增减（如新增 sk- 密钥规则），这里用动态基线，
    # 只要求"是一套完整规则"，避免每加一条规则就改测试。
    baseline = len(client.get("/api/v1/rules").json())
    assert baseline >= 12
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
    assert len(client.get("/api/v1/rules").json()) == baseline


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
        json={"id": "mobile", "name": "x", "type": "secret", "pattern": r"(\d{6})", "weight": 0.5},
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

def test_upload_size_limited_before_full_read(tmp_env, monkeypatch) -> None:
    """上传超过 max_upload_mb 时立即 413，不再把整个文件读进内存。"""
    monkeypatch.setenv("FLOWMASK_MAX_UPLOAD_MB", "1")
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    payload = b"a" * (2 * 1024 * 1024)
    files = {"file": ("big.txt", payload)}
    with TestClient(create_app()) as protected:
        assert protected.post("/api/v1/detect/file", files=files).status_code == 413
        assert protected.post("/api/v1/tasks", files=files).status_code == 413
        batch = protected.post("/api/v1/tasks/batch", files=[("files", ("big.txt", payload))])
        assert batch.status_code == 201
        assert "超过上限" in batch.json()["items"][0]["error"]


def test_health_reports_ner_availability(client) -> None:
    """默认没开 NER 时，/health 明确告诉前端“不可用”，前端据此隐藏开关。"""
    body = client.get("/api/v1/health").json()
    assert body["ner"] is False
    assert body["ner_backend"] == ""


def test_detect_enable_ner_switch(client, monkeypatch) -> None:
    """enable_ner 只在显式打开时生效：打开后补出地名，关闭时保持纯 L1 结果。"""
    from app.api import deps
    from app.identify.ner import NerSpan
    from app.identify.pipeline import DetectionPipeline

    class FakeNer:
        name = "fake-ner"

        def available(self) -> bool:
            return True

        def detect(self, text: str) -> list[NerSpan]:
            start = text.find("宁德")
            return [NerSpan(start=start, end=start + 2, label="B-LOC")] if start >= 0 else []

    monkeypatch.setattr(deps, "_pipeline", DetectionPipeline(ner=FakeNer()))  # type: ignore[arg-type]
    # 用「宁德」而不是「福州」：地名库只收省会/主要地级市，宁德既不在地名库、
    # 又没有省/市/区/路后缀与方位词，L1 一定抓不到，只有 NER 能补出来。
    text = "他昨天从宁德赶过来，商量下季度的方案。"

    plain = client.post("/api/v1/detect", json={"text": text}).json()
    assert plain["findings"] == []

    with_ner = client.post("/api/v1/detect", json={"text": text, "enable_ner": True}).json()
    assert with_ner["findings"], "开 NER 后应该补出地名"
    finding = with_ner["findings"][0]
    assert "address" in finding["types"]
    span = next(item for item in finding["spans"] if item["type"] == "address")
    assert text[span["start"] : span["end"]] == "宁德"
    assert "ner" in finding["model_chain"]

