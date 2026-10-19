"""审计哈希链：防篡改的溯源记录。

每条记录保存"原文/脱敏副本的哈希 + 模型链路 + 置信度向量 + 操作人"，并用两种手段串起来：

1. ``prev_hash`` 指向上一条记录的 entry_hash（首条为 64 个 0），任何记录被删除/插入/改字段，
   后续记录的重算哈希都对不上；
2. ``signature = HMAC-SHA256(secret, entry_hash)``，没有密钥无法伪造一条链上记录。

entry_hash 的规范化口径固定：对固定字段做「字典序排序 + 紧凑分隔符」的 JSON，
UTF-8 编码后取 SHA-256。写库方与校验方用同一函数，因此可独立复算。

存储仍然只用标准库 sqlite3，不引第三方依赖——评审时能一句句讲清楚。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from ..core.config import get_settings
from ..core.db import Database, utcnow_iso

GENESIS = "0" * 64

# 参与哈希计算的字段（签名与 prev_hash 本身不参与，否则循环依赖）
HASH_FIELDS = (
    "task_id",
    "input_hash",
    "output_hash",
    "model_chain",
    "confidence_vector",
    "rule_id",
    "operator_id",
    "user_id",
    "ts",
)


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(payload: dict[str, Any]) -> str:
    """对规范化 JSON 求 SHA-256。"""
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def sign(secret: str, entry_hash: str) -> str:
    """HMAC-SHA256 签名；演示环境用配置里的 audit_secret，生产应放 KMS。"""
    return hmac.new(secret.encode("utf-8"), entry_hash.encode("utf-8"), hashlib.sha256).hexdigest()


def _payload_of(row: Any) -> dict[str, Any]:
    return {name: row[name] for name in HASH_FIELDS}


def entry_hash_of(row: Any) -> str:
    """由一行记录（Row / dict）重算 entry_hash，校验与写入共用。"""
    return canonical_hash(_payload_of(row))


def _head_hash(conn: Any) -> str:
    row = conn.execute("SELECT task_id, input_hash, output_hash, model_chain, confidence_vector, rule_id, operator_id, user_id, ts FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return GENESIS
    return entry_hash_of(row)


def append_entry(
    db: Database,
    *,
    task_id: str,
    input_hash: str,
    output_hash: str,
    model_chain: str,
    confidence_vector: str = "",
    rule_id: str = "",
    operator_id: str = "system",
    user_id: str = "local",
    ts: str | None = None,
) -> dict[str, Any]:
    """追加一条审计记录：读取链头哈希 → 计算本条哈希 → 签名 → 插入。"""
    settings = get_settings()
    payload = {
        # 统一转字符串：列在 SQLite 里是 TEXT，写入用 int、读回是 str 会导致校验哈希不一致
        "task_id": str(task_id),
        "input_hash": input_hash,
        "output_hash": output_hash,
        "model_chain": model_chain,
        "confidence_vector": confidence_vector,
        "rule_id": rule_id,
        "operator_id": operator_id,
        "user_id": user_id,
        "ts": ts or utcnow_iso(),
    }
    with db.write() as conn:
        payload["prev_hash"] = _head_hash(conn)
        # 注意：哈希口径只含 HASH_FIELDS，不含 prev_hash 本身（否则循环依赖）
        digest = canonical_hash({name: payload[name] for name in HASH_FIELDS})
        signature = sign(settings.audit_secret, digest)
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
                payload["prev_hash"],
                signature,
            ),
        )
        entry_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {
        "id": entry_id,
        "entry_hash": digest,
        "signature": signature,
        "prev_hash": payload["prev_hash"],
        **{k: payload[k] for k in HASH_FIELDS},
    }


def list_entries(db: Database, limit: int = 50, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    """倒序分页列出审计记录（不含签名密钥说明）。"""
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    with db.connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [dict(row) for row in rows], int(total)


def verify_chain(db: Database, secret: str | None = None) -> dict[str, Any]:
    """从最早一条开始逐条校验：prev_hash 链接 + 字段哈希 + HMAC 签名。"""
    key = get_settings().audit_secret if secret is None else secret
    expected_prev = GENESIS
    checked = 0
    head = GENESIS
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id ASC").fetchall()
    for row in rows:
        checked += 1
        if row["prev_hash"] != expected_prev:
            return {
                "ok": False,
                "total": len(rows),
                "checked": checked,
                "broken_at": row["id"],
                "reason": "prev_hash 与上一条记录的哈希不一致（记录被删除、插入或重排）",
                "head_hash": head,
            }
        digest = entry_hash_of(row)
        if not hmac.compare_digest(sign(key, digest), row["signature"]):
            return {
                "ok": False,
                "total": len(rows),
                "checked": checked,
                "broken_at": row["id"],
                "reason": "签名校验失败（记录字段被修改，或密钥不匹配）",
                "head_hash": head,
            }
        expected_prev = digest
        head = digest
    return {
        "ok": True,
        "total": len(rows),
        "checked": checked,
        "broken_at": None,
        "reason": "",
        "head_hash": head,
    }
