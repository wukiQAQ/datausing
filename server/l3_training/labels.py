"""L3 裁判任务的标签契约：四级分级、十类敏感信息、统一 JSON 输出。

训练侧与推理侧必须共用同一套字段，否则微调出来的模型接回 ``l3_service``
会出现"训练时输出 A 格式、线上按 B 格式解析"的错位。
"""

from __future__ import annotations

import json
import re

LEVELS = ("L3", "L2", "L1", "SAFE")

CATEGORIES = (
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

CATEGORY_LABELS = {
    "phone": "手机号",
    "id_card": "身份证号",
    "bank_card": "银行卡号",
    "email": "邮箱",
    "ip": "IP地址",
    "secret": "密钥凭据",
    "password": "密码",
    "name": "姓名",
    "address": "地址",
    "medical": "医疗信息",
}

LEVEL_HINTS = {
    "L3": "强敏感标识（证件号/银行卡/密钥/病历），泄露可直接定位自然人",
    "L2": "可识别个人的联系方式或住址（手机号/邮箱/地址/姓名组合）",
    "L1": "低风险线索（单独姓名、城市、工号），不足以定位到具体个人",
    "SAFE": "无个人信息，或明确是文档示例/测试数据/占位符",
}

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def target_json(level: str, categories: list[str] | tuple[str, ...], reason: str) -> str:
    """按契约拼出一行 JSON；类别按固定顺序排列，保证训练目标可复现。"""
    if level not in LEVELS:
        raise ValueError(f"非法级别：{level!r}，只能是 {LEVELS}")
    unknown = [c for c in categories if c not in CATEGORIES]
    if unknown:
        raise ValueError(f"非法类别：{unknown}")
    ordered = [c for c in CATEGORIES if c in set(categories)]
    payload = {"level": level, "categories": ordered, "reason": reason.strip()}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def parse_target(raw: str) -> dict[str, object] | None:
    """把模型输出解析回结构化目标；解析不出来或级别非法返回 ``None``。"""
    text = (raw or "").strip()
    if not text:
        return None
    match = _JSON_RE.search(text)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    level = str(parsed.get("level") or parsed.get("risk") or "").strip().upper()
    if level not in LEVELS:
        return None
    raw_categories = parsed.get("categories") or []
    if isinstance(raw_categories, str):
        raw_categories = re.split(r"[,，、/\s]+", raw_categories)
    categories = [str(c).strip().lower() for c in raw_categories if str(c).strip()]
    return {
        "level": level,
        "categories": [c for c in CATEGORIES if c in set(categories)],
        "reason": str(parsed.get("reason", "")).strip()[:200],
    }


def reason_for(level: str, categories: list[str] | tuple[str, ...]) -> str:
    """用模板生成训练用理由：不走大模型，保证理由与金标一致、无幻觉。"""
    if level not in LEVELS:
        raise ValueError(f"非法级别：{level!r}")
    names = [CATEGORY_LABELS.get(c, c) for c in CATEGORIES if c in set(categories)]
    if level == "SAFE":
        return "未检出个人信息，或内容明确标注为示例/测试数据"
    if not names:
        return f"命中{LEVEL_HINTS[level]}"
    joined = "、".join(names)
    return f"命中{joined}；{LEVEL_HINTS[level]}"


def level_from_row(row: dict[str, object]) -> str:
    """从评测集行取金标级别（兼容 ``level`` / ``gold_level`` 两种字段名）。"""
    level = str(row.get("level") or row.get("gold_level") or "").strip().upper()
    return level if level in LEVELS else ""


def categories_from_row(row: dict[str, object]) -> list[str]:
    """从评测集行取类别，过滤掉契约外的值并保持固定顺序。"""
    raw = row.get("types") or row.get("categories") or []
    if isinstance(raw, str):
        raw = re.split(r"[,，、/\s]+", raw)
    values = {str(item).strip().lower() for item in raw if str(item).strip()}
    return [c for c in CATEGORIES if c in values]
