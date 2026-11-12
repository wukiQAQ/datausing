"""本地 AI 助手：零依赖的规则引擎 + 可选的 Ollama 本机模型润色。

设计原则（与「数据不出本机」的产品定位一致）：

- **规则模式永远可用**：不联网、不引入任何第三方包，按字段名 / 用途关键词 / 级别
  计数给出脱敏方案。这就是 Ollama 未启动时的兜底路径。
- **输入不含敏感值**：本模块只接收字段名、级别计数、文本长度和用途说明，原文永远
  不会进到这里，因此日志和异常信息里不可能出现原文。
- **失败即降级**：ollama 模式调用本机模型失败（未启动、超时、返回非 JSON）时自动
  退回规则结论并标记 ``source="degraded"``，接口始终返回 200，保证检测页不被模型
  服务拖垮。

``Assistant(settings)`` 由路由按请求构造；构造本身不产生网络副作用，只有 ollama 模式
下的 ``suggest`` / ``advise`` / ``health`` / ``models`` 才会访问本机服务。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .core.config import Settings
from .scenarios import STRATEGY_KEYS, get_scenario

# 结论来源：本地规则 / 本机模型 / 模型失败后降级回规则
SOURCE_RULES = "rules"
SOURCE_LLM = "local_llm"
SOURCE_DEGRADED = "degraded"

# 探测本机 Ollama 的超时（秒）：健康检查要快，不能把页面卡住
_PROBE_TIMEOUT_S = 0.8

# 强制脱敏级别只允许 L1~L3；SAFE 等于不脱敏，不应出现在 mask_level 里
_MASK_LEVELS: tuple[str, ...] = ("L1", "L2", "L3")

# 本机模型可能返回级别别名（L3/HIGH 等价），解析时统一接受
_MIN_LEVEL_ALIASES: dict[str, str] = {
    "SAFE": "SAFE",
    "LOW": "SAFE",
    "MID": "MEDIUM",
    "MEDIUM": "MEDIUM",
    "HIGH": "HIGH",
    "L1": "MEDIUM",
    "L2": "MEDIUM",
    "L3": "HIGH",
}

# ---- 字段名关键词（只匹配字段「名」，不涉及任何值） ----

# 强身份/凭据类：身份证、银行卡、密码、令牌……出现即按高危处理
_ID_FIELD_MARKERS: tuple[str, ...] = (
    "id_card", "idcard", "id_no", "idno", "身份证", "证件号",
    "bank_card", "bankcard", "card_no", "cardno", "银行卡", "卡号",
    "password", "passwd", "密码", "secret", "密钥", "token", "令牌",
    "api_key", "apikey", "credential", "凭据",
)
# 只能直接屏蔽的字段（保留任何片段都会泄露凭据）
_BLOCK_FIELD_MARKERS: tuple[str, ...] = (
    "password", "passwd", "密码", "secret", "密钥", "token", "令牌",
    "api_key", "apikey", "credential", "凭据",
)
# 一般个人信息（联系方式/地址/姓名类）
_CONTACT_FIELD_MARKERS: tuple[str, ...] = (
    "phone", "mobile", "tel", "手机", "电话", "地址", "address",
    "邮箱", "email", "e-mail", "姓名", "name", "联系人", "contact",
)
# 姓名/患者/学生类：需要 NER 辅助识别自由文本里的人名
_NAME_FIELD_MARKERS: tuple[str, ...] = (
    "name", "姓名", "患者", "病人", "student", "学生", "病人姓名", "患者姓名",
)

# ---- 用途关键词 ----
_PUBLIC_PROMPT_WORDS: tuple[str, ...] = ("公开", "对外", "发布", "演示", "公示", "宣传", "展示")
_ANALYSIS_PROMPT_WORDS: tuple[str, ...] = ("分析", "统计", "科研", "建模", "训练", "研究", "挖掘")

# 级别强度排序：用于「至少 L2」「至少 L3」的提升判断
_LEVEL_RANK: dict[str, int] = {"SAFE": 0, "L1": 1, "L2": 2, "L3": 3}


# ---------------------------------------------------------------------------
# 小工具：payload 清洗与关键词匹配（全部防御式，坏输入不抛异常）
# ---------------------------------------------------------------------------


def _to_int(value: object) -> int:
    """尽量把计数转成整数，转不了就是 0（聚合数据可能来自前端手写 JSON）。"""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def _as_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _normalize_fields(fields: object) -> list[str]:
    """字段名统一小写去空白，方便做子串匹配。"""
    if not isinstance(fields, (list, tuple)):
        return []
    cleaned: list[str] = []
    for item in fields:
        text = str(item or "").strip().lower()
        if text:
            cleaned.append(text)
    return cleaned


def _hit_any(fields: list[str], markers: tuple[str, ...]) -> bool:
    return any(marker in field for field in fields for marker in markers)


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _normalize_level_counts(level_counts: object) -> dict[str, int]:
    """级别计数统一成大写键；兼容 L3/HIGH/DANGER 等不同来源的写法。"""
    result: dict[str, int] = {}
    if not isinstance(level_counts, dict):
        return result
    for raw_key, raw_value in level_counts.items():
        count = _to_int(raw_value)
        if count <= 0:
            continue
        key = str(raw_key or "").strip().upper()
        alias = {
            "DANGER": "L3", "高危": "L3",
            "MID": "L2", "中危": "L2",
            "LOW": "L1", "低危": "L1",
            "安全": "SAFE",
            "CRITICAL": "L3",
        }.get(key, key)
        result[alias] = result.get(alias, 0) + count
    return result


def _danger_count(level_counts: dict[str, int]) -> int:
    """高危命中数：L3/DANGER/HIGH 任一来源都算。"""
    return max(level_counts.get("L3", 0), level_counts.get("DANGER", 0), level_counts.get("HIGH", 0))


def _classify_fields(fields: list[str]) -> dict[str, bool]:
    return {
        "has_id": _hit_any(fields, _ID_FIELD_MARKERS),
        "has_block": _hit_any(fields, _BLOCK_FIELD_MARKERS),
        "has_contact": _hit_any(fields, _CONTACT_FIELD_MARKERS),
        "has_name": _hit_any(fields, _NAME_FIELD_MARKERS),
    }


def _default_retention(strategy: str) -> str:
    """规则模式下按策略给一句数据保留说明（与场景模板的 retention 同风格）。"""
    mapping = {
        "block": "原文不落库，导出文件只含屏蔽占位，不提供还原",
        "partial": "原文不落库，导出文件仅保留部分字符用于人工核对",
        "hash": "原文不落库，输出确定性加盐哈希，可跨表关联但不可还原",
        "generalize": "原文不落库，仅保留泛化区间或后四位，降低可识别性",
        "pseudonym": "原文不落库，服务端维护伪名映射，导出文件不含映射表",
        "encrypt": "原文不落库，密文只在服务端可解，阅读需二次授权",
    }
    return mapping.get(strategy, "原文不落库，仅输出脱敏结果")


# ---------------------------------------------------------------------------
# 本机模型输出解析（失败一律返回 None，由调用方降级）
# ---------------------------------------------------------------------------


def _strip_code_fence(text: str) -> str:
    """剥掉模型偶尔多写的 ```json 代码块围栏。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
        if cleaned[:4].lower() == "json":
            cleaned = cleaned[4:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    return cleaned.strip()


def _parse_llm_suggestion(raw: str) -> dict[str, object] | None:
    """解析模型给出的建议 JSON；字段非法就返回 None（视为不可用）。"""
    try:
        parsed = json.loads(_strip_code_fence(raw))
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    strategy = str(parsed.get("strategy") or "").strip().lower()
    mask_level = str(parsed.get("mask_level") or "").strip().upper()
    if strategy not in STRATEGY_KEYS or mask_level not in _MASK_LEVELS:
        return None
    min_level = _MIN_LEVEL_ALIASES.get(str(parsed.get("min_level") or "SAFE").strip().upper(), "SAFE")
    return {
        "strategy": strategy,
        "mask_level": mask_level,
        "min_level": min_level,
        "enable_l2": _as_bool(parsed.get("enable_l2")),
        "enable_l3": _as_bool(parsed.get("enable_l3")),
        "enable_ner": _as_bool(parsed.get("enable_ner")),
        "retention": str(parsed.get("retention") or "").strip()[:300],
        "rationale": str(parsed.get("rationale") or "").strip()[:300],
    }


def _parse_llm_advice(raw: str) -> dict[str, object] | None:
    """解析模型给出的研判 JSON；缺 summary 或两个列表都空就视为不可用。"""
    try:
        parsed = json.loads(_strip_code_fence(raw))
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    summary = str(parsed.get("summary") or "").strip()
    findings = [str(item).strip() for item in (parsed.get("findings") or []) if str(item).strip()] \
        if isinstance(parsed.get("findings"), (list, tuple)) else []
    recommendations = [str(item).strip() for item in (parsed.get("recommendations") or []) if str(item).strip()] \
        if isinstance(parsed.get("recommendations"), (list, tuple)) else []
    if not summary or (not findings and not recommendations):
        return None
    return {"summary": summary[:300], "findings": findings[:3], "recommendations": recommendations[:4]}


# 类别 key 到中文展示名（by_type 通常来自检测管道的类型统计）
_TYPE_LABELS: dict[str, str] = {
    "phone": "手机号",
    "mobile": "手机号",
    "id_card": "身份证号",
    "idcard": "身份证号",
    "bank_card": "银行卡号",
    "email": "邮箱",
    "address": "地址",
    "name": "姓名",
    "ip": "IP 地址",
    "secret": "访问令牌",
    "token": "访问令牌",
    "api_key": "访问令牌",
    "password": "密码",
    "medical": "医疗信息",
    "person": "个人信息",
}


class Assistant:
    """本地脱敏助手：规则引擎为主，可选用本机 Ollama 模型润色。"""

    def __init__(self, settings: Settings) -> None:
        """只读配置，不发请求；enabled/mode 决定后续行为。"""
        self.enabled: bool = bool(getattr(settings, "assistant_enabled", True))
        self.mode: str = str(getattr(settings, "assistant_mode", "rules") or "rules").strip().lower()
        self.endpoint: str = str(getattr(settings, "assistant_endpoint", "") or "").strip()
        self.model: str = str(getattr(settings, "assistant_model", "") or "").strip()
        self.timeout_s: float = float(getattr(settings, "assistant_timeout_s", 5.0) or 5.0)

    # -- 对外接口 ---------------------------------------------------------

    def suggest(self, payload: dict) -> dict:
        """推荐一套脱敏参数。``payload`` 只含字段名/计数/用途，不含原文。"""
        data = dict(payload or {})
        rule = self._rule_suggest(data)
        if not (self.enabled and self.mode == "ollama"):
            rule["source"] = SOURCE_RULES
            return rule
        try:
            raw = self._generate(self._build_suggest_prompt(data))
        except Exception:  # 网络/超时/连接拒绝：一律降级，不把底层异常抛给路由
            rule["source"] = SOURCE_DEGRADED
            return rule
        suggestion = _parse_llm_suggestion(raw)
        if suggestion is None:
            rule["source"] = SOURCE_DEGRADED
            return rule
        merged = dict(rule)
        merged.update({key: value for key, value in suggestion.items() if value not in ("", None)})
        merged["source"] = SOURCE_LLM
        return merged

    def advise(self, payload: dict) -> dict:
        """按聚合统计生成风险研判（不接触原文，只做文案与策略建议）。"""
        data = dict(payload or {})
        rule = self._rule_advise(data)
        if not (self.enabled and self.mode == "ollama"):
            rule["source"] = SOURCE_RULES
            return rule
        try:
            raw = self._generate(self._build_advise_prompt(data))
        except Exception:
            rule["source"] = SOURCE_DEGRADED
            return rule
        advice = _parse_llm_advice(raw)
        if advice is None:
            rule["source"] = SOURCE_DEGRADED
            return rule
        merged = dict(rule)
        merged["summary"] = advice["summary"]
        merged["findings"] = advice["findings"] or rule["findings"]
        merged["recommendations"] = advice["recommendations"] or rule["recommendations"]
        merged["source"] = SOURCE_LLM
        return merged

    def health(self) -> dict:
        """助手可用性：规则模式恒可用；ollama 模式快速探测本机 /api/tags。"""
        base = {"enabled": self.enabled, "mode": self.mode, "model": self.model}
        if not self.enabled:
            return {**base, "available": False, "note": "AI 助手已关闭（FLOWMASK_ASSISTANT_ENABLED=0）"}
        if self.mode != "ollama":
            return {**base, "available": True, "note": "本地规则模式（不联网）"}
        names = self._fetch_tags(_PROBE_TIMEOUT_S)
        if names is None:
            return {**base, "available": False, "note": "本机模型服务不可达（规则模式仍可用）"}
        return {**base, "available": True, "note": f"本机模型服务可用（{len(names)} 个模型）"}

    def models(self) -> dict:
        """本机 Ollama 已安装模型列表；规则模式下不探测，直接说明原因。"""
        if not self.enabled:
            return {"models": [], "endpoint": self.endpoint, "note": "AI 助手已关闭（FLOWMASK_ASSISTANT_ENABLED=0）"}
        if self.mode != "ollama":
            return {
                "models": [],
                "endpoint": self.endpoint,
                "note": "规则模式不调用本机模型服务；设置 FLOWMASK_ASSISTANT_MODE=ollama 后可用",
            }
        names = self._fetch_tags(_PROBE_TIMEOUT_S)
        if names is None:
            return {"models": [], "endpoint": self.endpoint, "note": "本机模型服务不可达（规则模式仍可用）"}
        note = "" if names else "本机模型服务未安装任何模型（ollama pull 后可用）"
        return {"models": names, "endpoint": self.endpoint, "note": note}

    # -- 规则引擎：建议 ---------------------------------------------------

    def _rule_suggest(self, payload: dict) -> dict:
        """规则版脱敏建议：先套场景模板，再按用途/计数做最小修正。"""
        scenario = get_scenario(payload.get("scenario")) if isinstance(payload.get("scenario"), str) else None
        fields = _normalize_fields(payload.get("fields"))
        prompt = str(payload.get("prompt") or "").strip()
        content_type = str(payload.get("content_type") or "text").strip().lower()
        level_counts = _normalize_level_counts(payload.get("level_counts"))
        danger = _danger_count(level_counts)
        kinds = _classify_fields(fields)
        reasons: list[str] = []

        if scenario is not None:
            strategy, mask_level = scenario.strategy, scenario.mask_level
            min_level = scenario.min_level
            retention = scenario.retention
            reasons.append(f"命中场景「{scenario.label}」，以场景模板为基线（{strategy} 策略、强制 {mask_level}）。")
        else:
            min_level = "SAFE"
            if kinds["has_id"]:
                strategy = "block" if kinds["has_block"] else "hash"
                mask_level = "L3"
                reasons.append("检测到身份证/银行卡/密码类高危字段名，按高危处理：隐去或哈希并强制 L3。")
            elif _contains_any(prompt, _ANALYSIS_PROMPT_WORDS):
                strategy, mask_level = "hash", "L2"
                reasons.append("用途为分析/统计，建议用加盐哈希脱敏，保留跨表关联与聚合能力。")
            elif kinds["has_contact"]:
                strategy, mask_level = "partial", "L2"
                reasons.append("检测到手机号/地址/邮箱/姓名等一般个人信息，建议部分遮蔽并强制 L2。")
            else:
                strategy, mask_level = "partial", "L2"
                reasons.append("未识别出明确高危字段，按默认方案：部分遮蔽并强制 L2。")
            retention = _default_retention(strategy)

        # 用途修正：对外发布绝不允许 partial；分析用途 partial 会破坏数值形态
        if _contains_any(prompt, _PUBLIC_PROMPT_WORDS):
            if strategy != "block":
                strategy = "block"
                retention = _default_retention(strategy)
            mask_level = "L3"
            reasons.append("用途涉及对外发布/演示，改为直接屏蔽并提升到 L3，避免任何原文外泄。")
        elif _contains_any(prompt, _ANALYSIS_PROMPT_WORDS) and strategy == "partial":
            strategy = "hash"
            retention = _default_retention(strategy)
            reasons.append("用途为分析/统计，部分遮蔽会破坏数值形态，改用加盐哈希。")

        # 高危计数修正：已检测到 DANGER 且字段含身份/联系方式时，强制级别至少 L2
        if danger > 0 and (kinds["has_id"] or kinds["has_contact"]) and _LEVEL_RANK[mask_level] < 2:
            mask_level = "L2"
            reasons.append(f"已检测到 {danger} 条高危命中且字段含身份/联系方式，强制级别至少 L2。")

        enable_l2 = mask_level in ("L2", "L3")
        enable_l3 = mask_level == "L3"
        enable_ner = kinds["has_name"] or (enable_l3 and content_type in ("text", "pdf", "image"))
        if scenario is not None:
            # 场景模板显式开启的能力保留（例如金融场景要求 NER、发布场景要求 L3）
            enable_ner = enable_ner or scenario.enable_ner
            enable_l3 = enable_l3 or scenario.enable_l3

        rationale = "".join(reasons[:2]) or "按默认方案：部分遮蔽并强制 L2。"
        return {
            "strategy": strategy,
            "mask_level": mask_level,
            "min_level": min_level,
            "enable_l2": enable_l2,
            "enable_l3": enable_l3,
            "enable_ner": enable_ner,
            "retention": retention,
            "rationale": rationale,
            "source": SOURCE_RULES,
        }

    # -- 规则引擎：研判 ---------------------------------------------------

    def _rule_advise(self, payload: dict) -> dict:
        """规则版风险研判：只根据聚合数字写可执行建议。"""
        by_level = _normalize_level_counts(payload.get("by_level"))
        by_type = self._type_counts(payload.get("by_type"))
        total = _to_int(payload.get("total_findings"))
        if total <= 0:
            total = sum(by_level.values()) or sum(count for _, count in by_type)
        tasks = _to_int(payload.get("tasks"))
        scenario = get_scenario(payload.get("scenario")) if isinstance(payload.get("scenario"), str) else None
        danger = _danger_count(by_level)
        safe = by_level.get("SAFE", 0)
        # 看板可传范围前缀、告警积压与已处理比例；缺省时保持旧行为
        scope = str(payload.get("scope") or "").strip()
        total_alerts = _to_int(payload.get("total_alerts"))
        open_alerts = _to_int(payload.get("open_alerts"))
        open_by_level = _normalize_level_counts(payload.get("open_by_level"))
        try:
            handled_ratio: float | None = (
                None if payload.get("handled_ratio") is None else float(payload.get("handled_ratio"))
            )
        except (TypeError, ValueError):
            handled_ratio = None
        has_alert_info = bool(total_alerts or open_alerts or handled_ratio is not None)

        def percent(part: int) -> int:
            return int(round(part * 100.0 / total)) if total > 0 else 0

        top = by_type[0] if by_type else None
        top_name = self._type_label(top[0]) if top else ""
        # 最高等级：只看 L1~L3，SAFE 不参与风险结论
        top_level = next((level for level in ("L3", "L2", "L1") if by_level.get(level, 0) > 0), "")
        top_level_count = by_level.get(top_level, 0) if top_level else 0

        summary_parts = [f"{scope}共 {total} 条检测结果" if scope else f"共 {total} 条检测结果"]
        if total:
            summary_parts.append(f"其中高危 {danger} 条（{percent(danger)}%）、安全 {safe} 条（{percent(safe)}%）")
        if tasks:
            # tasks 是「筛选范围内有检测记录的任务数」，不是待处理任务数
            summary_parts.append(f"共覆盖 {tasks} 个任务")
        if total_alerts:
            summary_parts.append(f"告警 {total_alerts} 条、其中 {open_alerts} 条待处理")
        if scenario is not None:
            summary_parts.append(f"业务场景「{scenario.label}」")
        summary = "，".join(summary_parts) + "。"

        findings: list[str] = []
        if total and top_level:
            findings.append(
                f"最高等级 {top_level} 命中 {top_level_count} 条，占全部检测结果的 {percent(top_level_count)}%。"
            )
        elif total and danger:
            findings.append(f"最高等级 L3 命中 {danger} 条，占全部检测结果的 {percent(danger)}%。")
        if top is not None and top_name:
            findings.append(
                f"最集中的敏感类型是{top_name}（{top[1]} 条，占 {percent(top[1])}%），建议优先复核。"
            )
        if open_alerts:
            detail = "、".join(
                f"{level} {open_by_level.get(level, 0)} 条"
                for level in ("L3", "L2", "L1")
                if open_by_level.get(level, 0)
            )
            findings.append(f"待处理告警 {open_alerts} 条" + (f"（{detail}）" if detail else "") + "，需尽快进入处置队列。")
        if handled_ratio is not None:
            findings.append(
                f"已处理告警占比 {handled_ratio:.0%}：已认领≠已完成，认领后仍需跟踪实际处置结果。"
            )
        if not findings:
            findings.append("整体风险平稳：当前聚合数据里没有明显的高危或未处理项。")
        findings = findings[:4]

        recommendations: list[str] = []
        if danger:
            recommendations.append(
                f"L3 高危共 {danger} 条（占 {percent(danger)}%），建议优先处置，导出时强制 L3 脱敏。"
            )
        # 按命中类型给具体动作：凭据/身份/账户类整段屏蔽，联系方式类保留首字符掩码
        for type_key, count in by_type[:2]:
            recommendations.append(self._type_action(type_key, self._type_label(type_key)))
        if has_alert_info:
            recommendations.append(
                f"当前有 {open_alerts} 条待处理告警，建议进入处置队列按级别优先处理高危项。"
            )
        if tasks:
            recommendations.append(f"建议对 {tasks} 个任务逐条复核，并在任务里保留审计留痕。")
        if total and safe * 100 >= total * 50:
            recommendations.append("建议按密级为 SAFE 的数据批量导出时使用 hash 策略，保留统计与建模可用性。")
        if scenario is not None:
            recommendations.append(
                f"建议沿用场景「{scenario.label}」参数：{scenario.strategy} 策略、强制 {scenario.mask_level} 脱敏。"
            )
        if not recommendations:
            recommendations.append("建议保持当前策略并定期巡检，出现高危聚集时再提升脱敏强度。")
        # 有告警积压信息时最多 4 条；旧调用方（无告警字段）仍保持最多 3 条
        recommendations = recommendations[: 4 if has_alert_info else 3]

        return {"summary": summary, "findings": findings, "recommendations": recommendations, "source": SOURCE_RULES}

    @staticmethod
    def _type_action(type_key: str, type_name: str) -> str:
        """按敏感类型给出脱敏动作（凭据类屏蔽并轮换，联系方式类首字符掩码）。"""
        key = str(type_key or "").strip().lower()
        name = type_name or key or "敏感"
        if key in {"id_card", "idcard", "bank_card", "secret", "token", "api_key", "password", "medical"}:
            action = f"建议对{name}类命中整段屏蔽（block），不保留任何片段"
            if key in {"secret", "token", "api_key", "password"}:
                action += "，并立即轮换相关凭据。"
            else:
                action += "，导出时强制 L3 脱敏。"
            return action
        if key in {"phone", "mobile", "email", "name", "address", "ip"}:
            return f"建议对{name}类命中保留首字符掩码（partial），其余字符按固定长度替换。"
        return f"建议对{name}类命中按密级复核，导出时按密级强制脱敏。"

    def _type_counts(self, by_type: object) -> list[tuple[str, int]]:
        """把 by_type 归一化成 [(类别, 计数)]，按计数降序、类别升序。"""
        counts: dict[str, int] = {}
        if not isinstance(by_type, (list, tuple)):
            return []
        for item in by_type:
            if not isinstance(item, dict):
                continue
            name = ""
            for key in ("type", "name", "key", "label", "category"):
                value = item.get(key)
                if value:
                    name = str(value).strip().lower()
                    break
            count = 0
            for key in ("count", "total", "n", "value", "hits"):
                if key in item:
                    count = _to_int(item.get(key))
                    break
            if name and count > 0:
                counts[name] = counts.get(name, 0) + count
        return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))

    @staticmethod
    def _type_label(type_key: str) -> str:
        """类别 key 转中文展示名；未知类别原样展示。"""
        return _TYPE_LABELS.get(str(type_key).strip().lower(), str(type_key).strip())

    # -- 本机模型调用 -----------------------------------------------------

    def tags_url(self) -> str:
        """从 /api/generate 端点推导 /api/tags（只换路径、保留 scheme+host+port）。"""
        parsed = urllib.parse.urlsplit(self.endpoint or "")
        if not parsed.scheme or not parsed.netloc:
            return ""
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/api/tags", "", ""))

    def _generate(self, prompt: str) -> str:
        """调用本机 Ollama 生成文本（非流式）。除 prompt 外不发送任何用户数据。"""
        body = json.dumps(
            {"model": self.model, "prompt": prompt, "stream": False, "format": "json"},
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("本机模型返回格式不正确")
        return str(payload.get("response") or "")

    def _fetch_tags(self, timeout_s: float) -> list[str] | None:
        """GET /api/tags；不可达或返回不合法一律 None（调用方据此提示降级）。"""
        url = self.tags_url()
        if not url:
            return None
        try:
            with urllib.request.urlopen(url, timeout=timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return None
        if not isinstance(payload, dict):
            return None
        items = payload.get("models")
        if not isinstance(items, list):
            return None
        names: list[str] = []
        for item in items:
            name = item.get("name") if isinstance(item, dict) else item
            text = str(name or "").strip()
            if text:
                names.append(text)
        return names

    def _build_suggest_prompt(self, payload: dict) -> str:
        """拼装建议 prompt：只放字段名/计数/用途，明确禁止复述敏感内容。"""
        scenario = get_scenario(payload.get("scenario")) if isinstance(payload.get("scenario"), str) else None
        fields = _normalize_fields(payload.get("fields"))[:32]
        level_counts = _normalize_level_counts(payload.get("level_counts"))
        usage = str(payload.get("prompt") or "").strip()[:200]
        content_type = str(payload.get("content_type") or "text").strip().lower()
        text_length = _to_int(payload.get("text_length"))
        return (
            "你是数据脱敏顾问。只根据下面的字段名、统计数字和用途给出一个脱敏方案；"
            "不要猜测或复述任何敏感内容。严格只输出一行 JSON（不要 markdown 代码块），字段必须是：\n"
            '{"strategy": "block|partial|hash|generalize|pseudonym|encrypt", '
            '"mask_level": "L3|L2|L1", "min_level": "SAFE|MEDIUM|HIGH", '
            '"enable_l2": true, "enable_l3": true, "enable_ner": true, '
            '"retention": "中文一句话", "rationale": "中文一句话"}\n'
            f"场景：{scenario.key if scenario else '未命中'}\n"
            f"数据形态：{content_type}\n"
            f"字段名（仅名称）：{', '.join(fields) or '无'}\n"
            f"级别计数（L3/L2/L1/SAFE）：{json.dumps(level_counts, ensure_ascii=False)}\n"
            f"文本长度：{text_length}\n"
            f"用途说明：{usage or '未提供'}"
        )

    def _build_advise_prompt(self, payload: dict) -> str:
        """拼装研判 prompt：只放聚合统计，不放任何原文。"""
        by_level = _normalize_level_counts(payload.get("by_level"))
        by_type = self._type_counts(payload.get("by_type"))[:8]
        total = _to_int(payload.get("total_findings")) or sum(by_level.values())
        tasks = _to_int(payload.get("tasks"))
        total_alerts = _to_int(payload.get("total_alerts"))
        open_alerts = _to_int(payload.get("open_alerts"))
        handled_ratio = payload.get("handled_ratio")
        scope = str(payload.get("scope") or "").strip()
        scenario = get_scenario(payload.get("scenario")) if isinstance(payload.get("scenario"), str) else None
        return (
            "你是安全运营分析员。根据下面的聚合统计（不包含任何原文）写风险研判，"
            "严格只输出一行 JSON（不要 markdown 代码块），字段必须是：\n"
            '{"summary": "中文一句话总览", "findings": ["1~3 条中文发现"], '
            '"recommendations": ["1~4 条中文可执行建议"]}\n'
            f"统计范围：{scope or '全部'}\n"
            f"级别计数：{json.dumps(by_level, ensure_ascii=False)}\n"
            f"类别命中：{json.dumps({name: count for name, count in by_type}, ensure_ascii=False)}\n"
            f"检测总数：{total}\n共覆盖任务：{tasks}\n"
            f"告警总数：{total_alerts}\n待处理告警：{open_alerts}\n"
            f"已处理比例：{'未知' if handled_ratio is None else handled_ratio}\n"
            f"业务场景：{scenario.label if scenario else '未指定'}"
        )
