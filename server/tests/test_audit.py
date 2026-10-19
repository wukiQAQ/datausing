"""审计哈希链测试：正常追加、可校验、被篡改时能定位。"""

from __future__ import annotations

from app.audit import GENESIS, append_entry, canonical_hash, list_entries, verify_chain


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
