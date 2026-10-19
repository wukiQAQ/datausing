"""L3 护栏后端的统一抽象：真实 Qwen3Guard 与离线 mock 两种实现。

设计原则与 ``l2_service`` 保持一致：
- 后端只做“判定”，不做脱敏/评分，返回归一化的 ``Safe/Controversial/Unsafe`` 与一句理由；
- 真实模型按需懒加载（导入 torch 失败时给出可照做的报错，而不是启动即崩）；
- mock 后端让“没有权重也能演示与写测试”，演示链路不依赖下载。
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Protocol

RISKS = ("Safe", "Controversial", "Unsafe")

# Qwen3Guard 原生输出形如：
#   Safety: Safe
#   Categories: ...
# 也有新版会输出 JSON；两种都要能解析，否则接上模型反而"看不懂输出"。
_SAFETY_RE = re.compile(r"Safety\s*[:：]\s*(Safe|Unsafe|Controversial)", re.IGNORECASE)
_CATEGORIES_RE = re.compile(r"Categories\s*[:：]\s*(.+)", re.IGNORECASE)
_RISK_RE = re.compile(r'"?risk"?\s*[:：]\s*"?(Safe|Unsafe|Controversial)', re.IGNORECASE)


def parse_guard_text(raw: str) -> tuple[str, str] | None:
    """把模型原始输出解析成 ``(risk, reason)``；解析不出来返回 ``None``。

    兼容三类输出：JSON、``Safety: ...`` 原生格式、以及夹在思考过程里的自由文本。
    """
    text = (raw or "").strip()
    if not text:
        return None
    import json

    # 1) 整段是 JSON，或 JSON 被包在 ```json ... ``` 里
    candidate = text
    if "```" in candidate:
        blocks = re.findall(r"```(?:json)?\s*(.+?)```", candidate, re.DOTALL)
        if blocks:
            candidate = blocks[0].strip()
    for piece in (candidate, text[text.find("{") : text.rfind("}") + 1] if "{" in text else ""):
        if not piece:
            continue
        try:
            parsed = json.loads(piece)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            risk = str(parsed.get("risk") or parsed.get("safety") or "").strip()
            if risk.lower() in {"safe", "unsafe", "controversial"}:
                return risk.capitalize(), str(parsed.get("reason", ""))[:200]

    # 2) 原生 Safety/Categories 格式
    match = _SAFETY_RE.search(text) or _RISK_RE.search(text)
    if match:
        risk = match.group(1).capitalize()
        cat = _CATEGORIES_RE.search(text)
        reason = cat.group(1).strip() if cat else text.splitlines()[0][:200]
        return risk, reason[:200]
    return None


@dataclass(slots=True)
class JudgeResult:
    """一次 L3 判定的结果。"""

    risk: str
    reason: str
    model: str
    latency_ms: float

    def to_dict(self) -> dict[str, object]:
        return {
            "risk": self.risk,
            "reason": self.reason,
            "model": self.model,
            "latency_ms": round(self.latency_ms, 2),
        }


class GuardBackend(Protocol):
    name: str

    def describe(self) -> dict[str, object]: ...

    def judge(self, text: str) -> JudgeResult: ...


# --------------------------------------------------------------------------- mock

_BENIGN_MARKERS = ("测试", "示例", "样例", "演示", "模板", "占位", "仅供", "仅用于", "假数据", "虚构")
_ID_CARD_RE = re.compile(r"\b\d{17}[\dXx]\b")
_LUHN_RE = re.compile(r"\b\d{16}\b")
_MEDICAL_WORDS = ("病历", "诊断", "处方", "病史", "检查报告")
_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{12,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")
_PASSWORD_RE = re.compile(r"(?i)(password|passwd|pwd|密码)\s*[:=：]\s*\S+")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


class MockGuardBackend:
    """启发式 mock：不需要权重，给测试与离线演示用。

    规则很“笨”但方向正确：有明确证件/密钥/口令 → Unsafe；只有姓名、地址、邮箱 →
    Controversial（信息敏感但单条不构成高危）；带“测试/示例”等标记 → Safe。
    """

    name = "l3-mock"

    def describe(self) -> dict[str, object]:
        return {"mode": "mock", "model": self.name, "note": "启发式护栏，无需权重"}

    def judge(self, text: str) -> JudgeResult:
        started = time.perf_counter()
        reason = ""
        risk = "Safe"
        if any(marker in text for marker in _BENIGN_MARKERS):
            risk, reason = "Safe", "存在示例/测试标记，按文档示例处理"
        elif _SECRET_RE.search(text) or _PASSWORD_RE.search(text):
            risk, reason = "Unsafe", "含密钥或口令凭据"
        elif _ID_CARD_RE.search(text) or any(
            _luhn_ok(m.group(0)) for m in _LUHN_RE.finditer(text)
        ) or any(word in text for word in _MEDICAL_WORDS):
            risk, reason = "Unsafe", "含身份证/银行卡/医疗等强敏感信息"
        elif re.search(r"1[3-9]\d{9}", text):
            risk, reason = "Controversial", "含手机号，属可识别到个人的敏感信息"
        elif re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text) or re.search(r"(省|市|区|路|街|号)", text):
            risk, reason = "Controversial", "含联系方式或地址类个人信息"
        else:
            risk, reason = "Safe", "未发现敏感个人信息"
        return JudgeResult(risk=risk, reason=reason, model=self.name, latency_ms=(time.perf_counter() - started) * 1000)


# --------------------------------------------------------------------------- 真实模型


class TransformersGuardBackend:
    """Qwen3Guard-Gen 系列（transformers）后端；按需懒加载，失败时给可照做的提示。"""

    def __init__(self, model_dir: str, device: str = "auto", max_new_tokens: int = 64) -> None:
        self.model_dir = model_dir
        self.device = device
        self.max_new_tokens = max_new_tokens
        self.name = os.path.basename(model_dir.rstrip("/\\")) or "qwen3guard"
        self._tokenizer = None
        self._model = None
        self._torch = None

    # ---- 加载 -----------------------------------------------------------
    def _require(self) -> None:
        try:
            import torch  # noqa: F401
            from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: F401
        except ImportError as exc:  # pragma: no cover - 依赖缺失时给指引
            raise RuntimeError(
                "L3 需要 torch 与 transformers 才能加载 Qwen3Guard：\n"
                "  pip install torch --index-url https://download.pytorch.org/whl/cu128\n"
                "  pip install transformers"
            ) from exc
        if self._model is None:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            self._torch = torch
            dtype = torch.bfloat16 if self.device != "cpu" else torch.float32
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_dir, trust_remote_code=True)
            self._model = AutoModelForCausalLM.from_pretrained(
                self.model_dir, torch_dtype=dtype, trust_remote_code=True
            )
            if self.device == "auto":
                self.device = "cuda" if torch.cuda.is_available() else "cpu"
            self._model.to(self.device)
            self._model.eval()

    def describe(self) -> dict[str, object]:
        return {"mode": "hf", "model": self.name, "model_dir": self.model_dir, "device": self.device}

    # ---- 判定 -----------------------------------------------------------
    def judge(self, text: str) -> JudgeResult:
        self._require()
        assert self._tokenizer is not None and self._model is not None and self._torch is not None
        started = time.perf_counter()
        messages = [{"role": "user", "content": text[:4000]}]
        prompt = self._tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = self._tokenizer([prompt], return_tensors="pt").to(self._model.device)
        with self._torch.inference_mode():
            output = self._model.generate(
                **inputs, max_new_tokens=self.max_new_tokens, do_sample=False, pad_token_id=0
            )
        generated = self._tokenizer.decode(
            output[0][inputs["input_ids"].shape[1] :], skip_special_tokens=True
        )
        parsed = parse_guard_text(generated)
        if parsed is None:
            return JudgeResult(
                risk="Controversial",
                reason=f"模型输出无法解析：{generated.strip()[:120]}",
                model=self.name,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        risk, reason = parsed
        return JudgeResult(
            risk=risk, reason=reason, model=self.name, latency_ms=(time.perf_counter() - started) * 1000
        )


class EchoGuardBackend:
    """联调用：按固定关键词回固定风险，方便验证接口与降级链路。"""

    name = "l3-echo"

    def describe(self) -> dict[str, object]:
        return {"mode": "echo", "model": self.name, "note": "固定规则，仅用于联调"}

    def judge(self, text: str) -> JudgeResult:
        started = time.perf_counter()
        risk = "Unsafe" if "UNSAFE" in text else "Safe"
        return JudgeResult(
            risk=risk, reason=f"echo:{risk}", model=self.name, latency_ms=(time.perf_counter() - started) * 1000
        )


def build_backend() -> GuardBackend:
    """按环境变量构造后端：``FLOWMASK_L3_MODE = mock | hf | echo | auto``。"""
    mode = os.environ.get("FLOWMASK_L3_MODE", "auto").strip().lower()
    model_dir = os.environ.get(
        "FLOWMASK_L3_MODEL_DIR",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "qwen3guard-gen-0.6b"),
    )
    device = os.environ.get("FLOWMASK_L3_DEVICE", "auto").strip().lower()
    if mode == "mock":
        return MockGuardBackend()
    if mode == "echo":
        return EchoGuardBackend()
    if mode == "hf":
        return TransformersGuardBackend(model_dir=model_dir, device=device)
    # auto：权重目录存在就用真模型，否则退回 mock（保证 demo 永远起得来）
    if os.path.isdir(model_dir) and os.listdir(model_dir):
        return TransformersGuardBackend(model_dir=model_dir, device=device)
    return MockGuardBackend()
