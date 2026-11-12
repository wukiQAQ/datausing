"""看板筛选口径测试：/stats 与 /findings、/alerts 在同一筛选下必须数字一致。

造数优先走真实接口（``POST /api/v1/tasks/text``，检测流程同步完成），
既覆盖解析、落库、告警生成，也避免测试直接依赖数据库表结构。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

# 同时命中手机号、身份证号、密码，便于断言 level / types 筛选
SENSITIVE_TEXT = "联系人电话 13800138000，身份证号 11010519491231002X，登录密码 Abc123456789"
# 无敏感实体 → SAFE 段，用于验证 by_level 的 SAFE 与 alerts 传 SAFE 返回空集
SAFE_TEXT = "今天天气不错，适合去公园散步。"

VALID_TYPES = {"phone", "id_card", "bank_card", "email", "ip", "secret", "password", "name", "address", "medical"}


def _wait_done(client, task_id: str, timeout: float = 5.0) -> dict:
    """轮询任务状态直到 done/failed（与既有 test_api 的等待口径保持一致）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/v1/tasks/{task_id}").json()
        if body["status"] in {"done", "failed"}:
            assert body["status"] == "done"
            return body
        time.sleep(0.05)
    raise AssertionError("任务在超时时间内未完成")


def _create_text_task(client, text: str = SENSITIVE_TEXT) -> str:
    response = client.post("/api/v1/tasks/text", json={"text": text, "enable_l2": False})
    assert response.status_code == 200, response.text
    task_id = response.json()["task_id"]
    _wait_done(client, task_id)
    return task_id


