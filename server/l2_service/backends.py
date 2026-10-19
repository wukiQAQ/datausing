"""L2 后端的可替换实现：mock（占位）/ onnx（真实模型）。

统一输出 ``Prediction``：命中的敏感类别及置信度 + "判为非敏感"的置信度（``benign``）。
后者用于抑制 L1 的形似误报（例如文档/测试数据里的示例号码）——这是"看上下文判断真假"
的落点，也是多模型协同相对纯规则的主要增益之一。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

# 服务端只输出"类别 → 置信度"，类别名与一体机的 EntityType 对齐
ALLOWED_TYPES = (
    "phone",
    "id_card",
    "bank_card",
    "email",
    "ip",
    "secret",
    "password",
    "name",
    "address",
    "medical",
)

MAX_BATCH = 32


@dataclass(slots=True)
class TypeScore:
    type: str
    confidence: float


@dataclass(slots=True)
class Prediction:
    types: list[TypeScore] = field(default_factory=list)
    benign: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "types": [{"type": t.type, "confidence": round(t.confidence, 4)} for t in self.types],
            "benign": round(self.benign, 4),
            "notes": list(self.notes),
        }


class Backend(Protocol):
    name: str

    def describe(self) -> dict[str, object]: ...

    def predict(self, texts: list[str]) -> list[Prediction]: ...


# --------------------------------------------------------------------------- mock


_ADDRESS_RE = re.compile(r"(?:省|自治区|市|区|县|旗|镇|乡|村|路|街|大道|道|巷|弄|号|栋|单元|室)")
_NAME_RE = re.compile(r"(?:客户|联系人|姓名|收件人|收货人|患者|员工|用户)[:：\s]{0,2}([\u4e00-\u9fa5]{2,4})")
_PASSWORD_RE = re.compile(r"(?:密码|口令|pwd|password|passwd)\s*[:=：]?\s*\S{4,}", re.IGNORECASE)
_SECRET_RE = re.compile(r"(?:token|密钥|secret|api[_-]?key|access[_-]?key)", re.IGNORECASE)
_MEDICAL_WORDS = (
    "诊断",
    "病历",
    "用药",
    "住院",
    "出院",
    "血压",
    "血糖",
    "既往史",
    "过敏史",
    "处方",
    "检验报告",
    "影像",
    "化疗",
    "手术记录",
    "门诊",
)
_BENIGN_MARKERS = (
    "示例",
    "样例",
    "测试",
    "测试数据",
    "测试号码",
    "假数据",
    "虚构",
    "仅供",
    "仅用于",
    "联调",
    "演示",
    "演示数据",
    "模板",
    "占位",
    "压测",
    "placeholder",
    "dummy",
    "demo",
    "test",
    "mock",
    "xxxx",
)


def _count_markers(text: str, markers: tuple[str, ...]) -> int:
    lowered = text.lower()
    return sum(1 for marker in markers if marker in lowered)


class MockBackend:
    """词表 + 启发式的"语义"占位实现。

    它刻意不做"更强的正则"：只判**类别是否存在**与**这段文字像不像示例/测试数据**，
    定位与脱敏仍然由 L1 负责。这样接真模型时，上层流水线的行为完全一致。
    """

    name = "mock-lexicon"

    def describe(self) -> dict[str, object]:
        return {
            "backend": self.name,
            "kind": "heuristic",
            "labels": list(ALLOWED_TYPES),
            "note": "占位实现：词表+上下文标记，用于跑通链路与增益对比；换真模型只需换后端",
        }

    def predict(self, texts: list[str]) -> list[Prediction]:
        return [self._predict_one(text) for text in texts]

    def _predict_one(self, text: str) -> Prediction:
        pred = Prediction()

        # 1) 地址：出现 ≥2 个行政区/道路号标记时认为存在地址信息
        address_hits = len(_ADDRESS_RE.findall(text))
        if address_hits >= 2:
            pred.types.append(TypeScore("address", min(0.6 + 0.05 * address_hits, 0.9)))

        # 2) 姓名：靠上下文词（"客户/联系人/姓名"…）+ 顿号后的双字名
        name_match = _NAME_RE.search(text)
        if name_match:
            pred.types.append(TypeScore("name", 0.78))

        # 3) 医疗：词表命中
        medical_hits = _count_markers(text, _MEDICAL_WORDS)
        if medical_hits:
            pred.types.append(TypeScore("medical", min(0.7 + 0.05 * medical_hits, 0.9)))

        # 4) 口令/密钥：字段名出现即认为有凭据语义（具体值由 L1 定位）
        if _PASSWORD_RE.search(text):
            pred.types.append(TypeScore("password", 0.8))
        if _SECRET_RE.search(text):
            pred.types.append(TypeScore("secret", 0.82))

        # 5) 非敏感判定：示例/测试/演示数据标记越多越确信
        benign_markers = _count_markers(text, _BENIGN_MARKERS)
        if benign_markers:
            # 1 个标记 0.75（刚好达到压制阈值），越多越接近 0.99（高危直达类也能被压制）
            pred.benign = min(0.75 + 0.08 * (benign_markers - 1), 0.99)
            pred.notes.append(f"benign_markers={benign_markers}")

        return pred


# -------------------------------------------------------------------------- onnx


class OnnxBackend:
    """真实模型后端：加载多标签分类 ONNX（int8）+ tokenizer。

    约定的模型目录（由 ``export_onnx.py`` 生成）：

    - ``model.int8.onnx``：多标签分类模型（输出 ``[batch, num_labels]`` 概率或 logits）
    - ``tokenizer.json``：HuggingFace fast tokenizer
    - ``labels.json``：``["phone", "id_card", ...]`` 与输出维度对齐

    依赖（可选安装）：``pip install onnxruntime tokenizers``。缺依赖或文件不全时
    ``describe()`` 会给出明确提示，``build_backend`` 回落到 mock，保证服务本身可用。
    """

    name = "onnx-roberta"

    def __init__(self, model_dir: Path, max_length: int = 128) -> None:
        self.model_dir = model_dir
        self.max_length = max_length
        self._session = None
        self._tokenizer = None
        self._labels: list[str] = []
        self._error: str | None = None
        try:
            self._load()
        except Exception as exc:  # pragma: no cover - 依赖外部文件
            self._error = f"{type(exc).__name__}: {exc}"

    def _load(self) -> None:  # pragma: no cover - 依赖外部模型文件
        import onnxruntime  # noqa: PLC0415
        from tokenizers import Tokenizer  # noqa: PLC0415

        model_path = self.model_dir / "model.int8.onnx"
        if not model_path.exists():
            model_path = self.model_dir / "model.onnx"
        if not model_path.exists():
            raise FileNotFoundError(f"{self.model_dir} 下没有 model.int8.onnx/model.onnx")
        labels_path = self.model_dir / "labels.json"
        if not labels_path.exists():
            raise FileNotFoundError(f"{self.model_dir} 下没有 labels.json")
        self._labels = json.loads(labels_path.read_text(encoding="utf-8"))
        self._tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
        self._tokenizer.enable_truncation(max_length=self.max_length)
        self._session = onnxruntime.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"]
        )

    def available(self) -> bool:
        return self._session is not None and self._tokenizer is not None

    def describe(self) -> dict[str, object]:
        return {
            "backend": self.name,
            "kind": "onnx",
            "model_dir": str(self.model_dir),
            "labels": list(self._labels),
            "ready": self.available(),
            "error": self._error,
        }

    def predict(self, texts: list[str]) -> list[Prediction]:  # pragma: no cover - 需真模型
        if not self.available():
            raise RuntimeError(self._error or "ONNX 后端未就绪")
        import numpy as np  # noqa: PLC0415

        encodings = [self._tokenizer.encode(text) for text in texts]
        max_len = max(len(enc.ids) for enc in encodings)
        input_ids = np.zeros((len(encodings), max_len), dtype=np.int64)
        attention = np.zeros((len(encodings), max_len), dtype=np.int64)
        for row, enc in enumerate(encodings):
            ids = enc.ids[:max_len]
            input_ids[row, : len(ids)] = ids
            attention[row, : len(ids)] = 1
        feeds = {"input_ids": input_ids, "attention_mask": attention}
        input_names = {i.name for i in self._session.get_inputs()}
        if "token_type_ids" in input_names:
            feeds["token_type_ids"] = np.zeros_like(input_ids)
        logits = self._session.run(None, feeds)[0]
        # sigmoid 得到多标签概率
        probs = 1.0 / (1.0 + np.exp(-logits))

        results: list[Prediction] = []
        for row in range(len(texts)):
            pred = Prediction()
            for index, label in enumerate(self._labels):
                confidence = float(probs[row][index])
                if label == "benign":
                    pred.benign = confidence
                elif confidence >= 0.5:
                    pred.types.append(TypeScore(label, confidence))
            results.append(pred)
        return results


class EchoBackend:
    """测试用后端：把输入里的特定标记映射成固定输出，便于断言契约而不依赖模型。"""

    name = "echo"

    def describe(self) -> dict[str, object]:
        return {"backend": self.name, "kind": "test"}

    def predict(self, texts: list[str]) -> list[Prediction]:
        results: list[Prediction] = []
        for text in texts:
            pred = Prediction()
            if "SENSITIVE" in text:
                pred.types.append(TypeScore("phone", 0.9))
            if "BENIGN" in text:
                pred.benign = 0.9
            results.append(pred)
        return results


# ------------------------------------------------------------------- 选择与健康


def build_backend() -> Backend:
    """按 ``FLOWMASK_L2_MODE`` 选择后端：mock（默认）| onnx | auto。"""
    mode = os.environ.get("FLOWMASK_L2_MODE", "mock").strip().lower()
    model_dir = Path(os.environ.get("FLOWMASK_L2_MODEL_DIR", "models/roberta-wwm-sens"))
    if mode == "echo":
        return EchoBackend()
    if mode == "onnx":
        return OnnxBackend(model_dir)
    if mode == "auto":
        backend = OnnxBackend(model_dir)
        return backend if getattr(backend, "available", lambda: False)() else MockBackend()
    return MockBackend()


def health_payload(backend: Backend, started_at: float) -> dict[str, object]:
    return {
        "ok": True,
        "service": "flowmask-l2",
        "backend": backend.name,
        "uptime_s": round(time.time() - started_at, 1),
        "max_batch": MAX_BATCH,
        "labels": list(ALLOWED_TYPES),
        "detail": backend.describe(),
    }
