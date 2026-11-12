"""审计哈希链测试：正常追加、可校验、被篡改时能定位、导出（含游标全量分页）。"""

from __future__ import annotations

import json

from app.audit import GENESIS, append_entry, canonical_hash, entry_hash_of, list_entries, verify_chain


def _append_three(db) -> None:
    for index in range(3):
        append_entry(
            db,
            task_id=f"task{index}",
            input_hash=canonical_hash({"i": index}),
            output_hash=canonical_hash({"o": index}),
            model_chain="l1",
            confidence_vector="[]",
            rule_id="idcard18",
        )


def _append_many(db, count: int) -> None:
    for index in range(count):
        append_entry(
            db,
            task_id=f"task{index}",
            input_hash=canonical_hash({"i": index}),
            output_hash=canonical_hash({"o": index}),
            model_chain="l1",
            confidence_vector="[]",
            rule_id="idcard18",
        )


def _get_db():
    from app.core.db import get_database

    return get_database()


def test_append_and_verify(database) -> None:
    _append_three(database)
    result = verify_chain(database)
    assert result["ok"] is True
    assert result["total"] == 3 and result["checked"] == 3
    assert result["head_hash"] != GENESIS

    rows, total = list_entries(database)
    assert total == 3
    # 倒序返回：最新一条在最前
    assert rows[0]["task_id"] == "task2"
    assert rows[-1]["prev_hash"] == GENESIS


def test_tampered_field_breaks_signature(database) -> None:
    _append_three(database)
    with database.write() as conn:
        conn.execute("UPDATE audit_log SET operator_id='hacker' WHERE task_id='task1'")
    result = verify_chain(database)
    assert result["ok"] is False
    assert result["broken_at"] is not None
    assert "签名" in result["reason"]


def test_deleted_row_breaks_chain(database) -> None:
    _append_three(database)
    with database.write() as conn:
        conn.execute("DELETE FROM audit_log WHERE task_id='task1'")
    result = verify_chain(database)
    assert result["ok"] is False
    assert "prev_hash" in result["reason"]


def test_wrong_secret_fails(database) -> None:
    _append_three(database)
    assert verify_chain(database, secret="另一个密钥")["ok"] is False


def test_empty_chain_is_ok(database) -> None:
    result = verify_chain(database)
    assert result["ok"] is True and result["total"] == 0 and result["head_hash"] == GENESIS


def test_export_default_is_latest_slice_ascending(client) -> None:
    """不传游标时保持旧行为：最近 N 条（默认 200），按 id 正序。"""
    _append_three(_get_db())
    response = client.get("/api/v1/audit/export?format=jsonl")
    assert response.status_code == 200
    rows = [json.loads(line) for line in response.text.splitlines()]
    assert [row["task_id"] for row in rows] == ["task0", "task1", "task2"]
    assert response.headers["X-FlowMask-Audit-Total"] == "3"
    assert response.headers["X-FlowMask-Audit-Returned"] == "3"
    assert "X-FlowMask-Audit-Next-After-Id" not in response.headers
    # 链尾锚点：调用方可另存，用于事后发现「尾部整段被删」
    assert response.headers["X-FlowMask-Audit-Head"] == entry_hash_of(rows[-1])


def test_export_cursor_pages_through_whole_chain(client) -> None:
    """after_id 从 0 开始逐页正序导出，响应头给出下一页游标。"""
    _append_many(_get_db(), 5)
    first = client.get("/api/v1/audit/export?format=jsonl&after_id=0&limit=2")
    assert [json.loads(line)["task_id"] for line in first.text.splitlines()] == ["task0", "task1"]
    cursor = first.headers["X-FlowMask-Audit-Next-After-Id"]
    assert cursor.isdigit()

    second = client.get(f"/api/v1/audit/export?format=jsonl&after_id={cursor}&limit=2")
    assert [json.loads(line)["task_id"] for line in second.text.splitlines()] == ["task2", "task3"]
    cursor = second.headers["X-FlowMask-Audit-Next-After-Id"]

    third = client.get(f"/api/v1/audit/export?format=jsonl&after_id={cursor}&limit=2")
    assert [json.loads(line)["task_id"] for line in third.text.splitlines()] == ["task4"]
    assert "X-FlowMask-Audit-Next-After-Id" not in third.headers

    # 游标越过链尾：空页、不再有下一页
    empty = client.get(f"/api/v1/audit/export?format=jsonl&after_id={int(cursor) + 10}")
    assert empty.status_code == 200
    assert empty.text == ""
    assert empty.headers["X-FlowMask-Audit-Returned"] == "0"
    assert empty.headers["X-FlowMask-Audit-Head"] == GENESIS


def test_export_json_reports_cursor_meta(client) -> None:
    _append_three(_get_db())
    response = client.get("/api/v1/audit/export?format=json&after_id=0&limit=2")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["after_id"] == 0
    assert body["returned"] == 2
    assert isinstance(body["next_after_id"], int)
    assert [item["task_id"] for item in body["items"]] == ["task0", "task1"]


def test_export_cursor_accepts_after_id_zero(client) -> None:
    """after_id=0 是合法游标（从链头开始），不能被当成「没传」。"""
    _append_three(_get_db())
    response = client.get("/api/v1/audit/export?format=jsonl&after_id=0&limit=200")
    rows = [json.loads(line) for line in response.text.splitlines()]
    assert [row["task_id"] for row in rows] == ["task0", "task1", "task2"]


def test_export_rejects_bad_limit_and_cursor(client) -> None:
    assert client.get("/api/v1/audit/export?limit=0").status_code == 422
    assert client.get("/api/v1/audit/export?limit=5001").status_code == 422
    assert client.get("/api/v1/audit/export?after_id=-1").status_code == 422


