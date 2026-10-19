"""L3 大模型兜底与解释（Qwen3Guard-Stream 0.6B）。

同样走"本地 HTTP 推理服务"而不是把权重加载进 API 进程：默认用 Ollama 的
``/api/generate``（``ollama run qwen3guard`` 之后即可调用），也兼容任何实现了同样
接口的本地服务。模型给出 Safe / Controversial / Unsafe 三级风险，本层再映射到
项目的 L1/L2/L3 四级，并把模型理由写进 finding 的 ``reason`` 供答辩展示。

失败/超时一律降级：返回 ``None``，由调用方保留 L1/L2 的结论并标记 ``degraded``。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from ..core.config import Settings
from .types import Level

GUARD_PROMPT = """你是数据安全审核员。判断下面这段文本是否包含需要保护的敏感个人信息，
并按风险分级回答。只输出一行 JSON：{{"risk": "Safe|Controversial|Unsafe", "reason": "一句话理由"}}

文本：
{content}
"""


@dataclass(slots=True)
class GuardVerdict:
    """L3 的判定结果。"""

    risk: str
    reason: str
    model: str

    @property
    def level(self) -> Level:
        """护栏三级风险 → 项目四级。Controversial 记为中危，避免 Safe 被误放。"""
        mapping = {"unsafe": Level.HIGH, "controversial": Level.MID, "safe": Level.SAFE}
        return mapping.get(self.risk.strip().lower(), Level.SAFE)


class GuardClassifier(Protocol):
    name: str

    def available(self) -> bool: ...

    def judge(self, text: str) -> GuardVerdict | None: ...


class OllamaGuardClassifier:
    """调用 Ollama /api/generate 的护栏客户端。"""

    name = "l3-qwen3guard"

    def __init__(self, endpoint: str, model: str, timeout_s: float) -> None:
        self.endpoint = endpoint
        self.model = model
        self.timeout_s = timeout_s

    def available(self) -> bool:
        return bool(self.endpoint and self.model)

    def judge(self, text: str) -> GuardVerdict | None:
        prompt = GUARD_PROMPT.format(content=text[:2000])
        payload = {"model": self.model, "prompt": prompt, "stream": False, "format": "json"}
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
            return None

        raw = body.get("response", "")
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (ValueError, TypeError):
            return None
        risk = str(parsed.get("risk", "")).strip()
        if risk.lower() not in {"safe", "controversial", "unsafe"}:
            return None
        return GuardVerdict(risk=risk, reason=str(parsed.get("reason", ""))[:200], model=self.model)


class HttpJudgeClassifier:
    """调用本仓 ``l3_service`` 的 ``/v1/judge`` 的护栏客户端。

    与 Ollama 版的区别：判定逻辑（prompt 模板、Qwen3Guard 原生输出解析）都在服务侧，
    主服务只发 ``{"text": ...}``、收归一化的 ``{"risk","reason","model","degraded"}``，
    这样换模型/换运行时不用改主服务。
    """

    name = "l3-qwen3guard-http"

    def __init__(self, endpoint: str, model: str, timeout_s: float) -> None:
        self.endpoint = endpoint
        self.model = model
        self.timeout_s = timeout_s

    def available(self) -> bool:
        return bool(self.endpoint)

    def judge(self, text: str) -> GuardVerdict | None:
        payload = {"text": text[:4000]}
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
            return None
        if not isinstance(body, dict) or body.get("degraded"):
            return None
        risk = str(body.get("risk", "")).strip()
        if risk.lower() not in {"safe", "controversial", "unsafe"}:
            return None
        return GuardVerdict(
            risk=risk,
            reason=str(body.get("reason", ""))[:200],
            model=str(body.get("model") or self.model),
        )


def build_guard_classifier(settings: Settings) -> GuardClassifier | None:
    """按配置构造 L3 客户端；未启用时返回 None。

    ``L3_MODE`` 决定调用方式：``http``（本仓 l3_service）/ ``ollama``（/api/generate）。
    """
    if not settings.enable_l3:
        return None
    if settings.l3_mode == "ollama":
        return OllamaGuardClassifier(settings.l3_endpoint, settings.l3_model, settings.l3_timeout_s)
    return HttpJudgeClassifier(settings.l3_endpoint, settings.l3_model, settings.l3_timeout_s)
