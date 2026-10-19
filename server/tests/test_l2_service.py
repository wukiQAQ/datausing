"""L2 语义识别服务：HTTP 契约、降级、以及与一体机客户端的联调。"""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:  # 便于直接 import l2_service
    sys.path.insert(0, str(SERVER_ROOT))

from app.identify.l2 import HttpSemanticClassifier, SemanticHit  # noqa: E402
from l2_service.app import create_app  # noqa: E402
from l2_service.backends import EchoBackend, MockBackend, Prediction, TypeScore  # noqa: E402


class RaisingBackend:
    """模拟模型加载失败/推理崩溃：服务必须降级而不是 500。"""

    name = "raising"

    def describe(self) -> dict[str, object]:
        return {"backend": self.name}

    def predict(self, texts: list[str]) -> list[Prediction]:
        raise RuntimeError("model exploded")


def test_health_reports_backend() -> None:
    client = TestClient(create_app(EchoBackend()))
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["backend"] == "echo"
    assert body["max_batch"] == 32
    assert "phone" in body["labels"]


def test_classify_echo_contract() -> None:
    client = TestClient(create_app(EchoBackend()))
    response = client.post(
        "/v1/classify",
        json={"items": [{"id": "a", "content": "SENSITIVE 内容"}, {"id": "b", "content": "BENIGN 示例"}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "echo"
    assert body["degraded"] is False
    assert body["items"][0]["types"] == [{"type": "phone", "confidence": 0.9}]
    # 非敏感判定走独立字段，不混进 types
    assert body["items"][1]["types"] == []
    assert body["items"][1]["benign"] == 0.9


def test_classify_rejects_oversized_batch() -> None:
    client = TestClient(create_app(EchoBackend()))
    items = [{"id": str(index), "content": "x"} for index in range(33)]
    assert client.post("/v1/classify", json={"items": items}).status_code == 413


def test_classify_rejects_empty_batch() -> None:
    client = TestClient(create_app(EchoBackend()))
    assert client.post("/v1/classify", json={"items": []}).status_code == 422


def test_classify_degrades_when_backend_fails() -> None:
    client = TestClient(create_app(RaisingBackend()))
    response = client.post("/v1/classify", json={"items": [{"id": "a", "content": "任意"}]})
    assert response.status_code == 200
    body = response.json()
    assert body["degraded"] is True
    assert body["items"][0]["degraded"] is True
    assert body["items"][0]["types"] == []


def test_mock_backend_detects_address_and_benign() -> None:
    backend = MockBackend()
    sensitive, benign = backend.predict(
        ["客户张伟住在福建省福州市鼓楼区五四路100号，联系电话13900139000。", "示例：手机号 13800138000 仅用于联调测试"]
    )
    assert any(item.type == "address" for item in sensitive.types)
    assert sensitive.benign == 0.0
    assert benign.benign >= 0.75
    assert benign.notes


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def live_service() -> str:
    """真起一个 uvicorn（echo 后端），验证"客户端 ↔ 服务"的 HTTP 契约与降级路径。"""
    import uvicorn

    port = _free_port()
    config = uvicorn.Config(
        create_app(EchoBackend()),
        host="127.0.0.1",
        port=port,
        log_level="error",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    if not server.started:  # pragma: no cover - 端口被占用等环境问题
        pytest.skip("uvicorn 未能启动")
    yield f"http://127.0.0.1:{port}/v1/classify"
    server.should_exit = True
    thread.join(timeout=5)


def test_http_client_parses_hits_and_benign(live_service: str) -> None:
    classifier = HttpSemanticClassifier(live_service, timeout_s=2.0)
    assert classifier.available()
    hits = classifier.predict("SENSITIVE 文本")
    assert [hit.type.value for hit in hits if hit.type is not None] == ["phone"]

    benign_hits = classifier.predict("BENIGN 文本")
    assert benign_hits and benign_hits[0].benign is True
    assert benign_hits[0].confidence == pytest.approx(0.9)


def test_http_client_degrades_on_dead_endpoint() -> None:
    classifier = HttpSemanticClassifier(f"http://127.0.0.1:{_free_port()}/v1/classify", timeout_s=0.5)
    assert classifier.predict("任意文本") == []


def test_semantic_hit_benign_defaults() -> None:
    hit = SemanticHit(type=None, confidence=0.5)
    assert hit.benign is False
    assert hit.source == "l2"
    assert hit.type is None


def test_type_score_rounding() -> None:
    pred = Prediction(types=[TypeScore("phone", 0.123456)], benign=0.74999)
    payload = pred.to_dict()
    assert payload["types"][0]["confidence"] == 0.1235
    assert payload["benign"] == 0.75
