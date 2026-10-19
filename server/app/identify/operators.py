"""隐私增强算子：差分隐私 Laplace 扰动 与 l-多样性校验。

需求分析创新点 5 要求"数值字段 Laplace 扰动 + 分组数据 l-多样性"，本模块先把
算子与判定逻辑落地（MVP 阶段可只用于数值字段演示，不阻塞主链路）。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field


def laplace_noise(epsilon: float, sensitivity: float = 1.0, rng: random.Random | None = None) -> float:
    """Laplace(0, sensitivity/epsilon) 采样；epsilon 越小噪声越大。"""
    if epsilon <= 0:
        raise ValueError("epsilon 必须为正数")
    scale = sensitivity / epsilon
    generator = rng or random
    # 反变换采样：u ∈ (-0.5, 0.5) -> -scale * sign(u) * ln(1 - 2|u|)
    u = generator.random() - 0.5
    return -scale * (1 if u > 0 else -1) * math.log(1 - 2 * abs(u))


def perturb_numeric(
    value: float, epsilon: float = 1.0, sensitivity: float = 1.0, rng: random.Random | None = None
) -> float:
    """对数值字段做一次 Laplace 扰动（保留一位小数便于展示）。"""
    return round(value + laplace_noise(epsilon, sensitivity, rng), 1)


@dataclass(slots=True)
class LDiversityReport:
    """l-多样性检查结果。violations 为不满足 l 的等价类。"""

    ok: bool
    l: int
    groups: int
    violations: list[dict[str, object]] = field(default_factory=list)


def check_l_diversity(
    rows: list[dict[str, object]],
    quasi_keys: list[str],
    sensitive_key: str,
    l: int = 2,
) -> LDiversityReport:
    """检查每个准标识符等价类里敏感值的多样性是否 ≥ l。

    ``rows`` 是记录列表；``quasi_keys`` 是可用于背景知识关联的字段（如年龄、邮编），
    ``sensitive_key`` 是需要保护的字段（如诊断结果）。
    """
    if l < 1:
        raise ValueError("l 必须为正整数")
    buckets: dict[tuple[object, ...], set[object]] = {}
    for row in rows:
        key = tuple(row.get(field_name) for field_name in quasi_keys)
        buckets.setdefault(key, set()).add(row.get(sensitive_key))

    violations: list[dict[str, object]] = []
    for key, values in buckets.items():
        if len(values) < l:
            violations.append(
                {
                    "quasi": {name: value for name, value in zip(quasi_keys, key)},
                    "sensitive_values": len(values),
                    "required": l,
                }
            )
    return LDiversityReport(ok=not violations, l=l, groups=len(buckets), violations=violations)


def generalize_numeric(value: float, bucket: int = 10) -> str:
    """数值泛化：把一个具体数值落到区间里（差分隐私之外的另一种低成本手段）。"""
    if bucket <= 0:
        raise ValueError("bucket 必须为正整数")
    lower = int(value // bucket) * bucket
    return f"{lower}-{lower + bucket}"
