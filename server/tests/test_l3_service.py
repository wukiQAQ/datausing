"""L3 护栏服务：输出解析、mock 判定、HTTP 契约与降级。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from l3_service.app import create_app  # noqa: E402
from l3_service.backends import (  # noqa: E402
    EchoGuardBackend,
    MockGuardBackend,
    build_backend,
    parse_guard_text,
)

ID_CARD = "11010519491231002X"


class RaisingBackend:
    """模拟模型崩溃：服务必须降级为 ``degraded=true`` 而不是 500。"""

    name = "raising"

    def describe(self) -> dict[str, object]:
        return {"mode": "raising"}

    def judge(self, text: str):  # noqa: ANN001 - 测试桩
        raise RuntimeError("guard exploded")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"risk": "Unsafe", "reason": "含身份证"}', ("Unsafe", "含身份证")),
        ("Safety: Safe\nCategories: None", ("Safe", "None")),
        ("Safety: Controversial", ("Controversial", "Safety: Controversial")),
        ("思考中……\n```json\n{\"risk\":\"Safe\",\"reason\":\"示例数据\"}\n```", ("Safe", "示例数据")),
        ("risk: unsafe", ("Unsafe", "risk: unsafe")),
    ],
)
def test_parse_guard_text_formats(raw: str, expected: tuple[str, str]) -> None:
    """Qwen3Guard 原生格式与 JSON 都必须能解析。"""
    parsed = parse_guard_text(raw)
    assert parsed is not None
    assert parsed[0] == expected[0]
    assert expected[1] in parsed[1]


def test_parse_guard_text_garbage_returns_none() -> None:
    assert parse_guard_text("") is None
    assert parse_guard_text("我不知道你在说什么") is None


@pytest.mark.parametrize(
    ("text", "risk"),
    [
        (f"姓名张三，身份证 {ID_CARD}", "Unsafe"),
        ("api_key=sk-ABCDEFGH12345678", "Unsafe"),
        ("客户手机 13800138000", "Controversial"),
        ("email=zhang.san@example.com", "Controversial"),
        ("这是一段演示示例文本（测试用）", "Safe"),
        ("今天天气不错", "Safe"),
    ],
)
def test_mock_backend_risk_levels(text: str, risk: str) -> None:
    result = MockGuardBackend().judge(text)
    assert result.risk == risk
    assert result.reason


def test_health_and_model_info() -> None:
    client = TestClient(create_app(MockGuardBackend()))
    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert health["backend"]["mode"] == "mock"
    info = client.get("/v1/model").json()
    assert info["model"] == "l3-mock"


def test_judge_contract() -> None:
    client = TestClient(create_app(MockGuardBackend()))
    response = client.post("/v1/judge", json={"text": f"身份证 {ID_CARD}"})
    assert response.status_code == 200
    body = response.json()
    assert body["risk"] == "Unsafe"
    assert body["degraded"] is False
    assert body["latency_ms"] >= 0


def test_judge_rejects_empty_text() -> None:
    client = TestClient(create_app(MockGuardBackend()))
    assert client.post("/v1/judge", json={"text": ""}).status_code == 422


def test_judge_degrades_instead_of_500() -> None:
    client = TestClient(create_app(RaisingBackend()))
    response = client.post("/v1/judge", json={"text": "张三 13800138000"})
    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is True
    assert "guard exploded" in body["reason"]


def test_echo_backend() -> None:
    client = TestClient(create_app(EchoGuardBackend()))
    assert client.post("/v1/judge", json={"text": "UNSAFE payload"}).json()["risk"] == "Unsafe"
    assert client.post("/v1/judge", json={"text": "normal"}).json()["risk"] == "Safe"


def test_build_backend_modes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FLOWMASK_L3_MODE", "mock")
    assert isinstance(build_backend(), MockGuardBackend)

    monkeypatch.setenv("FLOWMASK_L3_MODE", "echo")
    assert isinstance(build_backend(), EchoGuardBackend)

    # auto：权重目录不存在时退回 mock，保证 demo 一定能起
    monkeypatch.setenv("FLOWMASK_L3_MODE", "auto")
    monkeypatch.setenv("FLOWMASK_L3_MODEL_DIR", str(tmp_path / "missing"))
    assert isinstance(build_backend(), MockGuardBackend)


def test_resolve_dtype(monkeypatch) -> None:
    """FLOWMASK_L3_DTYPE 覆盖权重类型；auto 时 GPU 用 bf16、CPU 用 fp32。"""
    from types import SimpleNamespace

    from l3_service.backends import _resolve_dtype

    fake_torch = SimpleNamespace(
        bfloat16="bf16", float16="fp16", float32="fp32"
    )
    monkeypatch.delenv("FLOWMASK_L3_DTYPE", raising=False)
    assert _resolve_dtype(fake_torch, "cpu") == "fp32"
    assert _resolve_dtype(fake_torch, "cuda") == "bf16"
    monkeypatch.setenv("FLOWMASK_L3_DTYPE", "bf16")
    assert _resolve_dtype(fake_torch, "cpu") == "bf16"
    monkeypatch.setenv("FLOWMASK_L3_DTYPE", "fp16")
    assert _resolve_dtype(fake_torch, "cuda") == "fp16"
    monkeypatch.setenv("FLOWMASK_L3_DTYPE", "unknown")
    assert _resolve_dtype(fake_torch, "cpu") == "fp32"
