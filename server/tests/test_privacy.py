"""隐私增强导出测试：Laplace 计数扰动、l-多样性校验、接口参数校验。"""

from __future__ import annotations

import json
import random
import time

import pytest

from app.identify import DetectOptions
from app.privacy import (
    DEFAULT_EPSILON,
    DEFAULT_L,
    export_with_privacy,
    summarize,
    validate_params,
)

ID_CARD = "11010519491231002X"
PHONE = "13800138000"


def _wait_done(client, task_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/v1/tasks/{task_id}").json()
        if body["status"] in {"done", "failed"}:
            return body
        time.sleep(0.05)
    raise AssertionError("任务在超时时间内未完成")


def test_validate_params_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        validate_params(0.0, 3)
    with pytest.raises(ValueError):
        validate_params(11.0, 3)
    with pytest.raises(ValueError):
        validate_params(1.0, 0)
    with pytest.raises(ValueError):
        validate_params(1.0, 21)
    assert validate_params(0.05, 1) == (0.05, 1)
    assert validate_params(DEFAULT_EPSILON, DEFAULT_L) == (DEFAULT_EPSILON, DEFAULT_L)


def test_summarize_counts_and_noisy_versions() -> None:
    rows = [
        {"level": "L3", "types": "id_card,phone"},
        {"level": "L3", "types": "id_card"},
        {"level": "L2", "types": "phone"},
        {"level": "SAFE", "types": ""},
    ]
    summary = summarize(rows, epsilon=1.0, l=2, rng=random.Random(42))
    assert summary.total == 4
    assert summary.by_level["L3"] == 2
    assert summary.by_level["SAFE"] == 1
    assert summary.by_type["id_card"] == 2 and summary.by_type["phone"] == 2
    # 加噪后仍是非负数；epsilon=1 时噪声尺度为 1，计数不会被放大得多离谱
    assert all(value >= 0 for value in summary.dp_by_level.values())
    assert all(value >= 0 for value in summary.dp_by_type.values())
    # 准标识符 = 类别组合，敏感属性 = 分级：L3 组有 id_card/phone 两种组合，l=2 满足
    assert summary.l_diversity["l"] == 2
    assert summary.l_diversity["groups"] >= 1


def test_summarize_flags_l_diversity_violation() -> None:
    rows = [{"level": "L3", "types": "id_card"} for _ in range(3)]
    summary = summarize(rows, epsilon=2.0, l=3, rng=random.Random(0))
    # 每个等价类里只有一种分级取值 → 必然违反 l=3
    assert summary.l_diversity["ok"] is False
    assert summary.l_diversity["violation_count"] >= 1


def test_export_with_privacy_jsonl_has_meta_and_rows(database) -> None:
    from app.core.db import get_database
    from app.tasks.service import create_task

    db = get_database()
    task_id = create_task(
        db,
        source="test:privacy",
        content_type="text",
        document=_document(f"客户张三，手机 {PHONE}，身份证 {ID_CARD}"),
        options=DetectOptions(),
    )
    content, media_type, filename = export_with_privacy(db, task_id, "jsonl", 1.0, 2, random.Random(7))
    lines = content.decode("utf-8").strip().splitlines()
    meta = json.loads(lines[0])
    assert meta["privacy"]["epsilon"] == 1.0
    assert meta["privacy"]["l"] == 2
    assert "privacy" in filename and filename.endswith(".jsonl")
    assert media_type.startswith("application/x-ndjson")
    # 明细里不能出现原文
    body = "\n".join(lines[1:])
    assert ID_CARD not in body and PHONE not in body


def test_export_with_privacy_rejects_csv(database) -> None:
    from app.core.db import get_database
    from app.tasks.service import create_task

    db = get_database()
    task_id = create_task(
        db, source="t", content_type="text", document=_document(f"身份证 {ID_CARD}"), options=DetectOptions()
    )
    with pytest.raises(ValueError):
        export_with_privacy(db, task_id, "csv", 1.0, 2)


def test_privacy_export_api(client) -> None:
    text = f"客户张三，手机 {PHONE}，身份证 {ID_CARD}"
    created = client.post("/api/v1/tasks/text", json={"text": text}).json()
    task_id = created["task_id"]
    assert _wait_done(client, task_id)["status"] == "done"

    response = client.get(f"/api/v1/tasks/{task_id}/export/privacy", params={"format": "json", "epsilon": 2.0, "l": 2})
    assert response.status_code == 200
    assert response.headers["x-flowmask-privacy"] == "laplace+l-diversity"
    payload = response.json()
    privacy = payload["meta"]["privacy"]
    assert privacy["epsilon"] == 2.0 and privacy["l"] == 2
    assert privacy["total"] >= 1
    assert privacy["by_level"]["L3"] >= 1
    assert privacy["l_diversity"]["l"] == 2

    jsonl = client.get(f"/api/v1/tasks/{task_id}/export/privacy", params={"format": "jsonl"})
    assert jsonl.status_code == 200
    assert jsonl.text.splitlines()[0].startswith("{")


def test_privacy_export_api_validation(client) -> None:
    created = client.post("/api/v1/tasks/text", json={"text": f"手机 {PHONE}"}).json()
    task_id = created["task_id"]
    _wait_done(client, task_id)

    assert client.get(f"/api/v1/tasks/{task_id}/export/privacy", params={"epsilon": 0}).status_code == 400
    assert client.get(f"/api/v1/tasks/{task_id}/export/privacy", params={"l": 0}).status_code == 400
    assert client.get(f"/api/v1/tasks/{task_id}/export/privacy", params={"format": "csv"}).status_code == 400
    assert client.get("/api/v1/tasks/nope/export/privacy").status_code == 404


def _document(text: str):  # noqa: ANN001 - 测试辅助
    from app.parsers.registry import parse_text_input

    return parse_text_input(text)