def _stats(client, **params) -> dict:
    response = client.get("/api/v1/stats", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _findings(client, **params) -> dict:
    response = client.get("/api/v1/findings", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _alerts(client, **params) -> dict:
    response = client.get("/api/v1/alerts", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _alert_summary(client, **params) -> dict:
    response = client.get("/api/v1/alerts/summary", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_stats_keeps_legacy_fields_and_adds_dimensions(client) -> None:
    """旧字段口径不变，新字段（total_hits/by_day/risk/dimensions/filter）齐全。"""
    _create_text_task(client)
    _create_text_task(client, SAFE_TEXT)

    stats = _stats(client)

    # 旧字段：分级/直方图/类别/任务数
    assert set(stats["by_level"]) == {"L3", "L2", "L1", "SAFE"}
    assert sum(stats["by_level"].values()) == stats["total_findings"]
    assert set(stats["score_histogram"]) == {"0-0.25", "0.25-0.5", "0.5-0.75", "0.75-1.0"}
    assert sum(stats["score_histogram"].values()) == stats["total_findings"]
    assert stats["tasks_total"] >= 2
    assert stats["tasks"] >= 1
    assert stats["task_id"] is None
    # 识别流水线只落库"有命中"的段落（pipeline.py 对无命中段直接跳过），
    # 因此纯 SAFE 文本不会产生 findings 行，SAFE 分级计数保持 0。
    assert stats["by_level"]["SAFE"] == 0

    # 新字段：命中次数、类别中文名与分级分布
    assert stats["total_hits"] >= stats["total_findings"]
    assert stats["by_type"]
    for item in stats["by_type"]:
        assert set(item) >= {"type", "label", "count", "hits", "levels"}
        assert item["type"] in VALID_TYPES
        assert isinstance(item["label"], str) and item["label"]
        assert set(item["levels"]) == {"L3", "L2", "L1", "SAFE"}
        assert item["count"] <= stats["total_findings"]

    # 趋势：无时间筛选时限制最近 90 个当地日，并回显 trend_limited
    assert stats["by_day"]
    assert sum(day["total"] for day in stats["by_day"]) == stats["total_findings"]
    assert stats["filter"]["trend_limited"] is True
    assert stats["filter"]["types"] == []
    assert stats["filter"]["tz_offset"] == 0

    # 维度字典：全库、不受筛选影响
    assert len(stats["dimensions"]["types"]) == 10
    assert {entry["key"] for entry in stats["dimensions"]["types"]} == VALID_TYPES
    content_types = {entry["value"]: entry for entry in stats["dimensions"]["content_types"]}
    assert content_types["text"]["label"] == "文本"
    assert content_types["text"]["count"] >= 2
    assert len(stats["dimensions"]["tasks"]) <= 50
    assert [entry["value"] for entry in stats["dimensions"]["levels"]] == ["L3", "L2", "L1", "SAFE"]

    # 告警处置块：handled = ack + ignored，比例为 4 位小数
    risk = stats["risk"]
    assert set(risk) >= {
        "total",
        "open",
        "ack",
        "ignored",
        "handled",
        "handled_ratio",
        "by_level",
        "open_by_level",
        "by_day",
    }
    assert risk["total"] == risk["open"] + risk["ack"] + risk["ignored"]
    assert risk["handled"] == risk["ack"] + risk["ignored"]
    expected_ratio = round(risk["handled"] / risk["total"], 4) if risk["total"] else 0.0
    assert risk["handled_ratio"] == expected_ratio


def test_stats_task_id_scopes_kpis_but_not_dimensions(client) -> None:
    """task_id 只影响统计范围；dimensions 仍是全库字典。"""
    first = _create_text_task(client)
    _create_text_task(client, SAFE_TEXT)

    stats = _stats(client, task_id=first)
    findings = _findings(client, task_id=first)

    assert stats["task_id"] == first
    assert stats["tasks"] == 1
    assert stats["total_findings"] == findings["total"]
    assert stats["filter"]["task_id"] == first
    # 全库维度：两个任务都要出现在候选里
    assert len(stats["dimensions"]["tasks"]) >= 2
    # 旧语义 task_id 全库任务总数仍可通过 tasks_total 拿到
    assert stats["tasks_total"] >= 2


def test_findings_alerts_and_stats_share_filter_numbers(client) -> None:
    """同一筛选下 /stats、/findings、/alerts 与 /alerts/summary 数字一致。"""
    _create_text_task(client)
    _create_text_task(client, SAFE_TEXT)

    stats = _stats(client)
    findings_all = _findings(client, page_size=100)
    alerts_all = _alerts(client, page_size=100)
    summary_all = _alert_summary(client)

    assert stats["total_findings"] == findings_all["total"]
    assert stats["risk"]["total"] == alerts_all["total"] == summary_all["total"]
    # 告警只覆盖非 SAFE 段：alerts 总数 = 检测总数 - SAFE 段数
    assert stats["risk"]["total"] == stats["total_findings"] - stats["by_level"]["SAFE"]
    assert stats["risk"]["open"] == summary_all["open"]

    # 按类别筛选：三处口径一致
    type_key = stats["by_type"][0]["type"]
    type_stats = _stats(client, types=type_key)
    type_findings = _findings(client, types=type_key, page_size=100)
    type_alerts = _alerts(client, types=type_key, page_size=100)
    assert type_stats["total_findings"] == type_findings["total"] == type_alerts["total"]
    assert type_stats["total_findings"] == stats["by_type"][0]["count"]
    assert type_stats["filter"]["types"] == [type_key]

    # 按级别筛选：findings 与 alerts 的 level 语义完全一致（SAFE 除外）
    level_stats = _stats(client, level="L2")
    level_findings = _findings(client, level="L2", page_size=100)
    level_alerts = _alerts(client, level="L2", page_size=100)
    assert level_stats["total_findings"] == level_findings["total"] == level_alerts["total"]
    assert level_stats["total_findings"] == stats["by_level"]["L2"]
    assert level_stats["by_level"]["L3"] == 0

    # SAFE 段不落库（见 pipeline 对无命中段的跳过），三处都应为 0；
    # alerts 传 SAFE 返回空集而不是 400。
    safe_findings = _findings(client, level="SAFE", page_size=100)
    safe_stats = _stats(client, level="SAFE")
    assert safe_findings["total"] == stats["by_level"]["SAFE"] == 0
    assert safe_stats["total_findings"] == stats["by_level"]["SAFE"]
    assert safe_stats["risk"]["total"] == 0
    assert _alerts(client, level="SAFE")["total"] == 0
    assert _alert_summary(client, level="SAFE")["total"] == 0

    # 按内容形态筛选：全部是 text；不存在的形态返回 0
    text_stats = _stats(client, content_type="text")
    assert text_stats["total_findings"] == stats["total_findings"]
    assert _stats(client, content_type="json")["total_findings"] == 0
    assert _findings(client, content_type="json")["total"] == 0
    assert _alerts(client, content_type="json")["total"] == 0


def test_days_window_and_start_end_bounds(client) -> None:
    """days 相对窗口、start/end 开区间与 filter 回显。"""
    _create_text_task(client)
    now = datetime.now(timezone.utc)
    before = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    after = (now + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

    recent = _stats(client, days=1)
    assert recent["total_findings"] >= 1
    assert recent["filter"]["days"] == 1
    assert recent["filter"]["trend_limited"] is False

    inside = _stats(client, start=before, end=after)
    assert inside["total_findings"] >= 1
    assert inside["filter"]["start"].endswith("Z")
    assert inside["filter"]["end"].endswith("Z")

    # end 是开区间上界：只给早于数据的时间当 end，必须为空
    assert _stats(client, end=before)["total_findings"] == 0
    # start 晚于数据同样为空
    assert _stats(client, start=after)["total_findings"] == 0

    # 日期字符串也接受（当地时区补 00:00，end 加一天）
    today = (datetime.now(timezone.utc) + timedelta(minutes=480)).date().isoformat()
    day_stats = _stats(client, start=today, end=today, tz_offset=480)
    assert day_stats["total_findings"] >= 1
    assert day_stats["filter"]["tz_offset"] == 480


def test_by_day_uses_tz_offset(client) -> None:
    """by_day 的日期按 tz_offset 切当地日；UTC+14 与 UTC-14 必然落在不同日期。"""
    _create_text_task(client)

    east = _stats(client, days=2, tz_offset=840)
    west = _stats(client, days=2, tz_offset=-840)

    expected_east = (datetime.now(timezone.utc) + timedelta(minutes=840)).date().isoformat()
    assert expected_east in {day["date"] for day in east["by_day"]}
    assert sum(day["total"] for day in east["by_day"]) == east["total_findings"]
    assert sum(day["total"] for day in west["by_day"]) == west["total_findings"]
    # 偏移差 28 小时，两条记录必不在同一当地日期
    assert {day["date"] for day in east["by_day"]} != {day["date"] for day in west["by_day"]}


def test_risk_handled_ratio_matches_alert_summary(client) -> None:
    """认领一条告警后，/stats 的 risk 与 /alerts/summary 的处置口径一致。"""
    _create_text_task(client)

    opened = _alerts(client, status="open", page_size=100)
    assert opened["total"] >= 1
    acked = client.post(f"/api/v1/alerts/{opened['items'][0]['id']}/ack")
    assert acked.status_code == 200, acked.text
    assert acked.json()["status"] == "ack"

    stats = _stats(client)
    summary = _alert_summary(client)
    risk = stats["risk"]

    assert risk["ack"] == summary["by_status"]["ack"] >= 1
    assert risk["handled"] == summary["handled"] == risk["ack"] + risk["ignored"]
    assert risk["handled_ratio"] == summary["handled_ratio"]
    assert risk["total"] == summary["total"]
    assert summary["by_status"] == {key: risk[key] for key in ("open", "ack", "ignored")}
    assert set(summary["by_level"]) == {"L3", "L2", "L1"}
    assert set(risk["open_by_level"]) == {"L3", "L2", "L1"}
    assert sum(risk["open_by_level"].values()) == risk["open"]
    assert risk["by_day"]


def test_findings_and_alerts_carry_type_labels_source_content_type(client) -> None:
    """明细增量字段：type_labels（中文）、source、content_type（LEFT JOIN tasks）。"""
    _create_text_task(client)

    findings = _findings(client, page_size=100)
    assert findings["total"] >= 1
    item = findings["items"][0]
    assert isinstance(item["type_labels"], list) and item["type_labels"]
    assert set(item["type_labels"]) <= {
        "手机号",
        "身份证号",
        "银行卡号",
        "邮箱",
        "IP地址",
        "密钥凭据",
        "密码",
        "姓名",
        "地址",
        "医疗信息",
    }
    assert item["source"] == "text"
    assert item["content_type"] == "text"
    # 排序仍是 id 倒序
    ids = [row["id"] for row in findings["items"]]
    assert ids == sorted(ids, reverse=True)

    detail = client.get(f"/api/v1/findings/{item['id']}")
    assert detail.status_code == 200
    assert detail.json()["type_labels"] == item["type_labels"]
    assert detail.json()["source"] == "text"

    alerts = _alerts(client, page_size=100)
    assert alerts["total"] >= 1
    alert = alerts["items"][0]
    assert isinstance(alert["type_labels"], list) and alert["type_labels"]
    assert alert["source"] == "text"
    assert alert["content_type"] == "text"


def test_invalid_filters_return_400(client) -> None:
    """非法 level/types/tz_offset/days 统一 400；分页上限仍是 100。"""
    _create_text_task(client)

    bad_requests = [
        ("/api/v1/stats", {"level": "L9"}),
        ("/api/v1/stats", {"types": "not_a_type"}),
        ("/api/v1/stats", {"tz_offset": 999}),
        ("/api/v1/stats", {"days": 0}),
        ("/api/v1/findings", {"types": "not_a_type"}),
        ("/api/v1/findings", {"level": "L9"}),
        ("/api/v1/alerts", {"types": "not_a_type"}),
        ("/api/v1/alerts", {"level": "L9"}),
        ("/api/v1/alerts/summary", {"level": "L9"}),
    ]
    for path, params in bad_requests:
        response = client.get(path, params=params)
        assert response.status_code == 400, f"{path} {params} → {response.status_code}"

    unknown_type = client.get("/api/v1/stats", params={"types": "not_a_type"})
    assert "未知的敏感数据类型" in unknown_type.json()["detail"]

    assert client.get("/api/v1/findings", params={"page_size": 101}).status_code == 422
    assert client.get("/api/v1/findings", params={"page_size": 100}).status_code == 200


def test_assistant_advise_uses_open_alerts_and_handled_ratio(client) -> None:
    """助手接收告警积压字段后，文案覆盖「待处理」与「已认领≠已完成」。"""
    payload = {
        "scope": "近 30 天",
        "by_level": {"L3": 2, "L2": 3, "SAFE": 5},
        "by_type": [{"type": "phone", "count": 2}, {"type": "secret", "count": 1}],
        "total_findings": 10,
        "tasks": 2,
        "total_alerts": 5,
        "open_alerts": 3,
        "handled_ratio": 0.4,
        "open_by_level": {"L3": 2, "L2": 1},
    }
    response = client.post("/api/v1/assistant/advise", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["source"] == "rules"
    assert body["summary"].startswith("近 30 天")
    assert "待处理" in body["summary"]
    text = body["summary"] + "".join(body["findings"]) + "".join(body["recommendations"])
    assert "待处理" in text
    assert "已认领" in text
    assert len(body["recommendations"]) <= 4
    # 凭据类建议需要给出轮换动作
    assert "轮换" in text

    # 旧调用方不传新字段时仍可用，且建议不超过 3 条
    legacy = client.post(
        "/api/v1/assistant/advise",
        json={"by_level": {"L3": 2}, "by_type": [{"type": "phone", "count": 1}], "total_findings": 3},
    )
    assert legacy.status_code == 200
    legacy_body = legacy.json()
    assert 1 <= len(legacy_body["recommendations"]) <= 3
    assert "待处理" not in legacy_body["summary"]
