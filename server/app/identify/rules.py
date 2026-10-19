"""L1：规则快筛。

与 Go 版 ``internal/dlp/rules.go`` 一一对应，共 12 条规则。设计取舍：

- **规则带权重与置信度**：权重是"类别严重度"，置信度是"这条规则本身的可靠程度"，
  两者相乘得到该命中的证据强度，供评分层使用（不是简单二值判定）。
- **能在本地算的校验一定算**：身份证 MOD 11-2、银行卡 Luhn、IPv4 段范围校验，
  用来剔除"形似"的数字串，把精确率拉上去。
- **中文键名 + 全角标点**：真实业务文本里 ``"api_key"："..."``、``姓名：张三``
  这类写法很常见，规则必须同时兼容半角/全角冒号与引号。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .types import EntityType, Hit

Pattern = re.Pattern[str]


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    type: EntityType
    pattern: Pattern
    weight: float
    confidence: float
    group: int = 1
    validate: Callable[[str], bool] | None = None


def valid_idcard18(value: str) -> bool:
    """GB 11643-1999 身份证校验位（MOD 11-2）。"""
    if len(value) != 18:
        return False
    body, check = value[:17], value[17].upper()
    if not body.isdigit():
        return False
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    total = sum(int(digit) * weight for digit, weight in zip(body, weights))
    return "10X98765432"[total % 11] == check


def valid_luhn(value: str) -> bool:
    """银行卡 Luhn 校验（接受 16~19 位，允许中间空格/连字符）。"""
    digits = [int(ch) for ch in value if ch.isdigit()]
    if not 16 <= len(digits) <= 19:
        return False
    checksum = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


# 卡组织 BIN 首位：Visa=4、Master/JCB/Amex=3/5、银联=62（首位 6）。
# 光靠 Luhn 不够：19 位订单号/流水号约有 1/10 概率"恰好"通过校验，会变成高危误报。
_BANKCARD_LEAD = frozenset({"3", "4", "5", "6"})


def valid_bankcard(value: str) -> bool:
    """银行卡校验：Luhn 通过 且 首位符合卡组织 BIN 规则。"""
    return bool(value) and value[0] in _BANKCARD_LEAD and valid_luhn(value)


def valid_ipv4(value: str) -> bool:
    parts = value.split(".")
    if len(parts) != 4:
        return False
    return all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)


def _rule(
    name: str,
    entity: EntityType,
    pattern: str,
    weight: float,
    confidence: float,
    group: int = 1,
    validate: Callable[[str], bool] | None = None,
) -> Rule:
    return Rule(
        name=name,
        type=entity,
        pattern=re.compile(pattern),
        weight=weight,
        confidence=confidence,
        group=group,
        validate=validate,
    )


# 值分隔符：遇到空白、引号、连接符与中英文标点即视为敏感值结束
_VALUE = r"[^\s\"'&,，。；;、]{6,}"

RULES: tuple[Rule, ...] = (
    # ---- 证件与账号类：一票高危 ----
    _rule("idcard18", EntityType.ID_CARD,
          r"(?<![0-9])([1-9][0-9]{5}(?:19|20)[0-9]{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12][0-9]|3[01])[0-9]{3}[0-9Xx])(?![0-9])",
          0.95, 0.98, validate=valid_idcard18),
    # 15 位老身份证：6 位地区 + 6 位出生日期(YYMMDD) + 3 位顺序码；校验位是 18 位才有的，这里靠形态+日期合法性
    _rule("idcard15", EntityType.ID_CARD,
          r"(?<![0-9])([1-9][0-9]{5}[0-9]{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12][0-9]|3[01])[0-9]{3})(?![0-9])",
          0.80, 0.92),
    _rule("bankcard", EntityType.BANK_CARD,
          r"(?<![0-9])([0-9]{16,19})(?![0-9])",
          0.90, 0.92, validate=valid_bankcard),
    # ---- 联系方式类 ----
    _rule("mobile", EntityType.PHONE,
          r"(?<![0-9])(1[3-9][0-9]{9})(?![0-9])",
          0.65, 0.95),
    _rule("email", EntityType.EMAIL,
          r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})",
          0.45, 0.90),
    _rule("ipv4", EntityType.IP,
          r"(?<![0-9])((?:[0-9]{1,3}\.){3}[0-9]{1,3})(?![0-9])",
          0.15, 0.80, validate=valid_ipv4),
    # ---- 凭据类：命中即高危 ----
    _rule("secret_kv", EntityType.SECRET,
          r"(?i)(?:api[_-]?key|access[_-]?key|secret|token|passw(?:or)?d|pwd|密钥|私钥|口令|密码)"
          r"[\"']?\s*[:=：]\s*[\"']?(" + _VALUE + r")",
          1.00, 0.95),
    _rule("jwt", EntityType.SECRET,
          r"(eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})",
          1.00, 0.97),
    _rule("pem_private_key", EntityType.SECRET,
          r"(-----BEGIN [A-Z ]*PRIVATE KEY-----)",
          1.00, 0.99),
    # ---- 非结构化字段类（键值对形态）----
    _rule("name_kv", EntityType.NAME,
          r"(?:姓名|名字|客户名|(?<![A-Za-z])(?:name|full_name)(?![A-Za-z]))"
          r"\s*[\"']?\s*[:=：]\s*[\"']?([\u4e00-\u9fa5]{2,4}|[A-Za-z]{2,20})",
          0.40, 0.75),
    _rule("address_kv", EntityType.ADDRESS,
          r"(?:地址|住址|(?<![A-Za-z])address(?![A-Za-z]))"
          r"\s*[\"']?\s*[:=：]\s*[\"']?([\u4e00-\u9fa5]{4,40})",
          0.50, 0.75),
    _rule("medical_kv", EntityType.MEDICAL,
          r"(?:诊断|病史|病情|症状|住院|手术|用药)\s*[:=：]?\s*([\u4e00-\u9fa5A-Za-z0-9]{2,20})",
          0.75, 0.80),
)


def detect(text: str, rules: tuple[Rule, ...] | None = None) -> list[Hit]:
    """跑规则，返回按出现位置排序、互不重叠的命中列表。

    ``rules`` 为空时用当前生效规则集（默认为内置 ``RULES``，
    自定义规则启用后由 ``set_active_rules`` 覆盖）。
    """
    if not text:
        return []
    hits: list[Hit] = []
    for rule in rules if rules is not None else active_rules():
        for match in rule.pattern.finditer(text):
            value = match.group(rule.group)
            if not value:
                continue
            if rule.validate is not None and not rule.validate(value):
                continue
            start, end = match.span(rule.group)
            hits.append(
                Hit(
                    type=rule.type,
                    start=start,
                    end=end,
                    value=value,
                    weight=rule.weight,
                    confidence=rule.confidence,
                    rule=rule.name,
                )
            )
    return dedupe_overlaps(hits)


_active_rules: tuple[Rule, ...] | None = None


def active_rules() -> tuple[Rule, ...]:
    """当前生效的规则集（内置 + 数据库里启用的自定义规则）。"""
    return _active_rules if _active_rules is not None else RULES


def set_active_rules(rules: tuple[Rule, ...] | None) -> None:
    """替换生效规则集；传 ``None`` 恢复为内置规则（配置变更/测试用）。"""
    global _active_rules
    _active_rules = None if rules is None else tuple(rules)



def dedupe_overlaps(hits: list[Hit]) -> list[Hit]:
    """重叠命中只保留证据最强的一条。

    例：18 位身份证号也可能被银行卡规则匹配，但身份证规则权重更高，
    保留身份证、丢弃银行卡，避免同一段文字被计成两个类别。
    """
    ordered = sorted(hits, key=lambda hit: (hit.start, -(hit.weight * hit.confidence)))
    kept: list[Hit] = []
    for hit in ordered:
        if kept and hit.start < kept[-1].end:
            continue
        kept.append(hit)
    return kept