def test_full_export_via_cursor_verifies_offline(client) -> None:
    """分页拼出的完整链可直接离线验签（等价于脚本读文件）。"""
    from app.core.config import get_settings
    from scripts.verify_export import verify_rows

    _append_many(_get_db(), 4)
    rows: list[dict] = []
    after = 0
    for _ in range(10):  # 防御性上限，避免逻辑错误时死循环
        response = client.get(f"/api/v1/audit/export?format=jsonl&after_id={after}&limit=2")
        rows.extend(json.loads(line) for line in response.text.splitlines())
        cursor = response.headers.get("X-FlowMask-Audit-Next-After-Id")
        if cursor is None:
            break
        after = int(cursor)
    assert [row["task_id"] for row in rows] == [f"task{i}" for i in range(4)]
    result = verify_rows(rows, get_settings().audit_secret, require_genesis=True)
    assert result["ok"] is True
    assert result["checked"] == 4
    assert result["partial"] is False


def _wait_task_finished(client, task_id: str, timeout: float = 8.0) -> dict:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        task = client.get(f"/api/v1/tasks/{task_id}").json()
        if task["status"] in ("done", "failed"):
            return task
        time.sleep(0.05)
    raise AssertionError(f"任务 {task_id} 在 {timeout}s 内未结束")


def test_audit_failure_rolls_back_findings_and_alerts(client, monkeypatch) -> None:
    """审计写入失败时，findings/告警必须一起回滚，任务标记 failed 而不是半状态。"""
    from app.tasks import service as task_service

    def boom(*args, **kwargs):
        raise RuntimeError("audit boom")

    monkeypatch.setattr(task_service, "append_entry_conn", boom)
    created = client.post("/api/v1/tasks/text", json={"text": "手机号 13800138000"}).json()
    task = _wait_task_finished(client, created["task_id"])

    assert task["status"] == "failed"
    assert task["error"].startswith('TASK_PERSISTENCE_FAILED:')
    assert "audit boom" not in task["error"]  # 原始异常可能包含私密载荷
    assert client.get(f"/api/v1/tasks/{created['task_id']}/findings").json()["total"] == 0
    assert client.get("/api/v1/audit").json()["total"] == 0
    assert client.get("/api/v1/alerts").json()["total"] == 0


def test_prefix_deletion_with_rewritten_genesis_is_detected(database) -> None:
    """删掉链头几条、把新首条 prev_hash 改成 GENESIS 并重签名，也要被首条 id 检查拦住。"""
    from app.audit.chain import GENESIS, entry_hash_of, sign
    from app.core.config import get_settings

    _append_three(database)
    with database.write() as conn:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id ASC").fetchall()
        first_remaining = dict(rows[1])
        digest = entry_hash_of(first_remaining)
        conn.execute("DELETE FROM audit_log WHERE id=?", (rows[0]["id"],))
        conn.execute(
            "UPDATE audit_log SET prev_hash=?, signature=? WHERE id=?",
            (
                GENESIS,
                sign(get_settings().audit_secret, f"{GENESIS}:{digest}"),
                first_remaining["id"],
            ),
        )

    result = verify_chain(database)
    assert result["ok"] is False
    assert "首条记录 id" in result["reason"]


def test_legacy_v1_signature_is_accepted_with_warning(database) -> None:
    """升级前写入的旧记录（签名不含 prev_hash）仍可校验，并计入 legacy_rows。"""
    from app.audit.chain import GENESIS, HASH_FIELDS, canonical_hash, sign
    from app.core.config import get_settings
    from app.core.db import utcnow_iso

    payload = {
        "task_id": "legacy-task",
        "input_hash": canonical_hash({"i": "legacy"}),
        "output_hash": canonical_hash({"o": "legacy"}),
        "model_chain": "l1",
        "confidence_vector": "[]",
        "rule_id": "idcard18",
        "operator_id": "system",
        "user_id": "local",
        "ts": utcnow_iso(),
    }
    digest = canonical_hash({name: payload[name] for name in HASH_FIELDS})
    legacy_signature = sign(get_settings().audit_secret, digest)
    with database.write() as conn:
        conn.execute(
            """
            INSERT INTO audit_log
              (ts, task_id, input_hash, output_hash, model_chain, confidence_vector,
               rule_id, operator_id, user_id, prev_hash, signature)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                payload["ts"],
                payload["task_id"],
                payload["input_hash"],
                payload["output_hash"],
                payload["model_chain"],
                payload["confidence_vector"],
                payload["rule_id"],
                payload["operator_id"],
                payload["user_id"],
                GENESIS,
                legacy_signature,
            ),
        )

    result = verify_chain(database)
    assert result["ok"] is True
    assert result["legacy_rows"] == 1


def test_audit_csv_escapes_formula_injection(client, database) -> None:
    """operator_id/user_id 来自请求头，CSV 导出必须加前导单引号防公式执行。"""
    append_entry(
        database,
        task_id="formula",
        input_hash=canonical_hash({"i": "formula"}),
        output_hash=canonical_hash({"o": "formula"}),
        model_chain="l1",
        confidence_vector="[]",
        rule_id="idcard18",
        operator_id="=cmd|' /C calc'!A0",
        user_id="+1",
    )
    response = client.get("/api/v1/audit/export?format=csv")
    assert response.status_code == 200
    body = response.content.decode("utf-8-sig")
    assert "'=cmd" in body, "以 = 开头的单元格应被转义"
    assert "'+1" in body, "以 + 开头的单元格应被转义"
