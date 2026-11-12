"""L2 语义识别客户端。

模型运行在独立的 ``l2_service`` 中。客户端负责按契约发出请求并报告失败，
流水线保留已取得的识别结果。

契约（与 Go 侧 DLP 推理契约一致）：

    POST {l2_endpoint}
    {"items": [{"id": "seg-1", "content": "……"}]}
    ->
    {"items": [{"id": "seg-1", "types": [{"type": "id_card", "confidence": 0.93}]}]}
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Protocol

from ..core.config import Settings
from .inference import post_json
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
    """按 HTTP 契约调用 L2 服务，空命中和推理失败分别处理。"""

    name = "l2-roberta"

    def __init__(self, endpoint: str, timeout_s: float) -> None:
        self.endpoint = endpoint
        self.timeout_s = timeout_s

    def available(self) -> bool:
        return bool(self.endpoint)

    def predict(self, text: str) -> list[SemanticHit]:
        payload = {"items": [{"id": "seg", "content": text[:4000]}]}
        body = post_json(self.endpoint, payload, self.timeout_s)
        items = body.get("items")
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            raise ValueError("L2 返回的条目数量或格式不匹配")
        item = items[0]
        if item.get("id") != "seg" or item.get("degraded") or not isinstance(item.get("types"), list):
            raise ValueError("L2 返回无效或降级条目")
        hits: list[SemanticHit] = []
        benign = _confidence(item.get("benign", 0.0))
        if benign > 0:
            hits.append(SemanticHit(type=None, confidence=benign, benign=True))
        for entry in item["types"]:
            try:
                entity = EntityType(entry["type"])
            except ValueError:  # 兼容模型新增的类别。
                continue
            hits.append(SemanticHit(type=entity, confidence=_confidence(entry["confidence"])))
        return hits


def _confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("L2 置信度必须为数值")
    if not isfinite(value) or not 0 <= value <= 1:
        raise ValueError("L2 置信度必须位于 [0, 1]")
    return float(value)


def build_semantic_classifier(settings: Settings) -> SemanticClassifier | None:
    """端点决定客户端能力，默认开关由检测入口单独处理。"""
    endpoint = settings.l2_endpoint.strip()
    if not endpoint:
        return None
    return HttpSemanticClassifier(endpoint, settings.l2_timeout_s)
