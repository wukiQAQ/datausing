"""识别与分级共用的领域类型。

命名与 Go 版 ``internal/dlp`` 保持一致，方便两边对照阅读、也方便把已有测试用例平移。
类别键（``id_card`` 等）用于数据库与接口，中文名只用于展示与脱敏模板。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Level(str, Enum):
    """四级敏感度。字符串值即接口/存储里的取值。"""

    SAFE = "SAFE"
    LOW = "L1"
    MID = "L2"
    HIGH = "L3"

    @property
    def label(self) -> str:
        return {Level.SAFE: "安全", Level.LOW: "低危", Level.MID: "中危", Level.HIGH: "高危"}[self]

    @property
    def color(self) -> str:
        """前端四级色块：绿/黄/橙/红。"""
        return {Level.SAFE: "green", Level.LOW: "yellow", Level.MID: "orange", Level.HIGH: "red"}[self]

    @property
    def severity(self) -> int:
        """数值化严重程度，便于比较（0 SAFE / 1 L1 / 2 L2 / 3 L3）。"""
        return {Level.SAFE: 0, Level.LOW: 1, Level.MID: 2, Level.HIGH: 3}[self]

    @classmethod
    def parse(cls, value: str) -> "Level":
        text = (value or "").strip().upper()
        mapping = {
            "L3": cls.HIGH, "HIGH": cls.HIGH, "高危": cls.HIGH,
            "L2": cls.MID, "MID": cls.MID, "中危": cls.MID,
            "L1": cls.LOW, "LOW": cls.LOW, "低危": cls.LOW,
            "SAFE": cls.SAFE, "": cls.SAFE, "安全": cls.SAFE,
        }
        if text not in mapping:
            raise ValueError(f"未知的敏感级别: {value!r}")
        return mapping[text]


class EntityType(str, Enum):
    """敏感信息类别（GB/T 35273-2020 的 12 类个人信息的工程化子集）。"""

    PHONE = "phone"
    ID_CARD = "id_card"
    BANK_CARD = "bank_card"
    EMAIL = "email"
    IP = "ip"
    SECRET = "secret"
    PASSWORD = "password"
    NAME = "name"
    ADDRESS = "address"
    MEDICAL = "medical"


ENTITY_LABELS: dict[EntityType, str] = {
    EntityType.PHONE: "手机号",
    EntityType.ID_CARD: "身份证号",
    EntityType.BANK_CARD: "银行卡号",
    EntityType.EMAIL: "邮箱",
    EntityType.IP: "IP地址",
    EntityType.SECRET: "密钥凭据",
    EntityType.PASSWORD: "密码",
    EntityType.NAME: "姓名",
    EntityType.ADDRESS: "地址",
    EntityType.MEDICAL: "医疗信息",
}


def entity_label(entity: EntityType) -> str:
    """类别中文名；未登记的类别回退成类别键。"""
    return ENTITY_LABELS.get(entity, entity.value)


def parse_entity_type(value: str) -> EntityType | None:
    """接受英文键或中文名，返回类别；解析失败返回 None。"""
    text = (value or "").strip()
    for entity, label in ENTITY_LABELS.items():
        if text == label:
            return entity
    try:
        return EntityType(text)
    except ValueError:
        return None


@dataclass(slots=True)
class Hit:
    """一次命中。start/end 是原文中的字符下标（Python 字符串下标，非字节）。"""

    type: EntityType
    start: int
    end: int
    value: str
    weight: float
    confidence: float
    rule: str


@dataclass(slots=True)
class Result:
    """一次"识别 + 评分 + 分级"的完整结论。"""

    hits: list[Hit] = field(default_factory=list)
    score: float = 0.0
    level: Level = Level.SAFE
    types: list[EntityType] = field(default_factory=list)
    direct: bool = False
    degraded: bool = False
    # L2/L3 的类别级证据（没有字符区间，不参与脱敏，只影响评分与解释）
    extra_evidence: list[dict[str, object]] = field(default_factory=list)
    # L2 判定"示例/测试数据"时的分数压制系数（1.0 表示未压制）；
    # 归一化/上下文前缀会导致重算分数，必须把系数带过去，否则压制被覆盖。
    score_scale: float = 1.0
    # L3 复核抬升后的级别下限：归一化重算分数时不能被算回去
    level_floor: Level = Level.SAFE

    @property
    def type_keys(self) -> list[str]:
        return [t.value for t in self.types]

    @property
    def type_names(self) -> list[str]:
        return [entity_label(t) for t in self.types]

    @property
    def rule_ids(self) -> list[str]:
        seen: list[str] = []
        for hit in self.hits:
            if hit.rule not in seen:
                seen.append(hit.rule)
        return seen

    @property
    def confidence_vector(self) -> list[dict[str, object]]:
        """给审计日志用的置信度向量（可复算评分的依据）。"""
        vector = [
            {
                "type": hit.type.value,
                "confidence": round(hit.confidence, 4),
                "weight": round(hit.weight, 4),
                "rule": hit.rule,
            }
            for hit in self.hits
        ]
        vector.extend(self.extra_evidence)
        return vector
