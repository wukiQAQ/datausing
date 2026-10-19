"""L2 语义识别（RoBERTa-wwm-ext）接入层。

模型不放在本进程里：训练/推理是一个独立的 FastAPI 服务（``inference/``，后续实现），
本层只负责"按契约调用 + 超时熔断 + 失败降级"。这样一体机没有模型也能跑 L1-only，
答辩现场可以现场切换“启用 L2”演示升级链路。

契约（与 Go 侧 DLP 推理契约一致）：

    POST {l2_endpoint}
    {"items": [{"id": "seg-1", "content": "……"}]}
    ->
    {"items": [{"id": "seg-1", "types": [{"type": "id_card", "confidence": 0.93}]}]}
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from ..core.config import Settings
from .types import EntityType


@dataclass(slots=True)
class SemanticHit:
    """L2 给出的判定（不定位到字符区间，定位仍由 L1 负责）。

    - ``benign=False``：命中某个敏感类别，置信度在 ``confidence``；
    - ``benign=True``：这段文本更像"示例/测试数据"，``type`` 为空，
      ``confidence`` 是"判为非敏感"的置信度，用于压制 L1 的形似误报。
    """

    type: EntityType | None
    confidence: float
    source: str = "l2"
    benign: bool = False


class SemanticClassifier(Protocol):
    """L2 识别器接口，便于单测注入假实现。"""

    name: str

    def available(self) -> bool: ...

    def predict(self, text: str) -> list[SemanticHit]: ...


class HttpSemanticClassifier:
    """按 HTTP 契约调用 L2 服务；任何网络/协议错误都返回空列表（上层按 degraded 处理）。"""

    name = "l2-roberta"

    def __init__(self, endpoint: str, timeout_s: float) -> None:
        self.endpoint = endpoint
        self.timeout_s = timeout_s

    def available(self) -> bool:
        return bool(self.endpoint)

    def predict(self, text: str) -> list[SemanticHit]:
        payload = {"items": [{"id": "seg", "content": text[:4000]}]}
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return []

        if body.get("degraded"):
            return []

        hits: list[SemanticHit] = []
        for item in body.get("items", []):
            benign = float(item.get("benign") or 0.0)
            if benign > 0:
                hits.append(SemanticHit(type=None, confidence=benign, benign=True))
            for entry in item.get("types", []):
                try:
                    entity = EntityType(entry["type"])
                except (KeyError, ValueError):
                    continue
                hits.append(SemanticHit(type=entity, confidence=float(entry.get("confidence", 0.0))))
        return hits


def build_semantic_classifier(settings: Settings) -> SemanticClassifier | None:
    """按配置构造 L2 识别器；未启用时返回 None（调用方据此降级）。"""
    if not settings.enable_l2:
        return None
    return HttpSemanticClassifier(settings.l2_endpoint, settings.l2_timeout_s)
