"""四级脱敏执行 + 脱敏策略层。

模板与 Go 版一致，也是《需求分析》"四级分级脱敏"的落点：

| 级别 | 处置方式 | 示例 |
| --- | --- | --- |
| L3 高危 | 全屏蔽为类别占位 | ``11010519491231002X`` → ``[已屏蔽:身份证号]`` |
| L2 中危 | 保留首字符 + 星号 | ``13800138000`` → ``1******`` |
| L1 低危 | 前置警示、保留原文 | ``[低危] 13800138000`` |
| SAFE 安全 | 不处理 | 原文 |

只改命中片段、不动上下文，这样"这条数据是在什么业务里泄露的"仍然可读；
类别名用中文占位符，演示与报告里一眼能看懂被屏蔽的是什么。

在四级模板之外，还提供一层**脱敏策略**（第一次优化新增）：同一份数据可以按
``block`` / ``partial`` / ``hash`` / ``generalize`` / ``pseudonym`` / ``encrypt``
六种方式处理，供"导出脱敏文件"与前端下拉框复用。策略函数都是纯函数，不读全局
配置——密钥由调用方显式传入，便于测试与多租户场景。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import struct

from .types import EntityType, Hit, Level, Result, entity_label, parse_entity_type

MAX_STARS = 6


def _stars(length: int) -> str:
    return "*" * max(1, min(length - 1, MAX_STARS))


# 敏感词表（knowledge base）命中指向的是「类别词」而不是敏感值，
# 例如正文里出现「身份证」三个字本身，不该被替换成 [已屏蔽:身份证号]。
KEYWORD_RULE_PREFIX = "kw-"


def mask_value(value: str, entity: EntityType, level: Level) -> str:
    """按级别对单个敏感值做脱敏。"""
    if not value:
        return value
    if level == Level.HIGH:
        return f"[已屏蔽:{entity_label(entity)}]"
    if level == Level.MID:
        return value[0] + _stars(len(value))
    if level == Level.LOW:
        return f"[低危]{value}"
    return value


def _apply_replacements(text: str, hits: list[Hit], level: Level) -> str:
    """按命中区间单遍替换（``mask`` / ``mask_hits`` 共用，输出与旧实现逐字节一致）。

    跳过敏感词表命中（``kw-``：那是类别词本身，不是敏感值）与已覆盖的重叠区间。
    """
    parts: list[str] = []
    cursor = 0
    for hit in sorted(hits, key=lambda item: item.start):
        if hit.rule.startswith(KEYWORD_RULE_PREFIX):
            continue
        if hit.start < cursor:
            continue
        parts.append(text[cursor:hit.start])
        parts.append(mask_value(hit.value, hit.type, level))
        cursor = hit.end
    parts.append(text[cursor:])
    return "".join(parts)


def mask(text: str, result: Result, level: Level | None = None) -> str:
    """按命中区间替换脱敏副本；``level`` 可覆盖（用于"强制某种脱敏强度"的演示）。"""
    if not text or not result.hits:
        return text
    effective = level or result.level
    if effective == Level.SAFE:
        return text
    return _apply_replacements(text, result.hits, effective)


def mask_hits(text: str, hits: list[Hit], level: Level) -> str:
    """只给命中列表与级别，直接产出脱敏文本（脱敏算子的通用入口）。"""
    if not text or not hits or level == Level.SAFE:
        return text
    return _apply_replacements(text, hits, level)


# ---------------------------------------------------------------------------
# 脱敏策略层（第一次优化）：block / partial / hash / generalize / pseudonym / encrypt
# ---------------------------------------------------------------------------

#: 可逆加密片段前缀：``enc:<base64url(nonce + 密文)>``
ENCRYPTED_PREFIX = "enc:"
#: 密文片段正则（与 ``decrypt_encrypted_text`` 的还原口径一致）
ENCRYPTED_PATTERN = re.compile(r"enc:[A-Za-z0-9_\-=]+")

DEFAULT_STRATEGY = "partial"


def _hmac_hex(secret: str, value: str) -> str:
    """``HMAC-SHA256(secret, value)`` 的十六进制摘要；secret 为空时用空串盐。"""
    key = (secret or "").encode("utf-8")
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()


def normalize_strategy(key: str | None) -> str:
    """未知 / 空策略一律回退到 ``DEFAULT_STRATEGY``（partial）。"""
    text = (key or "").strip().lower()
    return text if text in MASK_STRATEGIES else DEFAULT_STRATEGY


def _coerce_entity(entity: EntityType | str | None) -> EntityType | None:
    """把类别键 / 中文名 / 枚举统一解析成 ``EntityType``；解析不了返回 ``None``。"""
    if isinstance(entity, EntityType):
        return entity
    if entity is None:
        return None
    return parse_entity_type(str(entity))


def _entity_key(entity: EntityType | str | None) -> str:
    parsed = _coerce_entity(entity)
    return parsed.value if parsed is not None else str(entity or "")


def _entity_label_text(entity: EntityType | str | None) -> str:
    """类别中文名；无法识别的类别按"其他"处理。"""
    parsed = _coerce_entity(entity)
    return entity_label(parsed) if parsed is not None else "其他"


# ---- 可逆加密：HMAC-SHA256 计数器流，纯标准库（不引入 cryptography） --------


def _keystream(secret: str, nonce: bytes, length: int) -> bytes:
    """由 ``HMAC-SHA256(secret, nonce || counter_be32)`` 拼出与明文等长的密钥流。"""
    key = (secret or "").encode("utf-8")
    chunks: list[bytes] = []
    produced = 0
    counter = 0
    while produced < length:
        chunks.append(hmac.new(key, nonce + struct.pack(">I", counter), hashlib.sha256).digest())
        produced += 32
        counter += 1
    return b"".join(chunks)[:length]


def encrypt_text(text: str, secret: str) -> str:
    """把一段明文加密成 ``enc:<base64url>`` 片段（同值每次密文不同，但都能还原）。

    加密强度够不上"密码学存储加密"（无认证、无防重放），定位是**同一请求内的可逆
    脱敏导出**：文件里的敏感值可以交给服务端再还原，但离线拿到文件的人读不出明文。
    与审计密钥分开：导出密钥泄露不影响审计链签名。
    """
    if not secret:
        raise ValueError("脱敏导出密钥未配置")
    plain = text.encode("utf-8")
    nonce = secrets.token_bytes(16)
    stream = _keystream(secret, nonce, len(plain))
    cipher = bytes(left ^ right for left, right in zip(plain, stream))
    token = base64.urlsafe_b64encode(nonce + cipher).decode("ascii").rstrip("=")
    return ENCRYPTED_PREFIX + token


def decrypt_encrypted_text(text: str, secret: str) -> tuple[str, int]:
    """把文本里的所有 ``enc:`` 片段还原成明文，返回 ``(新文本, 还原片段数)``。

    空密钥抛 ``ValueError("脱敏导出密钥未配置")``；片段格式/解码失败抛
    ``ValueError("密文片段无效")``——错误信息不含密文或明文，避免回显敏感内容。
    """
    if not secret:
        raise ValueError("脱敏导出密钥未配置")
    replaced = 0

    def _restore(match: re.Match[str]) -> str:
        nonlocal replaced
        token = match.group(0)[len(ENCRYPTED_PREFIX) :]
        try:
            padded = token + "=" * (-len(token) % 4)
            raw = base64.urlsafe_b64decode(padded.encode("ascii"))
            if len(raw) <= 16:
                raise ValueError("密文片段过短")
            nonce, cipher = raw[:16], raw[16:]
            stream = _keystream(secret, nonce, len(cipher))
            plain = bytes(left ^ right for left, right in zip(cipher, stream))
            restored = plain.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("密文片段无效") from exc
        replaced += 1
        return restored

    return ENCRYPTED_PATTERN.sub(_restore, text), replaced


# ---- 单值脱敏：六种策略 ----------------------------------------------------


def mask_value_strategy(
    value: str,
    entity: EntityType | str | None,
    strategy: str,
    secret: str = "",
) -> str:
    """按策略对单个敏感值做脱敏（纯函数；``secret`` 只被 hash / pseudonym / encrypt 使用）。

    - ``block``：整段变成 ``[已屏蔽:<类别>]``；
    - ``partial``：保留首字符 + 星号（与四级模板的 L2 口径一致）；
    - ``hash``：``h_`` + HMAC-SHA256 前 16 位十六进制，同值同结果、不可还原；
    - ``generalize``：按类别保留可读的结构信息（前几位 / 域名 / 后四位）；
    - ``pseudonym``：``类别_`` + HMAC 前 8 位，同值同结果；
    - ``encrypt``：``enc:`` 可逆密文（空密钥直接报错）。
    """
    if not value:
        return value
    key = normalize_strategy(strategy)

    if key == "block":
        return f"[已屏蔽:{_entity_label_text(entity)}]"

    if key == "partial":
        if len(value) <= 1:
            return "*"
        return value[0] + _stars(len(value))

    if key == "hash":
        return "h_" + _hmac_hex(secret, value)[:16]

    if key == "pseudonym":
        return f"{_entity_key(entity).upper()}_{_hmac_hex(secret, value)[:8]}"

    if key == "encrypt":
        return encrypt_text(value, secret)

    # key == "generalize"：按类别保留结构特征
    def safe_generalization(output: str) -> str:
        return '[已屏蔽]' if value in output else output
    entity_key = _entity_key(entity)
    if entity_key == EntityType.ID_CARD.value:
        return safe_generalization(value[:6] + "********")
    if entity_key == EntityType.PHONE.value:
        if len(value) < 7:
            return safe_generalization(value[:2] + "****")
        return safe_generalization(value[:3] + "****" + value[-2:])
    if entity_key == EntityType.BANK_CARD.value:
        return safe_generalization("****" + value[-4:])
    if entity_key == EntityType.EMAIL.value:
        _, separator, domain = value.partition("@")
        return safe_generalization("***@" + (domain if separator else ""))
    if entity_key == EntityType.NAME.value:
        return safe_generalization(value[0] + "*")
    if entity_key == EntityType.ADDRESS.value:
        return safe_generalization(value[:3] + "***")
    return safe_generalization(f"[{_entity_label_text(entity)}]")

# ---- 片段级脱敏：给 spans / hits 直接用 ------------------------------------


def _span_field(span: object, name: str) -> object:
    """从 dict 或对象里取 ``start`` / ``end`` / ``type`` 字段。"""
    if isinstance(span, dict):
        return span.get(name)
    return getattr(span, name, None)


def mask_spans_with_strategy(
    text: str,
    spans: object,
    strategy: str,
    secret: str = "",
    mappings: list | None = None,
) -> str:
    """按 ``spans``（dict 或带 start/end/type 属性的对象）脱敏整段文本。

    只处理 ``0 <= start < end <= len(text)`` 的合法片段；从后往前替换，避免前一次
    替换造成后面的偏移失效；重叠片段以右侧优先（与 ``mask_hits`` 的口径一致）。
    """
    if not text or not spans:
        return text
    cleaned: list[tuple[int, int, object]] = []
    for span in spans:  # type: ignore[union-attr]
        try:
            start = int(_span_field(span, "start"))  # type: ignore[arg-type]
            end = int(_span_field(span, "end"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if start < 0 or end > len(text) or start >= end:
            continue
        cleaned.append((start, end, _span_field(span, "type")))
    if not cleaned:
        return text
    key = normalize_strategy(strategy)

    parts: list[str] = []
    cursor = len(text)
    edits = []
    for start, end, entity in sorted(cleaned, key=lambda item: item[0], reverse=True):
        if end > cursor:
            continue
        parts.append(text[end:cursor])
        replacement = mask_value_strategy(text[start:end], entity, key, secret)
        parts.append(replacement)
        edits.append((start, end, entity, replacement))
        cursor = start
    parts.append(text[:cursor])
    if mappings is not None:
        shift = 0
        for start, end, entity, replacement in reversed(edits):
            if replacement != text[start:end]:
                mappings.append(dict(start=start, end=end, masked_start=start + shift,
                                     masked_end=start + shift + len(replacement),
                                     type=getattr(entity, 'value', entity), strategy=key))
            shift += len(replacement) - (end - start)
    return "".join(reversed(parts))


def mask_text_strategy(text: str, hits: list[Hit], strategy: str, secret: str = "") -> str:
    """把 ``Hit`` 列表转成 spans，再调 :func:`mask_spans_with_strategy`。"""
    if not text or not hits:
        return text
    spans = [
        {"start": hit.start, "end": hit.end, "type": hit.type.value}
        for hit in hits
        if not hit.rule.startswith(KEYWORD_RULE_PREFIX)
    ]
    return mask_spans_with_strategy(text, spans, strategy, secret)


# ---- 策略元数据：前端下拉框 / 接口文档都从这里取 ---------------------------

#: 示例统一用虚构数据（138 开头手机号、校验位合法的示例身份证号、example.com 邮箱）
_STRATEGY_EXAMPLES: tuple[tuple[str, str, str], ...] = (
    ("phone", EntityType.PHONE.value, "13812345678"),
    ("id_card", EntityType.ID_CARD.value, "11010519491231002X"),
    ("email", EntityType.EMAIL.value, "zhangsan@example.com"),
)
#: 示例脱敏结果用固定密钥生成（encrypt 每次随机，但都是能还原的合法密文）
_EXAMPLE_SECRET = "flowmask-demo-secret"

MASK_STRATEGIES: dict[str, dict] = {
    "block": {
        "key": "block",
        "label": "全屏蔽",
        "description": "命中片段整段替换为类别占位符，不可读、不可还原，安全性最高。",
        "reversible": False,
    },
    "partial": {
        "key": "partial",
        "label": "部分遮蔽",
        "description": "保留首字符、其余用星号遮蔽；既能对照位置，又读不出完整值（默认策略）。",
        "reversible": False,
    },
    "hash": {
        "key": "hash",
        "label": "哈希指纹",
        "description": "用服务端密钥做 HMAC-SHA256 取前 16 位；同值同指纹，可关联统计、不可还原。",
        "reversible": False,
    },
    "generalize": {
        "key": "generalize",
        "label": "泛化",
        "description": "按类别保留结构特征（证件前 6 位、邮箱域名、卡号后 4 位），可用于数据分析。",
        "reversible": False,
    },
    "pseudonym": {
        "key": "pseudonym",
        "label": "假名化",
        "description": "用「类别_哈希前 8 位」替换原值；同值同假名，可跨表关联、不可反推。",
        "reversible": False,
    },
    "encrypt": {
        "key": "encrypt",
        "label": "可逆加密",
        "description": "用服务端导出密钥加密为 enc: 密文，仅本机接口可还原；离线拿到文件读不出明文。",
        "reversible": True,
    },
}

# 用脱敏函数本身生成示例，保证文档里写的示例与真实输出永远一致
for _name, _meta in MASK_STRATEGIES.items():
    _meta["examples"] = [
        {
            "category": category,
            "raw": raw,
            "masked": mask_value_strategy(raw, entity_key, _name, _EXAMPLE_SECRET),
        }
        for category, entity_key, raw in _STRATEGY_EXAMPLES
    ]
del _name, _meta
