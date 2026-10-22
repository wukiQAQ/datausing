"""L2 训练数据构造：合成自举数据集 + 评测集导入 + 划分/落盘。

真权重训练的第一个瓶颈是标注数据。这里给两条现实路径：

1. :func:`synthetic_examples` —— 模板 + 校验位生成，几百条即可把
   「训练 → 导出 ONNX → 接进 l2_service」整条链路跑通（先有闭环，再谈效果）；
2. :func:`from_eval_dataset` —— 把人工评测集（``server/eval/dataset.jsonl``）
   直接转成多标签样本，顺带用弱标注补齐未标类别。

公开语料（SenResume / MSRA / Resume / Weibo）经人工清洗后，只要落成同样的
``{"text": ..., "labels": {类别: bool}}`` 就能混训，不需要改训练脚本。
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from .labels import BENIGN, LABELS, SENSITIVE_LABELS, is_benign_marked, weak_label

# 身份证校验位表（GB 11643-1999 MOD 11-2）
_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK = "10X98765432"
_REGIONS = ("110105", "320102", "440304", "330106", "510107", "370102")
_SURNAMES = ("张", "王", "李", "赵", "陈", "刘", "杨", "黄", "周", "吴")
_GIVEN_NAMES = ("伟", "芳", "娜", "敏", "静", "强", "磊", "洋", "艳", "勇", "军", "杰")
_DOMAINS = ("example.com", "corp.example", "mail.test")
_STREETS = ("五四路", "人民路", "中山路", "解放大道", "经十路")
_CITIES = (
    ("福建省", "福州市", "鼓楼区"),
    ("江苏省", "南京市", "玄武区"),
    ("广东省", "深圳市", "南山区"),
    ("浙江省", "杭州市", "西湖区"),
)
_WORDS = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel")
_DRUGS = ("二甲双胍", "阿莫西林", "布洛芬", "头孢克肟", "氯沙坦")
_SYMPTOMS = ("反复咳嗽两周", "间断性头痛", "发热三天", "上腹隐痛一月")
_CONTEXTS = (
    "订单备注：{v}",
    "工单内容：{v}",
    "接口返回 {v}",
    "配置项：{v}",
    "短信正文：{v}",
    "客服记录：客户反馈 {v}",
)
_BENIGN_TEXTS = (
    "项目周报：本周完成接口联调，下周进入压测阶段，暂无风险。",
    "产品说明：本系统支持文档上传、分级识别与审计导出，全部本地运行。",
    "运维公告：今晚 22:00-23:00 进行数据库例行备份，服务不受影响。",
    "会议纪要：确定下季度重点是提升识别准确率与页面响应速度。",
    "测试说明：本页数据均为虚构示例，仅用于功能联调与演示。",
)
_FAKE_VALUE_TEXTS = (
    "示例数据：手机号 13800138000（虚构，仅用于联调）",
    "测试账号：客户张三，身份证 11010519491231002X，请勿用于生产",
    "占位符 phone=13000000000，mock 数据，接口压测用",
    "样例 JSON：{{\"api_key\": \"sk-EXAMPLE000000\", \"note\": \"demo only\"}}",
)


def make_id_card(rng: random.Random) -> str:
    """生成校验位合法的 18 位身份证号（可用于训练样本，不是真实个人信息）。"""
    region = rng.choice(_REGIONS)
    birth = f"{rng.randint(1970, 2005)}{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}"
    seq = f"{rng.randint(1, 999):03d}"
    body = region + birth + seq
    total = sum(int(ch) * weight for ch, weight in zip(body, _ID_WEIGHTS))
    return body + _ID_CHECK[total % 11]


def _luhn_check_digit(partial: str) -> int:
    total = 0
    for index, ch in enumerate(reversed(partial)):
        value = int(ch)
        if index % 2 == 0:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return (10 - total % 10) % 10


def make_bank_card(rng: random.Random) -> str:
    """生成 Luhn 校验通过的 16 位银行卡号。"""
    prefix = rng.choice(("622202", "621700", "622848", "621226"))
    body = prefix + "".join(str(rng.randint(0, 9)) for _ in range(9))
    return body + str(_luhn_check_digit(body))


def make_phone(rng: random.Random) -> str:
    return "1" + rng.choice("35789") + "".join(str(rng.randint(0, 9)) for _ in range(9))


def make_email(rng: random.Random) -> str:
    return f"{rng.choice(_WORDS)}.{rng.randint(1, 99)}@{rng.choice(_DOMAINS)}"


def make_ip(rng: random.Random) -> str:
    return f"{rng.choice((10, 172, 192, 203))}.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"


def make_secret(rng: random.Random) -> str:
    kind = rng.choice(("api", "jwt", "pem"))
    if kind == "api":
        return "api_key=sk-" + "".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789") for _ in range(16))
    if kind == "jwt":
        return (
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9."
            + "".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(20))
            + ".signaturepart"
        )
    return "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA..."  # noqa: S105 - 演示用占位


def make_name(rng: random.Random) -> str:
    return rng.choice(_SURNAMES) + rng.choice(_GIVEN_NAMES)


def make_address(rng: random.Random) -> str:
    province, city, district = rng.choice(_CITIES)
    return f"{province}{city}{district}{rng.choice(_STREETS)}{rng.randint(1, 300)}号"


def make_medical_text(rng: random.Random) -> str:
    return f"患者主诉：{rng.choice(_SYMPTOMS)}，诊断：待查，用药：{rng.choice(_DRUGS)}"


def make_password(rng: random.Random) -> str:
    return f"password={' '.join(rng.choice('abcdefghijklmnopqrstuvwxyz0123456789') for _ in range(3)).replace(' ', '')}"


_BUILDERS = {
    "phone": make_phone,
    "id_card": make_id_card,
    "bank_card": make_bank_card,
    "email": make_email,
    "ip": make_ip,
    "secret": make_secret,
    "name": make_name,
    "address": make_address,
    "medical": make_medical_text,
    "password": make_password,
}

# 组合样本：真实业务里这几类经常同时出现（对应 L1 的共现升级）
_COMBOS: tuple[tuple[str, ...], ...] = (
    ("name", "phone"),
    ("name", "phone", "id_card"),
    ("id_card", "bank_card"),
    ("email", "phone"),
    ("name", "address"),
    ("phone", "address"),
)


@dataclass(frozen=True)
class Example:
    """一条训练样本：文本 + 多标签（含 benign）。"""

    text: str
    labels: dict[str, bool] = field(default_factory=dict)
    source: str = "synthetic"

    def positives(self) -> list[str]:
        return [name for name in LABELS if self.labels.get(name)]

    def to_dict(self) -> dict[str, object]:
        return {"text": self.text, "labels": self.labels, "source": self.source}

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "Example":
        labels = payload.get("labels") or {}
        if not isinstance(labels, dict):
            raise ValueError("labels 必须是 {类别: bool} 对象")
        unknown = set(labels) - set(LABELS)
        if unknown:
            raise ValueError(f"未知标签：{sorted(unknown)}")
        text = str(payload.get("text", ""))
        if not text.strip():
            raise ValueError("样本 text 不能为空")
        return cls(
            text=text,
            labels={name: bool(labels.get(name, False)) for name in LABELS},
            source=str(payload.get("source", "imported")),
        )


def _zero_labels() -> dict[str, bool]:
    return {name: False for name in LABELS}


def _render_sensitive(label: str, rng: random.Random) -> str:
    value = _BUILDERS[label](rng)
    if label in ("name", "address", "medical", "secret", "password"):
        return value if label in ("secret", "password", "medical") else f"联系人：{value}，请回电"
    return rng.choice(_CONTEXTS).format(v=value)


_BENIGN_MARKERS_ZH: tuple[str, ...] = (
    "示例",
    "样例",
    "测试数据",
    "演示数据",
    "占位数据",
    "mock 数据",
    "假数据",
    "虚构数据",
    "仅供联调",
    "压测样例",
    "脱敏样例",
    "联调测试",
)
_BENIGN_TOPICS: tuple[str, ...] = (
    "本周完成接口联调，下周进入压测阶段，暂无风险。",
    "本页内容全部为虚构示例，仅用于功能演示。",
    "本系统支持文档上传、分级识别与审计导出，全部本地运行。",
    "今晚 22:00-23:00 进行数据库例行备份，服务不受影响。",
    "下季度重点是提升识别准确率与页面响应速度。",
)


def _hard_benign_text(rng: random.Random) -> str:
    """带示例标记、正文里却确实出现敏感值的困难负例。

    真实文档里常见「示例数据：手机号 138…」这类写法：只靠规则会把演示材料
    误判为泄露，模型必须学会让 benign 压过敏感特征。
    """
    marker = rng.choice(_BENIGN_MARKERS_ZH)
    phone = make_phone(rng)
    id_card = make_id_card(rng)
    email = make_email(rng)
    body = rng.choice(
        (
            f'{{"name": "张三", "phone": "{phone}", "id_card": "{id_card}"}}',
            f"username=demo&email={email}&phone={phone}",
            f"name,phone\n李四,{phone}",
            f"客户张三，电话 {phone}，身份证 {id_card}",
            f"api_key=sk-EXAMPLE{''.join(rng.choice('ABCDEFGH0123456789') for _ in range(12))}",
            f"银行卡 {make_bank_card(rng)} 用于支付联调",
            f"password=P@ssw0rd_Example，请勿用于生产",
            f"token=eyJhbGciOiJIUzI1NiJ9.{phone}.signature-preview",
            f"身份证占位符 {{ID_PLACEHOLDER}} 与手机 {{PHONE_PLACEHOLDER}}",
        )
    )
    tail = rng.choice(("不是真实数据", "仅用于联调", "请勿用于生产", "文档占位", "虚构内容",
                       "公开测试数据", "压测假号码", "JSON 占位示例"))
    return f"{marker}：{body}（{tail}）"


def synthetic_examples(count: int = 800, seed: int = 42) -> list[Example]:
    """生成可复现的自举数据集：约 60% 单标签、10% 组合、30% benign。

    benign 里六成是「带示例标记 + 真敏感形态」的困难负例——这是压低误报的关键，
    占位的普通说明文字教不会模型该压过什么。
    """
    if count <= 0:
        raise ValueError("count 必须为正")
    rng = random.Random(seed)
    examples: list[Example] = []
    benign_quota = max(1, int(count * 0.3))
    combo_quota = max(1, int(count * 0.1))
    single_quota = count - benign_quota - combo_quota

    for index in range(single_quota):
        label = SENSITIVE_LABELS[index % len(SENSITIVE_LABELS)]
        labels = _zero_labels()
        labels[label] = True
        # 追加变化的工作单号：既贴近真实业务抬头，也让样本不重复（便于划分去重）
        text = _render_sensitive(label, rng) + f"（工单号 {rng.randint(100000, 999999)}）"
        examples.append(Example(text=text, labels=labels))

    for index in range(combo_quota):
        combo = _COMBOS[index % len(_COMBOS)]
        values = [f"{name}={_BUILDERS[name](rng)}" for name in combo]
        labels = _zero_labels()
        for name in combo:
            labels[name] = True
        examples.append(Example(text="订单信息：" + "，".join(values), labels=labels))

    for index in range(benign_quota):
        if index % 5 < 2:  # 40%：普通说明文字（不含敏感形态）
            text = f"{rng.choice(_BENIGN_MARKERS_ZH)}说明：{rng.choice(_BENIGN_TOPICS)}"
        else:  # 60%：困难负例
            text = _hard_benign_text(rng)
        if not is_benign_marked(text):
            text = "示例数据：" + text
        weak = weak_label(text)
        labels = _zero_labels()
        labels[BENIGN] = True
        for name in SENSITIVE_LABELS:
            labels[name] = bool(weak.labels.get(name))
        examples.append(Example(text=text, labels=labels))

    rng.shuffle(examples)
    return examples


# weibo NER 的实体标注 -> 本项目的类别键。
# 语料是 OntoNotes 风格：类型带后缀（per.nam 专名 / per.nom 代称 / gpe.nam 地名），
# 只把「专名」映射成 name / address：遍称（老百姓、网友们）当人名会污染训练集。
_WEIBO_NAME_TYPES = {"per.nam", "per"}
_WEIBO_ADDRESS_TYPES = {"gpe.nam", "loc.nam", "loc", "gpe"}
_WEIBO_ENTITY_TYPES = {"PER": "name", "LOC": "address", "GPE": "address"}


def _weibo_entity_types(item: dict) -> set[str]:
    """从一条语料里取出可用的类别键；优先用 entities 字段，退化时解析 tags。"""
    found: set[str] = set()
    for entity in item.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        kind = str(entity.get("type", "")).strip().lower()
        if kind in _WEIBO_NAME_TYPES:
            found.add("name")
        elif kind in _WEIBO_ADDRESS_TYPES:
            found.add("address")
    if found:
        return found
    for tag in item.get("tags") or []:
        key = str(tag).upper()
        if len(key) > 2 and key[1] == "-":
            key = key[2:]
        if "." in key:
            key, suffix = key.split(".", 1)
            # tags 里的 .NOM 是代称，同样不当专名用
            if suffix.upper() == "NOM":
                continue
        mapped = _WEIBO_ENTITY_TYPES.get(key)
        if mapped:
            found.add(mapped)
    return found


def from_weibo_ner(
    path: str | Path, limit: int = 400, seed: int = 2026
) -> list[Example]:
    """把公开语料 weibo NER 的叙述句转成 L2 训练样本（用公开 NER 标注做弱监督）。

    取舍：
    - 只取带 PER/LOC 标注的句子，映射成 name / address 两类，再用 L1 弱标注补齐其它类别；
    - 全 O 的句子**不**标 benign：微博里看着无害的句子也可能含未标注的联系方式，
      硬标 benign 等于把噪声教给模型（这一点在 l2_training/README 里也写了）。
    """
    source_path = Path(path)
    if not source_path.is_file():
        raise FileNotFoundError(f"缺少公开语料文件：{source_path}")
    raw = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("weibo NER 语料应为 JSON 数组（每项含 tokens / tags）")

    rng = random.Random(seed)
    shuffled = list(raw)
    rng.shuffle(shuffled)

    examples: list[Example] = []
    seen: set[str] = set()
    for item in shuffled:
        if len(examples) >= limit:
            break
        if not isinstance(item, dict):
            continue
        text = "".join(str(token) for token in item.get("tokens") or []).strip()
        if len(text) < 8 or text in seen:
            continue
        labels = _zero_labels()
        entity_types = _weibo_entity_types(item)
        has_entity = bool(entity_types)
        for typ in entity_types:
            labels[typ] = True
        weak = weak_label(text)
        for name in SENSITIVE_LABELS:
            if weak.labels.get(name):
                labels[name] = True
        if not has_entity and not any(labels[name] for name in SENSITIVE_LABELS):
            continue
        seen.add(text)
        examples.append(Example(text=text, labels=labels, source="weibo-ner"))
    return examples


def from_eval_dataset(path: str | Path) -> list[Example]:
    """把人工评测集转成训练样本：金标类别为准，弱标注补齐。"""
    source_path = Path(path)
    examples: list[Example] = []
    for line in source_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        text = str(item.get("content", ""))
        if not text.strip():
            continue
        labels = _zero_labels()
        weak = weak_label(text)
        for name in SENSITIVE_LABELS:
            labels[name] = bool(weak.labels.get(name))
        for name in item.get("types") or []:
            if name in labels:
                labels[name] = True
        # 评测集里的 SAFE 行本身就是"正常内容"，作为 benign 正例
        labels[BENIGN] = weak.labels.get(BENIGN, False) or item.get("level") == "SAFE"
        examples.append(
            Example(text=text, labels=labels, source=f"eval:{item.get('id', 'unknown')}")
        )
    return examples


def split_examples(
    examples: list[Example], val_ratio: float = 0.15, seed: int = 42
) -> tuple[list[Example], list[Example]]:
    """按比例随机划分训练/验证集（可复现，且尽量保证两边都有正例）。"""
    if not examples:
        raise ValueError("样本为空，无法划分")
    if not 0.0 <= val_ratio < 1.0:
        raise ValueError("val_ratio 必须在 [0, 1) 之间")
    # 先按文本去重：真实语料里同一段话常被重复收录，重复样本跨训练/验证集会虚高指标
    unique: list[Example] = []
    seen: set[str] = set()
    for example in examples:
        key = example.text.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(example)
    if len(unique) < 2:
        raise ValueError("去重后样本不足 2 条，无法划分训练/验证集")
    rng = random.Random(seed)
    shuffled = list(unique)
    rng.shuffle(shuffled)
    if val_ratio == 0.0:
        return shuffled, []
    val_size = max(1, int(len(shuffled) * val_ratio))
    val_size = min(val_size, len(shuffled) - 1)
    return shuffled[val_size:], shuffled[:val_size]


def write_jsonl(path: str | Path, examples: list[Example]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(example.to_dict(), ensure_ascii=False) for example in examples]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return target


def load_jsonl(path: str | Path) -> list[Example]:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"数据集不存在：{source}")
    examples: list[Example] = []
    for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            examples.append(Example.from_dict(json.loads(line)))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"{source} 第 {number} 行不合法：{exc}") from exc
    return examples
