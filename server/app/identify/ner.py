"""L1.5 实体识别：给人名/地址补一层不依赖字段名的检测。

为什么需要它：L1 的 ``name_kv`` / ``address_kv`` 规则依赖"字段名: 值"这种上下文
（``姓名: 张三``），一旦是自由叙述文本（``张三住在鼓楼区``），正则就抓不到。
中文 NER 模型能直接给出实体区间，正好补这个缺口。

设计约束：
- **只加类别，不抬级别**：人名/地名的权重与置信度沿用规则层的量级
  （name 0.40/0.70、address 0.50/0.70），单独出现最多 L1；与手机号/证件共现时
  才靠共现修正升级，避免"识别到人名就判中危"的误报；
- **懒加载 + 可降级**：模型加载失败、推理异常都只记 ``degraded``，不影响 L1 结论；
- **默认关闭**：``FLOWMASK_ENABLE_NER=1`` 才启用，默认不引入额外依赖与延迟。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .types import EntityType, Hit

# 与 rules.py 里 name_kv / address_kv 保持同一量级：NER 的价值是"覆盖率"，不是"提级"。
NER_NAME_WEIGHT = 0.40
NER_NAME_CONFIDENCE = 0.70
NER_ADDRESS_WEIGHT = 0.50
NER_ADDRESS_CONFIDENCE = 0.70

# 中文 NER 数据集（MSRA/PeopleDaily/RaNER）常用的标签集；同时兼容 BIO 前缀与英文名。
_NAME_LABELS = {"PER", "PERSON", "NAME", "NR", "NB", "NH"}
_ADDRESS_LABELS = {"LOC", "ADDRESS", "ADDR", "GPE", "NS", "ORG"}  # ORG 归到地址类：机构名同样能定位到主体

_BIO_PREFIXES = ("B-", "I-", "S-", "E-", "U-", "M-", "L-")


def map_ner_label(label: str) -> EntityType | None:
    """把 NER 标签映射到敏感类别；不认识的标签返回 ``None``（宁缺毋滥）。"""
    key = (label or "").strip().upper()
    for prefix in _BIO_PREFIXES:
        if key.startswith(prefix):
            key = key[len(prefix) :].strip()
            break
    if key in _NAME_LABELS:
        return EntityType.NAME
    if key in _ADDRESS_LABELS:
        return EntityType.ADDRESS
    return None


@dataclass(frozen=True, slots=True)
class NerSpan:
    """一个实体区间：``start``/``end`` 是 Python 字符串下标（与 Hit 一致）。"""

    start: int
    end: int
    label: str


def spans_to_hits(spans: list[NerSpan], text: str) -> list[Hit]:
    """把实体区间转成 ``Hit``；越界、空串、未知标签全部丢弃。"""
    hits: list[Hit] = []
    for span in spans:
        if span.start < 0 or span.end > len(text) or span.start >= span.end:
            continue
        entity = map_ner_label(span.label)
        if entity is None:
            continue
        if entity is EntityType.NAME:
            weight, confidence = NER_NAME_WEIGHT, NER_NAME_CONFIDENCE
        else:
            weight, confidence = NER_ADDRESS_WEIGHT, NER_ADDRESS_CONFIDENCE
        hits.append(
            Hit(
                type=entity,
                start=span.start,
                end=span.end,
                value=text[span.start : span.end],
                weight=weight,
                confidence=confidence,
                rule=f"ner-{entity.value}",
            )
        )
    return hits


class NerDetector(Protocol):
    name: str

    def available(self) -> bool: ...

    def detect(self, text: str) -> list[NerSpan]: ...


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _server_dir() -> Path:
    return Path(__file__).resolve().parents[2]


# --------------------------------------------------------------- 标准 token 分类
class TokenNerDetector:
    """标准 ``AutoModelForTokenClassification``（BIO 标签）后端。"""

    name = "token-ner"

    def __init__(self, model_dir: str | Path, *, device: str = "cpu", max_length: int = 256) -> None:
        self.model_dir = str(model_dir)
        self.device = device
        self.max_length = max_length
        self._tokenizer = None
        self._model = None
        self._id2label: dict[int, str] = {}

    def available(self) -> bool:
        return Path(self.model_dir).is_dir()

    def _load(self) -> None:
        if self._model is not None:
            return
        import torch  # noqa: F401  (延迟导入：没装 torch 的部署不该被拖垮)
        from transformers import AutoModelForTokenClassification, AutoTokenizer

        self._tokenizer = AutoTokenizer.from_pretrained(self.model_dir)
        self._model = AutoModelForTokenClassification.from_pretrained(self.model_dir)
        self._model.to(self.device)
        self._model.eval()
        self._id2label = {int(k): str(v) for k, v in (self._model.config.id2label or {}).items()}

    def detect(self, text: str) -> list[NerSpan]:
        if not text:
            return []
        self._load()
        import torch

        encoded = self._tokenizer(
            text,
            return_offsets_mapping=True,
            return_tensors="pt",
            truncation=True,
            max_length=self.max_length,
        )
        offsets = encoded.pop("offset_mapping")[0].tolist()
        encoded = {key: value.to(self.device) for key, value in encoded.items()}
        with torch.no_grad():
            logits = self._model(**encoded).logits[0]
        preds = logits.argmax(dim=-1).tolist()
        spans: list[NerSpan] = []
        current: NerSpan | None = None
        for (start, end), pred in zip(offsets, preds, strict=False):
            if start == end:  # 特殊符号 / 空白
                continue
            label = self._id2label.get(int(pred), str(pred))
            entity = map_ner_label(label)
            if entity is None or label.upper().startswith("B-") or current is None:
                if current is not None:
                    spans.append(current)
                    current = None
                if entity is not None:
                    current = NerSpan(start=start, end=end, label=label)
                continue
            # I- 连续标签：同一实体的相邻片段合并
            current = NerSpan(start=current.start, end=end, label=current.label)
        if current is not None:
            spans.append(current)
        return spans


# --------------------------------------------------------------- ModelScope RaNER
class RaNerDetector:
    """ModelScope RaNER 通用中文 NER（依赖 ``modelscope`` 的 pipeline）。"""

    name = "raner"

    def __init__(self, model_dir: str | Path, *, device: str = "cpu") -> None:
        self.model_dir = str(model_dir)
        self.device = device
        self._pipe = None

    def available(self) -> bool:
        return Path(self.model_dir).is_dir()

    def _load(self) -> None:
        if self._pipe is not None:
            return
        from modelscope.pipelines import pipeline

        self._pipe = pipeline(
            "named-entity-recognition", model=self.model_dir, device=self.device
        )

    def detect(self, text: str) -> list[NerSpan]:
        if not text:
            return []
        self._load()
        assert self._pipe is not None
        result = self._pipe(text)
        spans: list[NerSpan] = []
        cursor = 0
        for item in (result or {}).get("output") or []:
            span_text = str(item.get("span") or "").strip()
            if not span_text:
                continue
            index = text.find(span_text, cursor)
            if index < 0:
                index = text.find(span_text)
            if index < 0:
                continue
            spans.append(NerSpan(start=index, end=index + len(span_text), label=str(item.get("type") or "")))
            cursor = index + len(span_text)
        return spans


def build_ner_detector(settings=None) -> NerDetector | None:
    """按环境变量装配 NER 检测器；未启用返回 ``None``。

    - ``FLOWMASK_ENABLE_NER``：总开关（默认 0，避免默认拉起额外模型）
    - ``FLOWMASK_NER_MODEL_DIR``：模型目录（默认 ``server/models/raner-ner``）
    - ``FLOWMASK_NER_BACKEND``：``auto``（默认）/``raner``/``token``
    - ``FLOWMASK_NER_DEVICE``：``cpu``（默认）/``cuda``
    """
    enabled = getattr(settings, "enable_ner", None)
    if enabled is None:
        enabled = _env_flag("FLOWMASK_ENABLE_NER")
    if not enabled:
        return None

    backend = (os.environ.get("FLOWMASK_NER_BACKEND") or "auto").strip().lower()
    device = os.environ.get("FLOWMASK_NER_DEVICE") or "cpu"
    raw_dir = getattr(settings, "ner_model_dir", "") or os.environ.get("FLOWMASK_NER_MODEL_DIR", "")
    model_dir = Path(raw_dir) if raw_dir else _server_dir() / "models" / "raner-ner"

    if backend == "token":
        return TokenNerDetector(model_dir, device=device)
    if backend == "raner":
        return RaNerDetector(model_dir, device=device)
    # auto：目录里有 tokenizer.json 就按标准 token 分类加载，否则按 RaNER
    if (model_dir / "tokenizer.json").is_file():
        return TokenNerDetector(model_dir, device=device)
    return RaNerDetector(model_dir, device=device)
