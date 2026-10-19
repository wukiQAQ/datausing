"""L3 客户端：HTTP 契约、失败降级、按配置选择调用方式。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.identify.l3 import HttpJudgeClassifier, OllamaGuardClassifier, build_guard_classifier
from app.identify.types import Level


class _StubHandler(BaseHTTPRequestHandler):
    """最小 HTTP 桩：返回 ``server.payload``，用于验证客户端契约。"""

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        data = json.dumps(self.server.payload).encode("utf-8")  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: Any) -> None:  # 静音
        return


@pytest.fixture()
def judge_endpoint():
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_address[1]}/v1/judge"
    try:
        yield endpoint, server
    finally:
        server.shutdown()
        server.server_close()


def test_judge_success_maps_level(judge_endpoint) -> None:
    endpoint, server = judge_endpoint
    server.payload = {"risk": "Unsafe", "reason": "含身份证", "model": "qwen3guard-gen-0.6b"}
    client = HttpJudgeClassifier(endpoint, "qwen3guard-gen-0.6b", 2.0)
    verdict = client.judge("身份证 11010519491231002X")
    assert verdict is not None
    assert verdict.risk == "Unsafe"
    assert verdict.level == Level.HIGH
    assert verdict.reason == "含身份证"
    assert verdict.model == "qwen3guard-gen-0.6b"


@pytest.mark.parametrize(
    ("payload", "want_level"),
    [
        ({"risk": "Controversial", "reason": "手机号"}, Level.MID),
        ({"risk": "Safe", "reason": "无敏感"}, Level.SAFE),
    ],
)
def test_judge_risk_levels(judge_endpoint, payload: dict[str, str], want_level: Level) -> None:
    endpoint, server = judge_endpoint
    server.payload = payload
    verdict = HttpJudgeClassifier(endpoint, "m", 2.0).judge("x")
    assert verdict is not None and verdict.level == want_level


def test_judge_degraded_and_unknown_risk_return_none(judge_endpoint) -> None:
    endpoint, server = judge_endpoint
    server.payload = {"risk": "", "degraded": True, "reason": "模型异常"}
    assert HttpJudgeClassifier(endpoint, "m", 2.0).judge("x") is None

    server.payload = {"risk": "Maybe", "reason": "?"}
    assert HttpJudgeClassifier(endpoint, "m", 2.0).judge("x") is None


def test_judge_unreachable_endpoint_returns_none() -> None:
    # 回环上挑一个几乎不可能被占用的端口：连接失败即降级为 None
    client = HttpJudgeClassifier("http://127.0.0.1:9/v1/judge", "m", 0.5)
    assert client.judge("x") is None


def test_build_guard_classifier_modes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FLOWMASK_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FLOWMASK_ENABLE_L3", "0")
    assert build_guard_classifier(Settings.load()) is None

    monkeypatch.setenv("FLOWMASK_ENABLE_L3", "1")
    monkeypatch.setenv("FLOWMASK_L3_MODE", "http")
    monkeypatch.setenv("FLOWMASK_L3_ENDPOINT", "http://127.0.0.1:8092/v1/judge")
    http_client = build_guard_classifier(Settings.load())
    assert isinstance(http_client, HttpJudgeClassifier)

    monkeypatch.setenv("FLOWMASK_L3_MODE", "ollama")
    monkeypatch.setenv("FLOWMASK_L3_ENDPOINT", "http://127.0.0.1:11434/api/generate")
    ollama_client = build_guard_classifier(Settings.load())
    assert isinstance(ollama_client, OllamaGuardClassifier)
