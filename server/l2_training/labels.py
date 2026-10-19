"""L2 语义分类的标签体系与弱标注。

分工：L1 只看"长得像不像"，L2 要看"这段文本到底是不是在处理敏感信息"。
因此标签 = 10 个敏感类别 + 1 个 ``benign``（正常/示例数据）。

``benign`` 是这套模型的关键：企业内部大量文档里出现手机号/身份证号是**测试数据、
示例、占位符**，只靠正则必然误报；让模型学会"看到 示例/测试/虚构 就投 benign"，
是第三章多模型协同里最能拿分的一点。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

BENIGN = "benign"

#: 与 L1/存储层保持一致的 10 个敏感类别
SENSITIVE_LABELS: tuple[str, ...] = (
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

#: 训练时的输出顺序（最后一维固定是 benign，推理端 labels.json 直接照抄）
LABELS: tuple[str, ...] = (*SENSITIVE_LABELS, BENIGN)

#: 示例/测试数据标记：命中任意一个就倾向 benign（不区分大小写）
BENIGN_MARKERS: tuple[str, ...] = (
    "测试",
    "示例",
    "样例",
    "演示",
    "模板",
    "占位",
    "压测",
    "dummy",
    "demo",
    "test",
    "mock",
    "placeholder",
    "仅供",
    "仅用于",
    "联调",
    "假数据",
    "虚构",
    "xxxx",
)

_BENIGN_RE = re.compile("|".join(re.escape(marker) for marker in BENIGN_MARKERS), re.IGNORECASE)
_NAME_RE = re.compile(r"(?:客户|用户|联系人|收件人|姓名|患者)[:：\s]*[\u4e00-\u9fa5]{2,4}")
_ADDRESS_RE = re.compile(r"(?:省|市|区|县|镇|街|路|号|室|楼|栋)")
_MEDICAL_RE = re.compile(r"(?:诊断|病历|主诉|既往史|用药|处方|症状|住院|门诊|检验|检查结果)")
_PASSWORD_RE = re.compile(r"(?:password|passwd|pwd|口令|密码)\s*[:=：]\s*\S+", re.IGNORECASE)


@dataclass(frozen=True)
class WeakLabel:
    """弱标注结果：每个标签是否出现，以及判定依据（便于抽查与报告）。"""

    labels: dict[str, bool]
    evidence: tuple[str, ...] = ()

    def positives(self) -> list[str]:
        return [name for name, on in self.labels.items() if on]


def is_benign_marked(text: str) -> bool:
    """是否带"示例/测试"类标记（L2 的 benign 判定与数据构造共用）。"""
    return bool(_BENIGN_RE.search(text))


def weak_label(text: str) -> WeakLabel:
    """用 L1 规则 + 轻量上下文启发式给文本打多标签。

    公开语料往往只有粗标（甚至无标），先跑一遍弱标注得到"可训练"的起点，
    再由人工抽查少量样本修正——这是 20 天周期里最现实的标注策略。
    """

    # 延迟导入：单独跑数据分析/构造数据时不必先起服务和配置
    from app.identify.rules import detect

    labels = {name: False for name in SENSITIVE_LABELS}
    evidence: list[str] = []
    for hit in detect(text):
        key = hit.type.value if hasattr(hit.type, "value") else str(hit.type)
        if key in labels and not labels[key]:
            labels[key] = True
            evidence.append(f"l1:{hit.rule}")

    if not labels["name"] and _NAME_RE.search(text):
        labels["name"] = True
        evidence.append("heuristic:name_context")
    if not labels["address"] and len(_ADDRESS_RE.findall(text)) >= 2:
        labels["address"] = True
        evidence.append("heuristic:address_context")
    if not labels["medical"] and _MEDICAL_RE.search(text):
        labels["medical"] = True
        evidence.append("heuristic:medical_words")
    if not labels["password"] and _PASSWORD_RE.search(text):
        labels["password"] = True
        evidence.append("heuristic:password_kv")

    return WeakLabel(labels={**labels, BENIGN: is_benign_marked(text)}, evidence=tuple(evidence))
