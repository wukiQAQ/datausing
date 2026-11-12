"""统一筛选条件解析：任务 / 级别 / 类型 / 内容形态 / 时间窗 / 时区。

看板（/stats）与明细（/findings、/alerts）必须用同一套口径，否则会出现
「统计说 10 条、点进去只看到 6 条」的演示事故。本模块只做三件事：

1. ``parse_filter(...)`` 把查询参数归一化成 :class:`Filter`（非法值抛 ``ValueError``，
   由接口层统一转 400）；
2. ``Filter.where_sql(...)`` 生成 SQL 条件与参数（findings 与 alerts 共用，
   表别名不同而已）；
3. 时间口径：数据库存 UTC 秒级 ISO（``...Z``），筛选边界一律转 UTC 比较；
   按日聚合用 ``tz_offset``（分钟）切当地日期。

日期语义（与契约一致）：

- ``start`` / ``end`` 支持 ``YYYY-MM-DD`` 或带时区偏移的 ISO8601（如
  ``2026-11-05T00:00:00+08:00``）；只给日期时按当地时区补 00:00，
  ``end`` 额外加一天，保证 ``[start, end)`` 覆盖整天；
- ``end`` 是开区间上界（``created_at < end``）；
- ``days`` 相对窗口：``end`` 缺省为当前时刻，``start = end - days``；
- ``start`` / ``end`` 优先于 ``days``（只补缺的那一端）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .identify import Level, entity_label, parse_entity_type

# 时间窗与偏移的合法范围：-840 ~ +840 分钟覆盖 UTC-14 ~ UTC+14
TZ_OFFSET_MIN = -840
TZ_OFFSET_MAX = 840
DAYS_MIN = 1
DAYS_MAX = 3650

# 无显式时间筛选时，看板趋势只回看最近 90 个当地日（前端补零压力可控）
TREND_DAYS = 90

# 级别展示顺序：看板/维度统一按严重程度从高到低
LEVEL_ORDER: tuple[str, ...] = ("L3", "L2", "L1", "SAFE")
LEVEL_LABELS: dict[str, str] = {"L3": "高危", "L2": "中危", "L1": "低危", "SAFE": "安全"}

# 内容形态中文名（tasks.content_type）；未登记的新形态退回原值
CONTENT_TYPE_LABELS: dict[str, str] = {
    "text": "文本",
    "json": "JSON",
    "csv": "CSV",
    "sql": "SQL",
    "docx": "Word",
    "xlsx": "Excel",
    "pdf": "PDF",
    "image": "图片",
    "pcap": "流量包",
}


def type_label(type_key: str) -> str:
    """类别英文键 → 中文展示名；未知键原样返回（不丢信息）。"""
    parsed = parse_entity_type(type_key)
    return entity_label(parsed) if parsed else str(type_key)


def content_type_label(value: str) -> str:
    """内容形态中文名；未登记的形态原样返回。"""
    key = str(value or "").strip()
    return CONTENT_TYPE_LABELS.get(key, key)


def local_offset(tz_offset: int) -> timezone:
    """分钟偏移 → 固定时区（例如 480 → UTC+08:00）。"""
    return timezone(timedelta(minutes=int(tz_offset)))


def utc_text(dt: datetime) -> str:
    """统一成本库格式：UTC、秒级、``...Z``。"""
    return dt.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time_bound(value: str, *, is_end: bool, tz_offset: int) -> str:
    """把日期/ISO 时间统一转成 UTC 存储格式；非法时抛 ``ValueError``。"""
    text = str(value or "").strip()
    tz = local_offset(tz_offset)
    try:
        if len(text) == 10:  # 纯日期：按当地时区补边界
            day = date.fromisoformat(text)
            dt = datetime(day.year, day.month, day.day, tzinfo=tz)
            if is_end:
                dt += timedelta(days=1)  # 开区间上界 → 次日 00:00
        else:
            normalized = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:  # 不带偏移的 ISO：按前端声明的当地时区理解
                dt = dt.replace(tzinfo=tz)
    except ValueError as exc:
        raise ValueError(f"时间参数格式非法：{value!r}（支持 YYYY-MM-DD 或 ISO8601）") from exc
    return utc_text(dt)


def _parse_types(types: str | None) -> list[str]:
    """逗号分隔的类别键 → 去重后的英文键列表；非法类别抛 ``ValueError``。"""
    if not types:
        return []
    parsed: list[str] = []
    for raw in str(types).split(","):
        token = raw.strip()
        if not token:
            continue
        entity = parse_entity_type(token)
        if entity is None:
            raise ValueError(f"未知的敏感数据类型: {token}")
        value = entity.value
        if value not in parsed:
            parsed.append(value)
    return parsed


@dataclass
class Filter:
    """归一化后的筛选条件；findings 与 alerts 共用同一套字段。"""

    task_id: str | None = None
    level: str | None = None
    types: list[str] = field(default_factory=list)
    content_type: str | None = None
    start: str | None = None  # UTC ISO，含
    end: str | None = None  # UTC ISO，不含（开区间上界）
    days: int | None = None
    tz_offset: int = 0
    # alerts 表没有 SAFE 记录：传 SAFE 时返回空集而不是报错
    for_alerts: bool = False
    matches_nothing: bool = False

    @property
    def has_time_filter(self) -> bool:
        """是否显式给了时间筛选（决定看板趋势是否限 90 天）。"""
        return bool(self.start or self.end or self.days)

    def echo(self, *, trend_limited: bool | None = None) -> dict[str, Any]:
        """回显给前端，便于确认「当前数字是哪些筛选算出来的」。"""
        data: dict[str, Any] = {
            "task_id": self.task_id,
            "level": self.level,
            "types": list(self.types),
            "content_type": self.content_type,
            "start": self.start,
            "end": self.end,
            "days": self.days,
            "tz_offset": self.tz_offset,
        }
        if trend_limited is not None:
            data["trend_limited"] = trend_limited
        return data

    def where_sql(self, alias: str = "f", task_alias: str = "t") -> tuple[str, list[Any]]:
        """生成 ``WHERE ...`` 片段与参数；无筛选时返回 ``("", [])``。

        ``alias`` 是 findings/alerts 行别名，``task_alias`` 是 LEFT JOIN 的 tasks 别名。
        content_type 走 tasks 表，因此查询侧必须先 ``LEFT JOIN tasks``。
        """
        if self.matches_nothing:
            return "WHERE 0", []
        conditions: list[str] = []
        params: list[Any] = []
        if self.task_id:
            conditions.append(f"{alias}.task_id=?")
            params.append(self.task_id)
        if self.level:
            conditions.append(f"{alias}.level=?")
            params.append(self.level)
        if self.content_type:
            conditions.append(f"{task_alias}.content_type=?")
            params.append(self.content_type)
        if self.start:
            conditions.append(f"{alias}.created_at>=?")
            params.append(self.start)
        if self.end:
            conditions.append(f"{alias}.created_at<?")
            params.append(self.end)
        if self.types:
            # types 是逗号分隔串：两端补逗号后 LIKE '%,key,%'，避免 phone 命中 ip 之类的子串误判
            parts: list[str] = []
            for key in self.types:
                parts.append(f"(','||{alias}.types||',') LIKE ?")
                params.append(f"%,{key},%")
            conditions.append("(" + " OR ".join(parts) + ")")
        if not conditions:
            return "", []
        return "WHERE " + " AND ".join(conditions), params


def parse_filter(
    *,
    task_id: str | None = None,
    level: str | None = None,
    types: str | None = None,
    content_type: str | None = None,
    start: str | None = None,
    end: str | None = None,
    days: int | None = None,
    tz_offset: int | None = 0,
    for_alerts: bool = False,
) -> Filter:
    """解析并校验统一筛选参数；任何非法值抛 ``ValueError``。

    ``level`` 接受 L3/L2/L1/SAFE（也接受 HIGH/MID/LOW/中文），统一成枚举值；
    alerts 场景传 SAFE 不报错，而是标记 ``matches_nothing`` 返回空集。
    """
    tz = 0 if tz_offset is None else int(tz_offset)
    if tz < TZ_OFFSET_MIN or tz > TZ_OFFSET_MAX:
        raise ValueError(f"tz_offset 必须在 {TZ_OFFSET_MIN}~{TZ_OFFSET_MAX} 分钟之间")

    normalized_level: str | None = None
    # alerts 表没有 SAFE 记录：传 SAFE 时返回空集而不是报错（参数校验照常执行）
    matches_nothing = False
    if level:
        parsed_level = Level.parse(level)  # 非法时抛「未知的敏感级别」
        normalized_level = parsed_level.value
        matches_nothing = for_alerts and parsed_level == Level.SAFE

    if days is not None:
        if days < DAYS_MIN or days > DAYS_MAX:
            raise ValueError(f"days 必须在 {DAYS_MIN}~{DAYS_MAX} 之间")

    start_utc = _parse_time_bound(start, is_end=False, tz_offset=tz) if start else None
    end_utc = _parse_time_bound(end, is_end=True, tz_offset=tz) if end else None
    if days is not None:
        # days 只补缺失的一端，显式 start/end 优先
        if end_utc is None:
            # 时间戳只存到秒，开区间上界取当前时刻会把"同一秒内刚落库"的数据排除，
            # 因此默认上界向后放 1 秒，保证 days 窗口含当前这一秒。
            end_utc = utc_text(datetime.now(timezone.utc) + timedelta(seconds=1))
        if start_utc is None:
            end_dt = datetime.strptime(end_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            start_utc = utc_text(end_dt - timedelta(days=int(days)))

    return Filter(
        task_id=task_id,
        level=normalized_level,
        types=_parse_types(types),
        content_type=content_type,
        start=start_utc,
        end=end_utc,
        days=days,
        tz_offset=tz,
        for_alerts=for_alerts,
        matches_nothing=matches_nothing,
    )


def day_modifier(tz_offset: int) -> str:
    """SQLite ``date()`` 的时区修饰符（如 ``480 minutes``、``-300 minutes``）。"""
    return f"{int(tz_offset)} minutes"


def trend_start_day(tz_offset: int, *, days: int = TREND_DAYS) -> str:
    """最近 N 个当地日的起始日期（含今天，返回 ``YYYY-MM-DD``）。"""
    local_now = datetime.now(timezone.utc) + timedelta(minutes=int(tz_offset))
    return (local_now.date() - timedelta(days=max(1, int(days)) - 1)).isoformat()
