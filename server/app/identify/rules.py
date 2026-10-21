"""L1：规则快筛。

与 Go 版 ``internal/dlp/rules.go`` 一一对应，共 17 条规则（扩样时补了裸 sk- 密钥）。设计取舍：

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


# 常见姓氏（《百家姓》高频部分）：自由文本里没有字段名可依，
# 只能靠「姓氏 + 上下文」把「张三反馈…」这类裸人名捞出来，同时压住误报。
_SURNAMES = frozenset(
    "王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖萧田董袁潘于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤易柯俞柏虞邝臧耿米"
)

# 人名里不该出现的字：出现就说明规则抓到了普通词（的/了/是/市/路…）
_NAME_STOP = frozenset(
    "的了是在和与及或也都就还又很更最不没无有这那哪什么怎为以于对其之"
    "公室司局处科员工厂店院校区县市省街路号楼层栋单元室"
)


def valid_free_name(value: str) -> bool:
    """裸人名校验：2~4 字、首字是常见姓氏、不含停用字。"""
    if not 2 <= len(value) <= 4:
        return False
    if value[0] not in _SURNAMES:
        return False
    return not any(ch in _NAME_STOP for ch in value)


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
    # 裸露在 HTTP 头/URL/日志里的 API Key（Stripe 风格 sk-xxx，OpenAI 风格 sk-proj-xxx 也覆盖）：
    # 没有 key=value 上下文，secret_kv 抓不到，评测集扩样后才暴露的盲区。
    _rule("secret_sk", EntityType.SECRET,
          r"(?<![A-Za-z0-9])(sk-[A-Za-z0-9][A-Za-z0-9_-]{15,})",
          1.00, 0.92),
    # ---- 非结构化字段类（键值对形态）----
    _rule("name_kv", EntityType.NAME,
          r"(?:姓名|名字|客户名|(?<![A-Za-z])(?:name|full_name)(?![A-Za-z]))"
          r"\s*[\"']?\s*[:=：]\s*[\"']?([\u4e00-\u9fa5]{2,4}|[A-Za-z]{2,20})",
          0.40, 0.75),
    _rule("address_kv", EntityType.ADDRESS,
          r"(?:地址|住址|(?<![A-Za-z])address(?![A-Za-z]))"
          r"\s*[\"']?\s*[:=：]\s*[\"']?([\u4e00-\u9fa5]{4,40})",
          0.50, 0.75),
    # ---- 自由文本类（没有「姓名：」「地址：」这种字段名）----
    # 叙述型文本里人名常以「同事张珂铭…」「客户张三…」出现，靠角色词 + 姓氏降误报。
    _rule("name_free_role", EntityType.NAME,
          r"(?:同事|客户|用户|患者|病人|联系人|收件人|申请人|投诉人|负责人|经办人|就诊人|参保人|房东|租客|车主|驾驶员)"
          r"\s*[：: ]?\s*([\u4e00-\u9fa5]{2,3})"
          r"(?=[\s，。；、！？：]|反馈|表示|称|说|来电|联系|反映|投诉|申请|签字|确认|昨天|今天|上午|下午|上周|本周|已经|"
          r"办理|预约|预订|购买|咨询|到访|来访|入职|离职|签约|下单|报修)",
          0.45, 0.80, validate=valid_free_name),
    # 句首人名：「萧亚轩反馈仓库这周需要补货…」——首字姓氏 + 后面紧跟动作/时间词。
    _rule("name_free_sentence", EntityType.NAME,
          r"(?:^|[。；;！!？?\n])\s*([\u4e00-\u9fa5]{2,3})"
          r"(?=(?:反馈|表示|称|说|来电|联系|反映|投诉|申请|签字|确认|昨天|今天|上午|下午|上周|本周|"
          r"已经|已|刚|要|将|正在|在|把|向|从|对|提交|离���|请假))",
          0.40, 0.70, validate=valid_free_name),
    # 动作句式：「由魏民整理」「请金秀贤补充」——中文叙述里点名前常用「由/请/向/给」。
    _rule("name_free_action", EntityType.NAME,
          r"(?:由|请|向|给)([\u4e00-\u9fa5]{2,3})"
          r"(?=(?:整理|提交|负责|处理|补充|确认|签字|审核|审批|汇报|反馈|对接|跟进|执行|起草))",
          0.40, 0.72, validate=valid_free_name),
    # 自由文本地址：要有「省/市 + 区/县 + 路/街」的行政层级组合才认，
    # 只有「会议室 3 楼」这种不算，避免把普通地点描述误报成住址。
    _rule("address_free", EntityType.ADDRESS,
          # 句中的「住在/位于/地址：」不是地址的一部分，用非捕获前缀吃掉，捕获组只留行政区组合；
          # 末尾允许「中关村大街 1 号」这种带空格的门牌号写法。
          r"(?:^|(?<=[\s，。；：、！？\"'（(])|(?<=住在)|(?<=位于)|(?<=地址：)|(?<=住址：)|(?<=在))"
          r"(?!(?:他|她|我|你|您|们|住|从|到|往|去|在|于|和|与|地址|住址|位于))"
          r"((?:[\u4e00-\u9fa5]{2,6}(?:省|自治区))?[\u4e00-\u9fa5]{2,6}市"
          r"[\u4e00-\u9fa5]{0,8}(?:区|县)[\u4e00-\u9fa5]{0,10}(?:路|街|大道|巷|胡同)"
          r"[\u4e00-\u9fa5A-Za-z0-9]{0,10}(?:\s*[0-9]{1,5}\s*号)?)",
          0.50, 0.80),
    _rule("medical_kv", EntityType.MEDICAL,
          # 允许值中间带空格：「2 型糖尿病」「2 型糖尿病伴肾病」这类写法在病历里很常见；
          # 英文键 diagnosis / icd-10 在导出的病历 JSON、SQL 里很常见，一并收进来。
          r"(?:诊断|病史|病情|症状|住院|手术|用药|病历(?:记录)?|(?<![A-Za-z])(?:diagnosis|icd-?10)(?![A-Za-z]))"
          r"\s*[\"']?\s*[:=：]\s*[\"']?"
          r"([\u4e00-\u9fa5A-Za-z0-9](?:[\u4e00-\u9fa5A-Za-z0-9 ]{0,18}[\u4e00-\u9fa5A-Za-z0-9])?)",
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
