"""可复现地构建评测集：75 条人工策展样本 + 程序生成样本（补齐到 300 条、稀有类别再补到每类 ≥30）+ 公开语料压力集。

为什么要有这个脚本：
- 只靠自建样本时，"98.7%"容易被问"是不是自己出题自己考"；把每一行的
  生成规则、标签来源写进脚本，评委可以抽任意一行复算。
- 生成样本刻意与训练集（`l2_training` 的模板）用**不同的句式、键名和取值**，
  并在 `--check-overlap` 里报出与训练集的重复率，避免训练/评测泄漏。
- `--stats` 输出级别分布与**类别支持数**，避免"某类只有 3 条"却宣称该类效果好。

用法（在 `server/` 下）::

    python eval/build_dataset.py                    # 重建 eval/dataset.jsonl（300 条 + 稀有类别补样，幂等）
    python eval/build_dataset.py --stats            # 只看现有数据集画像（级别/类别支持数/格式）
    python eval/build_dataset.py --check-overlap    # 只报告与训练集的重复情况
    python eval/build_dataset.py --stress            # 额外拉 weibo NER 构造压力集（200 条）
    python eval/build_dataset.py --stress-only

产物：
- `eval/dataset.jsonl`：主评测集（策展 75 + 生成 225 = 300 条，再按 `RARE_TYPE_TARGETS` 补稀有类别）
- `eval/ner_stress.jsonl`：自由文本压力集（公开语料 weibo_ner 抽样，200 条）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from app.identify.scoring import score  # noqa: E402
from app.identify.types import EntityType, Hit  # noqa: E402

EVAL_DIR = SERVER_DIR / "eval"
DATASET = EVAL_DIR / "dataset.jsonl"
STRESS = EVAL_DIR / "ner_stress.jsonl"

# 生成样本的 ID 前缀，用于幂等重建（保留人工策展行不动）
GEN_PREFIX = "GEN-"
STRESS_PREFIX = "STRESS-"
CURATED_PREFIXES = ("L3-", "L2-", "L1-", "SAFE-", "BENIGN-")

# 与 app.identify.rules 的权重/置信度保持一致（用来算生成样本的金标级别）
WEIGHTS: dict[EntityType, tuple[float, float]] = {
    EntityType.ID_CARD: (0.95, 0.98),
    EntityType.BANK_CARD: (0.90, 0.92),
    EntityType.PHONE: (0.65, 0.95),
    EntityType.EMAIL: (0.45, 0.90),
    EntityType.IP: (0.15, 0.80),
    EntityType.SECRET: (1.00, 0.95),
    EntityType.NAME: (0.40, 0.75),
    EntityType.ADDRESS: (0.50, 0.75),
    EntityType.MEDICAL: (0.75, 0.80),
}

ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
ID_CHECK = "10X98765432"


def make_id_card(rng: random.Random, *, born: str | None = None) -> str:
    """生成校验位合法的 18 位身份证号。"""
    prefix = born or f"{rng.choice(['110101', '320102', '440304', '350102', '510107'])}{rng.randint(1960, 2005)}{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}"
    body = prefix + f"{rng.randint(0, 999):03d}"
    total = sum(int(body[i]) * ID_WEIGHTS[i] for i in range(17))
    return body + ID_CHECK[total % 11]


def _luhn_digit(body: str) -> str:
    total = 0
    for index, char in enumerate(reversed(body)):
        digit = int(char)
        if index % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return str((10 - total % 10) % 10)


def make_bank_card(rng: random.Random) -> str:
    body = rng.choice(["622202", "621226", "622848", "621700"]) + "".join(
        str(rng.randint(0, 9)) for _ in range(9)
    )
    return body + _luhn_digit(body)


def make_phone(rng: random.Random) -> str:
    return rng.choice(["138", "139", "137", "150", "186", "157"]) + "".join(
        str(rng.randint(0, 9)) for _ in range(8)
    )


NAMES = ("陈静", "王磊", "赵敏", "孙浩", "周婷", "吴迪", "郑爽", "冯宇", "何静", "许杰", "邓超", "曹颖")
CITIES = ("杭州市西湖区文三路 88 号", "成都市武侯区人民南路 3 段 18 号", "南京市鼓楼区中山北路 200 号",
          "武汉市洪山区珞喻路 129 号", "西安市雁塔区科技路 6 号")
MEDICAL = ("2 型糖尿病", "高血压三级", "慢性肾功能不全", "甲状腺结节", "抑郁症复诊")
EMAIL_DOMAINS = ("example.com", "corp.example.cn", "mail.example.org")
HOSPITALS = ("市第一人民医院", "省立医院", "区中医院")


def make_email(rng: random.Random) -> str:
    user = rng.choice(["chen.jing", "wang.lei", "zhao.min", "sun.hao", "zhou.ting"]) + str(rng.randint(1, 99))
    return f"{user}@{rng.choice(EMAIL_DOMAINS)}"


def _hit(entity: EntityType, value: str) -> Hit:
    weight, confidence = WEIGHTS[entity]
    return Hit(type=entity, start=0, end=len(value), value=value, weight=weight, confidence=confidence, rule="gold")


def gold_level(entities: list[EntityType]) -> tuple[str, list[str]]:
    """按评分模型把“构造时注入的实体”折算成四级与类型列表。"""
    result = score([_hit(entity, "x") for entity in entities])
    return result.level.value if hasattr(result.level, "value") else str(result.level), [e.value for e in entities]


# ---------------------------------------------------------------- 生成样本模板

def _record(idx: int, content: str, entities: list[EntityType], fmt: str, note: str) -> dict:
    level, types = gold_level(entities)
    return {
        "id": f"{GEN_PREFIX}{idx:03d}",
        "format": fmt,
        "content": content,
        "level": level,
        "types": types,
        "note": note,
        "source": "generated",
    }


def _lv(entities: list[EntityType]) -> str:
    return gold_level(entities)[0]


def generate_rows(seed: int = 2026) -> list[dict]:
    """生成 90 条样本：L3/L2/L1/SAFE 各占约 25/28/22/15。"""
    rng = random.Random(seed)
    rows: list[dict] = []
    idx = 0

    def name(rng_: random.Random) -> str:
        return rng_.choice(NAMES)

    # ---- L3：证件 / 银行卡 / 密钥（直达高危）
    for _ in range(9):
        idx += 1
        content = f"报销单：{name(rng)}，身份证 {make_id_card(rng)}，联系电话 {make_phone(rng)}，请尽快处理。"
        rows.append(_record(idx, content, [EntityType.ID_CARD, EntityType.PHONE, EntityType.NAME], "text", "身份证直达 L3"))
    for _ in range(7):
        idx += 1
        content = json.dumps({"holder": name(rng), "card_no": make_bank_card(rng), "phone": make_phone(rng)}, ensure_ascii=False)
        rows.append(_record(idx, content, [EntityType.BANK_CARD, EntityType.PHONE, EntityType.NAME], "json", "JSON 银行卡 Luhn 有效"))
    for _ in range(6):
        idx += 1
        key = "sk-" + "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(24))
        content = f"POST /v2/export HTTP/1.1\r\nHost: api.example.net\r\nAuthorization: Bearer {key}\r\n\r\n{{\"since\":\"2026-09\"}}"
        rows.append(_record(idx, content, [EntityType.SECRET], "text", "HTTP 头里的 API Key"))
    for _ in range(4):
        idx += 1
        content = f"update users set password='{rng.choice(['P@ssw0rd', 'Abc123456', 'Qwer!2345'])}', api_key='{make_id_card(rng)[:16]}' where uid={rng.randint(1000, 9999)};"
        rows.append(_record(idx, content, [EntityType.SECRET], "sql", "SQL 里的密钥字面量"))
    for _ in range(4):
        idx += 1
        content = f"姓名：{name(rng)}\n证件类型：居民身份证\n证件号码：{make_id_card(rng)}\n开户行：示例银行"
        rows.append(_record(idx, content, [EntityType.ID_CARD, EntityType.NAME], "text", "多行表单里的身份证"))

    # ---- L2：中危（手机/邮箱+地址/医疗）
    for _ in range(8):
        idx += 1
        content = f"订单联系人电话：{make_phone(rng)}，配送地址 {rng.choice(CITIES)}，请上午送达。"
        rows.append(_record(idx, content, [EntityType.PHONE, EntityType.ADDRESS], "text", "手机+地址共现 L2"))
    for _ in range(6):
        idx += 1
        content = f"email={make_email(rng)}&address={rng.choice(CITIES)}&user_id={rng.randint(10000, 99999)}"
        rows.append(_record(idx, content, [EntityType.EMAIL, EntityType.ADDRESS], "text", "表单邮箱+地址（format 只允许 text/json/csv/sql）"))
    for _ in range(6):
        idx += 1
        content = f"患者 {name(rng)}，诊断：{rng.choice(MEDICAL)}，复诊医院：{rng.choice(HOSPITALS)}。"
        rows.append(_record(idx, content, [EntityType.MEDICAL, EntityType.NAME], "text", "医疗字段 + 姓名"))
    for _ in range(6):
        idx += 1
        content = f"紧急联系人：{make_phone(rng)}（工位 {rng.randint(100, 999)}）"
        rows.append(_record(idx, content, [EntityType.PHONE], "text", "只有手机号（0.62 → L2）"))

    # ---- L1：低危（仅邮箱/姓名/地址/内网 IP）
    for _ in range(8):
        idx += 1
        content = f"抄送邮箱：{make_email(rng)}"
        rows.append(_record(idx, content, [EntityType.EMAIL], "text", "仅邮箱（0.405 → L1）"))
    for _ in range(6):
        idx += 1
        content = f"客户姓名：{name(rng)}，请客服核实后回访。"
        rows.append(_record(idx, content, [EntityType.NAME], "text", "仅姓名字段（0.3 → L1）"))
    for _ in range(5):
        idx += 1
        content = f"收货地址：{rng.choice(CITIES)}；备注：货到付款。"
        rows.append(_record(idx, content, [EntityType.ADDRESS], "text", "仅地址字段（0.375 → L1）"))
    for _ in range(3):
        idx += 1
        content = f"内网主机 10.{rng.randint(0, 30)}.{rng.randint(0, 255)}.{rng.randint(1, 254)} 正在同步日志。"
        rows.append(_record(idx, content, [EntityType.IP], "text", "内网 IP（0.12 → SAFE/L1 边界）"))

    # ---- SAFE：困难负例（示例/测试上下文 + 类敏感形态但校验不过）
    safe_texts = [
        "接口文档示例：手机号 13800000000、身份证 110101199001011234 均为占位数据，请勿用于生产。",
        "单元测试断言：assert phone == '13900000000'  # 测试数据，非真实用户",
        "压测脚本使用假号码 13012345678 与虚构地址，仅用于联调。",
        "日志：GET /api/v1/health HTTP/1.1 200 12ms agent=probe",
        "缓存键 user:profile:123456 命中，TTL 300s",
        "订单号 2026102000000123 已创建，支付金额 199.00 元。",
        "示例 JSON：{\"phone\": \"<PHONE>\", \"id_card\": \"<ID_CARD>\"} 为模板占位符。",
        "版本号 3.12.10，构建时间 2026-10-20T13:00:00Z，commit abcdef1。",
        "监控指标 pps=1234 bps=5678900 conns=88 drop_rate=0.0001",
        "随机串 x7Kp2Lm9Qa 与 UUID 550e8400-e29b-41d4-a716-446655440000 都不是密钥。",
        "示例银行卡 4111 1111 1111 1111 来自公开测试卡号，Luhn 校验通过但不可用。",
        "身份证格式说明：前 6 位是地址码，中间 8 位是出生日期，最后 1 位是校验位。",
        "样例数据表：姓名=张三（虚构），手机=13800138000（已脱敏练习用）。",
        "测试用例期望 L3，输入为 \"客户张三，身份证 \" + MOCK_ID_CARD 常量。",
        "演示环境单号 DEMO-2026-0001，客服电话 400-000-0000（官方热线）。",
    ]
    for text in safe_texts:
        idx += 1
        rows.append({"id": f"{GEN_PREFIX}{idx:03d}", "format": "text", "content": text, "level": "SAFE", "types": [], "note": "困难负例（示例/占位/无关数字）", "source": "generated"})

    return rows


# ---------------------------------------------------------------- 扩充样本（补齐到 300 条）

# 主评测集的目标规模（含 75 条策展行）：L3 90 / L2 80 / L1 60 / SAFE 70 = 300
GEN_TARGETS = {"L3": 90, "L2": 80, "L1": 60, "SAFE": 70}

SAFE_TEXTS = (
    "接口文档示例：手机号 13800000000、身份证 110101199001011234 均为占位数据，请勿用于生产。",
    "单元测试断言：assert phone == '13900000000'  # 测试数据，非真实用户",
    "压测脚本使用假号码 13012345678 与虚构地址，仅用于联调。",
    "日志：GET /api/v1/health HTTP/1.1 200 12ms agent=probe",
    "缓存键 user:profile:123456 命中，TTL 300s",
    "订单号 2026102000000123 已创建，支付金额 199.00 元。",
    '示例 JSON：{"phone": "<PHONE>", "id_card": "<ID_CARD>"} 为模板占位符。',
    "版本号 3.12.10，构建时间 2026-10-20T13:00:00Z，commit abcdef1。",
    "监控指标 pps=1234 bps=5678900 conns=88 drop_rate=0.0001",
    "随机串 x7Kp2Lm9Qa 与 UUID 550e8400-e29b-41d4-a716-446655440000 都不是密钥。",
    "示例银行卡 4111 1111 1111 1111 来自公开测试卡号，Luhn 校验通过但不可用。",
    "身份证格式说明：前 6 位是地址码，中间 8 位是出生日期，最后 1 位是校验位。",
    "样例数据表：姓名=张三（虚构），手机=13800138000（已脱敏练习用）。",
    '测试用例期望 L3，输入为 "客户张三，身份证 " + MOCK_ID_CARD 常量。',
    "演示环境单号 DEMO-2026-0001，客服电话 400-000-0000（官方热线）。",
)
SAFE_SUFFIXES = ("", "（工单 {no}）", "（文档片段 {no}）", "（示例编号 {no}）", "（测试记录 {no}）")


def _l3_family(rng: random.Random, i: int) -> tuple[str, list[EntityType], str, str]:
    """L3 家族：证件 / 银行卡 / 密钥，模板轮换。"""
    family = i % 5
    if family == 0:
        return (
            f"报销单：{rng.choice(NAMES)}，身份证 {make_id_card(rng)}，联系电话 {make_phone(rng)}，请尽快处理。",
            [EntityType.ID_CARD, EntityType.PHONE, EntityType.NAME],
            "text",
            "身份证直达 L3（扩充）",
        )
    if family == 1:
        content = json.dumps(
            {"holder": rng.choice(NAMES), "card_no": make_bank_card(rng), "phone": make_phone(rng)},
            ensure_ascii=False,
        )
        return content, [EntityType.BANK_CARD, EntityType.PHONE, EntityType.NAME], "json", "JSON 银行卡 Luhn 有效（扩充）"
    if family == 2:
        key = "sk-" + "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(24))
        content = f"GET /v2/export HTTP/1.1\r\nHost: api.example.net\r\nAuthorization: Bearer {key}\r\n\r\n"
        return content, [EntityType.SECRET], "text", "HTTP 头里的 API Key（扩充）"
    if family == 3:
        content = (
            f"update users set password='{rng.choice(['P@ssw0rd', 'Abc123456', 'Qwer!2345'])}', "
            f"api_key='{make_id_card(rng)[:16]}' where uid={rng.randint(1000, 9999)};"
        )
        return content, [EntityType.SECRET], "sql", "SQL 里的密钥字面量（扩充）"
    content = f"姓名：{rng.choice(NAMES)}\n证件类型：居民身份证\n证件号码：{make_id_card(rng)}\n开户行：示例银行"
    return content, [EntityType.ID_CARD, EntityType.NAME], "text", "多行表单里的身份证（扩充）"


def _l2_family(rng: random.Random, i: int) -> tuple[str, list[EntityType], str, str]:
    """L2 家族：手机+地址 / 邮箱+地址 / 医疗+姓名 / 仅手机。"""
    family = i % 4
    if family == 0:
        content = f"订单联系人电话：{make_phone(rng)}，配送地址 {rng.choice(CITIES)}，请上午送达。"
        return content, [EntityType.PHONE, EntityType.ADDRESS], "text", "手机+地址共现 L2（扩充）"
    if family == 1:
        content = f"email={make_email(rng)}&address={rng.choice(CITIES)}&user_id={rng.randint(10000, 99999)}"
        return content, [EntityType.EMAIL, EntityType.ADDRESS], "text", "表单邮箱+地址（扩充）"
    if family == 2:
        content = f"患者 {rng.choice(NAMES)}，诊断：{rng.choice(MEDICAL)}，复诊医院：{rng.choice(HOSPITALS)}。"
        return content, [EntityType.MEDICAL, EntityType.NAME], "text", "医疗字段 + 姓名（扩充）"
    content = f"紧急联系人：{make_phone(rng)}（工位 {rng.randint(100, 999)}）"
    return content, [EntityType.PHONE], "text", "只有手机号（扩充）"


def _l1_family(rng: random.Random, i: int) -> tuple[str, list[EntityType], str, str]:
    """L1 家族：仅邮箱 / 仅姓名 / 仅地址 / 工单处理人。"""
    family = i % 4
    if family == 0:
        return f"抄送邮箱：{make_email(rng)}", [EntityType.EMAIL], "text", "仅邮箱（扩充）"
    if family == 1:
        return f"客户姓名：{rng.choice(NAMES)}，请客服核实后回访。", [EntityType.NAME], "text", "仅姓名字段（扩充）"
    if family == 2:
        return f"收货地址：{rng.choice(CITIES)}；备注：货到付款。", [EntityType.ADDRESS], "text", "仅地址字段（扩充）"
    # 姓名必须带规则认得的键（姓名/名字/客户名/name）；裸叙述里的人名交给 NER，L1 本来抓不到
    return f"联系人姓名：{rng.choice(NAMES)}，请客服核实后回访。", [EntityType.NAME], "text", "联系人姓名字段（扩充）"


def _safe_family(rng: random.Random, i: int) -> tuple[str, list[EntityType], str, str]:
    """SAFE 家族：示例/占位/日志等困难负例，用编号后缀保证内容唯一。"""
    base = SAFE_TEXTS[i % len(SAFE_TEXTS)]
    suffix = SAFE_SUFFIXES[(i // len(SAFE_TEXTS)) % len(SAFE_SUFFIXES)]
    text = base + (suffix.format(no=rng.randint(100, 999)) if suffix else f"（测试记录 {rng.randint(100, 999)}）")
    return text, [], "text", "困难负例（示例/占位/无关数字，扩充）"


EXTRA_FAMILIES = {"L3": _l3_family, "L2": _l2_family, "L1": _l1_family, "SAFE": _safe_family}

# ---------------------------------------------------------------- 稀有类别补样
# 级别够 60 条不代表"每个类别都够测"：bank_card / medical / ip 原先只有 18/17/4 条，
# 单类别指标没有统计意义，这里按类别支持数下限再补一批（补完总量会略超 300）。
RARE_TYPE_TARGETS = {"bank_card": 30, "medical": 30, "ip": 30}


def _rare_family(rng: random.Random, kind: str, i: int) -> tuple[str, list[EntityType], str, str]:
    """为一个稀有类别造样本：每种 3 个模板，覆盖 text / json / csv / sql 格式。

    模板里带上 `i`（补样序号）作为记录号，保证同模板多行内容不重复。
    """
    template = i % 3
    if kind == "bank_card":
        card = make_bank_card(rng)
        holder = rng.choice(NAMES)
        if template == 0:
            return f"收款账户：{card}，开户人：{holder}，请核对后打款。", [EntityType.BANK_CARD, EntityType.NAME], "text", "稀有类别补样：银行卡（文本）"
        if template == 1:
            content = json.dumps({"account_no": card, "holder": holder, "bank": "示例银行"}, ensure_ascii=False)
            return content, [EntityType.BANK_CARD, EntityType.NAME], "json", "稀有类别补样：银行卡（JSON）"
        content = f"account_no,holder,bank\n{card},{holder},示例银行"
        return content, [EntityType.BANK_CARD, EntityType.NAME], "csv", "稀有类别补样：银行卡（CSV）"
    if kind == "medical":
        patient = rng.choice(NAMES)
        diagnosis = rng.choice(MEDICAL)
        hospital = rng.choice(HOSPITALS)
        if template == 0:
            return f"门诊记录（编号 {i:04d}）：诊断：{diagnosis}，建议两周后复查。", [EntityType.MEDICAL], "text", "稀有类别补样：诊断字段（文本）"
        if template == 1:
            content = json.dumps({"patient": patient, "diagnosis": diagnosis, "hospital": hospital, "record_no": i}, ensure_ascii=False)
            return content, [EntityType.MEDICAL, EntityType.NAME], "json", "稀有类别补样：病历 JSON"
        content = f"update records set diagnosis='{diagnosis}' where patient='{patient}' and record_no={i};"
        return content, [EntityType.MEDICAL, EntityType.NAME], "sql", "稀有类别补样：处方 SQL"
    # ip：单独出现时权重只有 0.12（SAFE），因此这些样本是"有类别的安全样本"
    octets = f"10.{rng.randint(0, 30)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"
    if template == 0:
        return f"内网主机 {octets} 正在同步日志（记录 {i:04d}），出口带宽正常。", [EntityType.IP], "text", "稀有类别补样：内网 IP（文本，SAFE）"
    if template == 1:
        content = json.dumps({"client_ip": octets, "action": "login", "result": "ok", "record_no": i}, ensure_ascii=False)
        return content, [EntityType.IP], "json", "稀有类别补样：客户端 IP（JSON，SAFE）"
    content = f"select * from audit where ip = '{octets}' and action = 'login' and record_no = {i};"
    return content, [EntityType.IP], "sql", "稀有类别补样：审计 IP（SQL，SAFE）"


def _type_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for typ in row.get("types") or []:
            counts[typ] = counts.get(typ, 0) + 1
    return counts


def generate_rare_rows(rows: list[dict], *, seed: int = 2026, start_idx: int = 0) -> list[dict]:
    """按 RARE_TYPE_TARGETS 补稀有类别样本；每次补当前缺口最大的那一类，直到全部达标。"""
    rng = random.Random(seed + 7)
    out: list[dict] = []
    attempt = 0
    while attempt < 400:
        counts = _type_counts(rows + out)
        deficit = {
            typ: target - counts.get(typ, 0)
            for typ, target in RARE_TYPE_TARGETS.items()
            if counts.get(typ, 0) < target
        }
        if not deficit:
            break
        kind = max(deficit, key=lambda key: deficit[key])
        content, entities, fmt, note = _rare_family(rng, kind, attempt)
        attempt += 1
        gold, types = gold_level(entities)
        if kind not in types:  # 模板与规则口径不一致时跳过，避免"标了却没检测到"
            continue
        if content in {row["content"] for row in out}:
            continue
        out.append(
            {
                "id": f"{GEN_PREFIX}{start_idx + len(out) + 1:03d}",
                "format": fmt,
                "content": content,
                "level": gold,
                "types": types,
                "note": note,
                "source": "generated",
            }
        )
    counts = _type_counts(rows + out)
    missing = {
        typ: target - counts.get(typ, 0)
        for typ, target in RARE_TYPE_TARGETS.items()
        if counts.get(typ, 0) < target
    }
    if missing:
        raise SystemExit(f"稀有类别补样不足：{missing}，请检查 RARE_TYPE_TARGETS 与规则口径")
    if len({_digest(row["content"]) for row in out}) != len(out):
        raise SystemExit("稀有类别补样出现内容重复，请检查模板里的随机源")
    return out



def generate_extra_rows(deficit: dict[str, int], *, seed: int = 2026, start_idx: int = 0) -> list[dict]:
    """按级别缺口生成样本；模板折算出的级别与目标级别不一致时跳过，避免金标漂移。"""
    rng = random.Random(seed)
    rows: list[dict] = []
    for level, want in deficit.items():
        if want <= 0:
            continue
        family = EXTRA_FAMILIES[level]
        made = 0
        attempt = 0
        while made < want and attempt < want * 30:
            content, entities, fmt, note = family(rng, attempt)
            attempt += 1
            gold, types = gold_level(entities)
            if gold != level:
                continue
            rows.append(
                {
                    "id": f"{GEN_PREFIX}{start_idx + len(rows) + 1:03d}",
                    "format": fmt,
                    "content": content,
                    "level": gold,
                    "types": types,
                    "note": note,
                    "source": "generated",
                }
            )
            made += 1
        if made < want:
            raise SystemExit(f"生成 {level} 样本不足：{made}/{want}，请检查 EXTRA_FAMILIES 模板")
    return rows


def dataset_stats(rows: list[dict]) -> dict:
    """数据集画像：级别分布、类别支持数、格式分布、唯一性、来源。"""
    levels = ("L3", "L2", "L1", "SAFE")
    by_level = {level: 0 for level in levels}
    by_type: dict[str, int] = {}
    by_format: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for row in rows:
        by_level[row["level"]] = by_level.get(row["level"], 0) + 1
        for typ in row["types"]:
            by_type[typ] = by_type.get(typ, 0) + 1
        by_format[row.get("format", "text")] = by_format.get(row.get("format", "text"), 0) + 1
        source = str(row.get("source") or "curated")
        by_source[source] = by_source.get(source, 0) + 1
    return {
        "total": len(rows),
        "by_level": by_level,
        "by_type": dict(sorted(by_type.items(), key=lambda item: -item[1])),
        "by_format": dict(sorted(by_format.items(), key=lambda item: -item[1])),
        "by_source": dict(sorted(by_source.items(), key=lambda item: -item[1])),
        "unique_ids": len({row["id"] for row in rows}),
        "unique_contents": len({_digest(row["content"]) for row in rows}),
        "rare_targets": dict(RARE_TYPE_TARGETS),
    }


def render_stats(stats: dict) -> str:
    """把画像渲染成 markdown 表，便于贴进文档与提交说明。"""
    lines = [
        "| 维度 | 分布 |",
        "| --- | --- |",
        f"| 总量 | {stats['total']} 条（唯一 id {stats['unique_ids']} / 唯一内容 {stats['unique_contents']}） |",
        f"| 级别 | {' / '.join(f'{k} {v}' for k, v in stats['by_level'].items())} |",
        f"| 格式 | {' / '.join(f'{k} {v}' for k, v in stats['by_format'].items())} |",
        f"| 来源 | {' / '.join(f'{k} {v}' for k, v in stats['by_source'].items())} |",
        f"| 类别支持数 | {' / '.join(f'{k} {v}' for k, v in stats['by_type'].items())} |",
        f"| 稀有类别下限 | {' / '.join(f'{k} ≥{v}' for k, v in stats.get('rare_targets', {}).items())} |",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- 压力集（公开语料）

GENERIC_NAMES = frozenset((
    "老百姓", "妹子", "网友", "大家", "记者", "医生", "同学", "朋友", "孩子", "老人", "男子", "女子",
    "工作人员", "负责人", "客服", "保安", "司机", "老师", "学生", "老板", "经理", "工人", "警察", "护士",
    "妈妈", "爸爸", "老公", "老婆", "姐姐", "哥哥", "弟弟", "妹妹", "爷爷", "奶奶", "外公", "外婆",
    "父母", "亲人", "家人", "亲戚", "家属", "同事", "领导", "病人", "患者", "顾客", "客户", "乘客",
    "群众", "村民", "当事人", "宝宝", "孩子", "儿童", "青年", "男人", "女人", "人民",
    "球星", "明星", "歌手", "演员", "作家", "导演", "主持人", "官员", "专家", "教授", "工程师",
    "律师", "球员", "运动员", "志愿者", "选手", "主播", "网红", "博主", "作者", "编者", "发言人",
    "高手", "童鞋", "富二代", "文艺女", "屌丝", "土豪", "学生党", "上班族", "大妈", "大叔", "汉子",
    "姑娘", "小伙", "妹子", "哥哥", "姐姐", "小弟", "大哥", "大师", "达人", "编者",
))
_COMMON_SURNAMES = frozenset("赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章苏潘葛范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐费廉岑薛雷贺倪汤殷罗毕郝安常傅卞齐康伍余元顾孟平黄萧尹姚邵汪祁毛狄米贝臧成戴宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄江童颜郭梅盛林钟徐邱骆高夏蔡田樊胡凌霍虞万柯管卢莫房裘解应宗丁宣邓郁单杭洪包诸左石崔吉龚程邢裴陆荣翁荀羊惠甄曲封储靳段富巫乌焦巴牧山谷车侯全班秋仲伊宫宁仇甘厉祖武符刘景詹龙叶幸司韶黎薄印宿白怀蒲邰从鄂索咸赖卓蔺屠蒙池乔胥闻莘党翟谭贡劳姬申扶冉宰郦雍桑桂濮牛寿通边燕冀浦尚农温庄晏柴瞿阎慕连茹艾鱼容向古易慎戈廖庾居衡步都耿满弘匡国文寇广禄阙东欧沃利蔚越隆师巩聂晁敖冷辛简饶曾沙鞠丰巢关相查荆红游权盖桓")


TEMPLATES_BOTH = (
    "同事{name}昨天从{loc}赶过来，会议上提了下季度的采购预算。",
    "客户{name}反馈：仓库位于{loc}，本周五前需要补货。",
    "{name}在{loc}参加培训，回程车票已改签到周日。",
    "上周{name}去{loc}出差，回来后整理了对接材料。",
    "供应商{name}把样品寄到了{loc}的仓库，请安排验收。",
)
TEMPLATES_NAME = (
    "同事{name}昨天赶过来，会议上提了下季度的采购预算。",
    "这份材料由{name}整理，周五前要发出去。",
    "{name}反馈仓库这周需要补货，回程车票已改签到周日。",
    "{name}刚提交了离职申请，交接清单还在整理。",
    "评审意见里提到要请{name}补充一下预算说明。",
)
TEMPLATES_LOC = (
    "本周巡检覆盖{loc}，未发现异常。",
    "仓库位于{loc}，本周五前需要补货。",
    "下个月的渠道会安排在{loc}，报名截止到月底。",
    "现场照片是在{loc}拍的，已上传到共享盘。",
    "设备清单里有一批在{loc}等待调拨。",
)
STRESS_SUFFIXES = ("（记录 {no}）", "（工单 {no}）", "（附件 {no}）")


def _stress_row(text: str, entities: list[EntityType], *, note: str) -> dict:
    """压力集行：金标只按模板里真正注入的实体折算级别。"""
    level, types = gold_level(entities)
    return {
        "id": f"{STRESS_PREFIX}000",
        "format": "text",
        "content": text,
        "level": level,
        "types": types,
        "note": f"公开语料 weibo_ner/dev 抽样（{note}），L1 规则抓不到、靠 NER",
        "source": "weibo_ner/dev",
    }


def build_stress_rows(*, cache: Path, count: int = 200, seed: int = 2026) -> list[dict]:
    """从 weibo NER dev 集抽人名/地点，包成叙述句，金标为 name/address（L1）。

    取舍：
    - 公开语料只取实体（PER/LOC/GPE），模板与上下文是自己加的，因此级别按本项目评分模型折算；
    - 每句尽量多取几个实体，铺满 200 条（人名+地点 > 仅人名 > 仅地点）；
    - 内容重复时追加「（记录/工单/附件 N）」后缀，保证每条内容唯一；
    - 过滤「老百姓 / 妹子 / 网友」这类语料标注噪声（weibo 把泛称也标了 PER）。
    """
    if not cache.is_file():
        raise SystemExit(
            f"缺少公开语料缓存：{cache}\n"
            "下载命令（dev + train 两片，合并后句子更多）：\n"
            "  curl.exe -s -L -o eval/_weibo_ner_dev.json "
            "https://hf-mirror.com/datasets/Aunderline/weibo_ner/resolve/main/dev.json\n"
            "  curl.exe -s -L -o eval/_weibo_ner_train.json "
            "https://hf-mirror.com/datasets/Aunderline/weibo_ner/resolve/main/train.json"
        )
    # 合并同目录下的其他公开语料分片（_weibo_ner_*.json），扩样到 200 条需要更多句子
    sources = [cache]
    sources.extend(sorted(path for path in cache.parent.glob("_weibo_ner_*.json") if path != cache))
    raw: list[dict] = []
    for source in sources:
        if not source.is_file():
            continue
        try:
            part = json.loads(source.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if isinstance(part, list):
            raw.extend(part)
    rng = random.Random(seed)

    def mentions(sentence: dict, prefixes: tuple[str, ...]) -> list[str]:
        tokens = sentence.get("tokens") or []
        out: list[str] = []
        for item in sentence.get("entities") or []:
            if not str(item.get("type", "")).upper().startswith(prefixes):
                continue
            start, end = item.get("start"), item.get("end")
            if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(tokens):
                continue
            text = "".join(tokens[start:end])
            if text and len(text) <= 12:
                out.append(text)
        return out

    def clean_name(value: str) -> str | None:
        if not 2 <= len(value) <= 3 or value in GENERIC_NAMES:
            return None
        if any(ch in value for ch in "，。！？、,.!?；;：:\"'“”「」（）()"):
            return None
        # 中文人名启发式：首字必须是常见姓氏，过滤 weibo 语料里的泛称/网络用语
        if value[0] not in _COMMON_SURNAMES:
            return None
        # 网络用语常被 weibo 误标成 PER（高手/童鞋/富二代/文艺女…），按结尾字再过一道
        if value[-1] in "们女男党族代鞋手者员家星神饭丝粉团狗猫孩纸":
            return None
        return value

    both: list[dict] = []
    only_name: list[dict] = []
    only_loc: list[dict] = []
    for sentence in raw:
        pers = [name for name in mentions(sentence, ("PER",)) if clean_name(name)]
        locs = [place for place in mentions(sentence, ("LOC", "GPE")) if 2 <= len(place) <= 12]
        for order, name in enumerate(pers[:2]):
            if locs:
                template = TEMPLATES_BOTH[len(both) % len(TEMPLATES_BOTH)]
                text = template.format(name=name, loc=locs[order % len(locs)])
                both.append(
                    _stress_row(text, [EntityType.NAME, EntityType.ADDRESS], note=f"PER={name}, LOC={locs[order % len(locs)]}")
                )
            else:
                text = TEMPLATES_NAME[len(only_name) % len(TEMPLATES_NAME)].format(name=name)
                only_name.append(_stress_row(text, [EntityType.NAME], note=f"PER={name}"))
        if not pers:
            for order, loc in enumerate(locs[:2]):
                text = TEMPLATES_LOC[len(only_loc) % len(TEMPLATES_LOC)].format(loc=loc)
                only_loc.append(_stress_row(text, [EntityType.ADDRESS], note=f"LOC={loc}"))

    candidates = both + only_name + only_loc
    if not candidates:
        raise SystemExit("weibo_ner 里没抽到可用的人名/地点")
    rng.shuffle(candidates)

    rows: list[dict] = []
    seen: set[str] = set()
    for row in candidates:
        if len(rows) >= count:
            break
        text = row["content"]
        attempt = 0
        while text in seen and attempt < 200:
            suffix = STRESS_SUFFIXES[attempt % len(STRESS_SUFFIXES)].format(no=rng.randint(100, 999))
            text = f"{row['content']}{suffix}"
            attempt += 1
        if text in seen:
            continue
        seen.add(text)
        row["content"] = text
        rows.append(row)
    if len(rows) < count:
        raise SystemExit(f"weibo_ner 可用样本不足：{len(rows)}/{count}，请放宽过滤或补充语料")
    for order, row in enumerate(rows, start=1):
        row["id"] = f"{STRESS_PREFIX}{order:03d}"
    return rows


# ---------------------------------------------------------------- 读写与校验

def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n"
    path.write_text(payload, encoding="utf-8")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def count_overlap(rows: list[dict], training_path: Path) -> tuple[int, int]:
    training = load_jsonl(training_path)
    train_hashes = {_digest(row.get("text", "")) for row in training}
    hits = sum(1 for row in rows if _digest(row["content"]) in train_hashes)
    return hits, len(rows)


def _dedupe_ids(rows: list[dict]) -> list[dict]:
    """保证 id 唯一：策展行与生成行可能撞号（如都叫 L2-014），撞号时按前缀顺延编号。"""
    next_no: dict[str, int] = {}
    for row in rows:
        prefix, _, tail = str(row["id"]).partition("-")
        if tail.isdigit():
            next_no[prefix] = max(next_no.get(prefix, 0), int(tail))
    seen: set[str] = set()
    for row in rows:
        ident = str(row["id"])
        if ident in seen:
            prefix = ident.partition("-")[0]
            while True:
                next_no[prefix] = next_no.get(prefix, 0) + 1
                candidate = f"{prefix}-{next_no[prefix]:03d}"
                if candidate not in seen:
                    break
            row["id"] = candidate
            ident = candidate
        seen.add(ident)
    return rows


def build_dataset(*, keep_curated: bool = True, seed: int = 2026) -> tuple[list[dict], dict]:
    """构建主评测集：策展行 + 基础生成 90 条 + 按缺口补齐到 GEN_TARGETS（合计 300 条）。"""
    existing = load_jsonl(DATASET)
    curated = [row for row in existing if str(row.get("id", "")).startswith(CURATED_PREFIXES)]
    if not keep_curated:
        curated = []

    seen: set[str] = set()
    rows: list[dict] = []

    def _add(new_rows: list[dict]) -> int:
        added = 0
        for row in new_rows:
            digest = _digest(row["content"])
            if digest in seen:
                continue
            seen.add(digest)
            rows.append(row)
            added += 1
        return added

    _add(curated)
    _add(generate_rows(seed=seed))

    # 按级别缺口补齐：每轮重新算缺口，最多 8 轮（模板级别与评分口径不符时会跳过）
    for attempt in range(8):
        counts = {level: 0 for level in GEN_TARGETS}
        for row in rows:
            counts[row["level"]] = counts.get(row["level"], 0) + 1
        deficit = {level: target - counts.get(level, 0) for level, target in GEN_TARGETS.items() if target > counts.get(level, 0)}
        if not deficit:
            break
        _add(generate_extra_rows(deficit, seed=seed + attempt, start_idx=len(rows)))
    else:
        counts = {level: 0 for level in GEN_TARGETS}
        for row in rows:
            counts[row["level"]] = counts.get(row["level"], 0) + 1
        missing = {level: target - counts.get(level, 0) for level, target in GEN_TARGETS.items() if target > counts.get(level, 0)}
        if missing:
            raise SystemExit(f"评测集未补齐到目标规模，缺：{missing}")

    # 级别够了以后再补稀有类别：避免"每级 60+ 条但 bank_card 只有 18 条"
    rare = generate_rare_rows(rows, seed=seed, start_idx=len(rows))
    _add(rare)

    rows = _dedupe_ids(rows)
    stats = dataset_stats(rows)
    stats["rare_boost"] = len(rare)
    stats["curated"] = sum(1 for row in rows if str(row.get("id", "")).startswith(CURATED_PREFIXES))
    stats["generated"] = stats["total"] - stats["curated"]
    return rows, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="重建评测集（可复现）")
    parser.add_argument("--check-overlap", action="store_true", help="只报告与 L2 训练集的重复情况")
    parser.add_argument("--stats", action="store_true", help="只打印现有数据集的画像（级别/类别支持数/格式）")
    parser.add_argument("--stress", action="store_true", help="额外构造公开语料压力集")
    parser.add_argument("--stress-only", action="store_true", help="只重建压力集")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--cache", default=str(EVAL_DIR / "_weibo_ner_dev.json"), help="公开语料缓存路径")
    args = parser.parse_args(argv)

    training = SERVER_DIR / "data" / "l2_training.jsonl"

    if args.stats:
        rows = load_jsonl(DATASET)
        if not rows:
            raise SystemExit(f"数据集不存在：{DATASET}")
        print(render_stats(dataset_stats(rows)))
        return 0

    if not args.stress_only:
        rows, stats = build_dataset(seed=args.seed)
        write_jsonl(DATASET, rows)
        print(
            f"[dataset] 写入 {DATASET}：{stats['total']} 条"
            f"（策展 {stats['curated']} + 生成 {stats['generated']}，其中稀有类别补样 {stats.get('rare_boost', 0)} 条）"
        )
        print(render_stats(stats))
        if training.is_file():
            overlap, total = count_overlap(rows, training)
            print(f"[leak] 与训练集({training.name})内容完全相同的行：{overlap}/{total}（{overlap / total:.1%}）")
        else:
            print(f"[leak] 训练集不存在（{training}），跳过重复检查")
        if args.check_overlap:
            return 0

    if args.stress or args.stress_only:
        cache = Path(args.cache)
        rows = build_stress_rows(cache=cache)
        write_jsonl(STRESS, rows)
        print(f"[stress] 写入 {STRESS}：{len(rows)} 条（公开语料 weibo_ner/dev 抽样）")
        print(render_stats(dataset_stats(rows)))
        print("  示例：" + json.dumps(rows[0], ensure_ascii=False))

    print(f"done at {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
