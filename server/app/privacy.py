"""隐私增强导出：Laplace 差分隐私计数 + l-多样性校验。

需求分析创新点 5 要求「数值字段 Laplace 扰动 + 分组数据 l-多样性」。主链路的原文本来就不落库，
这里把两者落在**导出侧**，让「导出的报表本身」也带隐私预算：

- **差分隐私**：类别/分级计数是攻击者最常拿来反推个体的统计量。导出时对每个计数加
  Laplace(0, 1/epsilon) 噪声，epsilon 越小噪声越大，报表仍可用但无法据此确认某条记录是否存在；
- **l-多样性**：把「命中的类别组合」当准标识符、把「分级」当敏感属性，检查组内分级取值
  是否至少 l 种；不满足的组合会在导出摘要里点名（运维应做泛化或抑制后再对外提供）。

实现保持在最小闭环：只用标准库 + 既有 `app/identify/operators.py` 算子，不引入第三方库。
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field

from .identify.operators import check_l_diversity, laplace_noise
from .tasks.export import export_findings, export_meta, export_rows

DEFAULT_EPSILON = 1.0
DEFAULT_L = 3
EPSILON_MIN = 0.05
EPSILON_MAX = 10.0
L_MIN = 1
L_MAX = 20
LEVELS: tuple[str, ...] = ("L3", "L2", "L1", "SAFE")
QUASI_KEYS = ["types"]
SENSITIVE_KEY = "level"
PRIVACY_FORMATS = ("jsonl", "json")

NOTE = "计数已加 Laplace(0, 1/epsilon) 噪声（敏感度 1，负值截 0）；明细为脱敏副本、不含原文"


@dataclass(slots=True)
class PrivacySummary:
    """一次隐私导出的统计摘要（随文件一起导出，便于审计复核）。"""

    epsilon: float
    l: int
    total: int
    by_level: dict[str, int]
    by_type: dict[str, int]
    dp_by_level: dict[str, float]
    dp_by_type: dict[str, float]
    l_diversity: dict[str, object] = field(default_factory=dict)
    note: str = NOTE

    def to_dict(self) -> dict[str, object]:
        return {
            "epsilon": self.epsilon,
            "l": self.l,
            "total": self.total,
            "by_level": self.by_level,
            "by_type": self.by_type,
            "dp_by_level": self.dp_by_level,
            "dp_by_type": self.dp_by_type,
            "l_diversity": self.l_diversity,
            "note": self.note,
        }


def validate_params(epsilon: float, l: int) -> tuple[float, int]:
    """校验并归一化导出参数；越界直接拒绝，避免「以为加了隐私其实没加」。"""
    try:
        epsilon_value = float(epsilon)
    except (TypeError, ValueError) as exc:
        raise ValueError("epsilon 必须是数字") from exc
    if not EPSILON_MIN <= epsilon_value <= EPSILON_MAX:
        raise ValueError(f"epsilon 必须在 {EPSILON_MIN} ~ {EPSILON_MAX} 之间（越小噪声越大）")
    try:
        l_value = int(l)
    except (TypeError, ValueError) as exc:
        raise ValueError("l 必须是整数") from exc
    if not L_MIN <= l_value <= L_MAX:
        raise ValueError(f"l 必须在 {L_MIN} ~ {L_MAX} 之间")
    return epsilon_value, l_value


def _noisy(count: int, epsilon: float, rng: random.Random | None) -> float:
    """给计数加噪：保留一位小数并截到 0，避免导出负数条数。"""
    return max(0.0, round(count + laplace_noise(epsilon, sensitivity=1.0, rng=rng), 1))


def _types_of(row: dict[str, object]) -> list[str]:
    raw = row.get("types")
    if isinstance(raw, str):
        return [item for item in raw.split(",") if item]
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw if item]
    return []


def summarize(
    rows: list[dict[str, object]],
    epsilon: float = DEFAULT_EPSILON,
    l: int = DEFAULT_L,
    rng: random.Random | None = None,
) -> PrivacySummary:
    """对导出行做统计：原始计数、加噪计数与 l-多样性校验。"""
    epsilon_value, l_value = validate_params(epsilon, l)

    by_level: dict[str, int] = {key: 0 for key in LEVELS}
    by_type: dict[str, int] = {}
    for row in rows:
        level = str(row.get("level") or "SAFE")
        by_level[level] = by_level.get(level, 0) + 1
        for category in _types_of(row):
            by_type[category] = by_type.get(category, 0) + 1

    by_type = dict(sorted(by_type.items(), key=lambda item: (-item[1], item[0])))
    dp_by_level = {key: _noisy(value, epsilon_value, rng) for key, value in by_level.items()}
    dp_by_type = {key: _noisy(value, epsilon_value, rng) for key, value in by_type.items()}

    report = check_l_diversity(rows, QUASI_KEYS, SENSITIVE_KEY, l_value)
    l_diversity = {
        "ok": report.ok,
        "l": report.l,
        "groups": report.groups,
        "violations": report.violations[:50],
        "violation_count": len(report.violations),
    }
    return PrivacySummary(
        epsilon=epsilon_value,
        l=l_value,
        total=len(rows),
        by_level=by_level,
        by_type=by_type,
        dp_by_level=dp_by_level,
        dp_by_type=dp_by_type,
        l_diversity=l_diversity,
    )


def export_with_privacy(
    db,  # noqa: ANN001 - Database，避免循环导入
    task_id: str,
    fmt: str = "jsonl",
    epsilon: float = DEFAULT_EPSILON,
    l: int = DEFAULT_L,
    rng: random.Random | None = None,
) -> tuple[bytes, str, str]:
    """隐私增强导出：返回 (内容, Content-Type, 建议文件名)。

    jsonl 会在首行 meta 里带 ``privacy`` 摘要；json 则在 ``meta.privacy``。
    CSV/SQL 不做隐私导出（列结构与逐行写入不适合携带摘要），需要时先导 JSON 再转换。
    """
    fmt = (fmt or "jsonl").lower()
    if fmt not in PRIVACY_FORMATS:
        raise ValueError(f"隐私导出只支持 {', '.join(PRIVACY_FORMATS)}（CSV/SQL 无隐私摘要）")

    rows = export_rows(db, task_id)
    summary = summarize(rows, epsilon, l, rng)
    meta = export_meta(db, task_id, len(rows))
    meta["privacy"] = summary.to_dict()
    stamp = str(meta["exported_at"]).replace(":", "").replace("-", "")

    if fmt == "json":
        payload = {"meta": meta, "findings": rows}
        return (
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            "application/json; charset=utf-8",
            f"flowmask-{task_id}-privacy-{stamp}.json",
        )

    lines = [json.dumps(meta, ensure_ascii=False)]
    lines.extend(json.dumps(row, ensure_ascii=False) for row in rows)
    return (
        ("\n".join(lines) + "\n").encode("utf-8"),
        "application/x-ndjson; charset=utf-8",
        f"flowmask-{task_id}-privacy-{stamp}.jsonl",
    )
