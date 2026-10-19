"""规则管理：12 条内置规则 + 自定义规则（增删改、启停）。

需求里的"支持自定义敏感数据类型"落在这里：新增一条正则规则（指定类别、权重、置信度），
保存后**立即生效**——检测链路每次 ``detect()`` 读的都是 ``active_rules()``。
停用内置规则也一样（在库里写一条同 id 的覆盖行）。
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, status

from ..core.db import Database, utcnow_iso
from ..identify import EntityType
from ..identify.rules import RULES, Rule, set_active_rules
from .deps import db_dep
from .schemas import RuleIn, RuleOut

router = APIRouter(tags=["rules"])

_BUILTIN_IDS = {rule.name for rule in RULES}


def _db_rows(db: Database) -> dict[str, dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM rules").fetchall()
    return {row["id"]: dict(row) for row in rows}


def sync_active_rules(db: Database) -> None:
    """把「内置规则的启停 + 自定义规则」编译成当前生效规则集。"""
    overrides = _db_rows(db)
    active: list[Rule] = []
    for rule in RULES:
        row = overrides.get(rule.name)
        if row is not None and not row["enabled"]:
            continue
        active.append(rule)
    for rule_id, row in overrides.items():
        if rule_id in _BUILTIN_IDS or not row["enabled"]:
            continue
        try:
            pattern = re.compile(row["pattern"])
        except re.error:
            continue  # 坏规则跳过：不能让一条脏数据拖垮整条检测链路
        active.append(
            Rule(
                name=rule_id,
                type=EntityType(row["type"]),
                pattern=pattern,
                weight=float(row["weight"]),
                confidence=float(row["confidence"]),
            )
        )
    set_active_rules(tuple(active))


def _builtin_out(rule: Rule, row: dict[str, Any] | None) -> RuleOut:
    return RuleOut(
        id=rule.name,
        name=rule.name,
        type=rule.type.value,
        pattern=rule.pattern.pattern,
        weight=rule.weight,
        confidence=rule.confidence,
        enabled=True if row is None else bool(row["enabled"]),
        updated_at=None if row is None else row["updated_at"],
        builtin=True,
    )


def _custom_out(row: dict[str, Any]) -> RuleOut:
    return RuleOut(
        id=row["id"],
        name=row["name"],
        type=row["type"],
        pattern=row["pattern"],
        weight=float(row["weight"]),
        confidence=float(row["confidence"]),
        enabled=bool(row["enabled"]),
        updated_at=row["updated_at"],
        builtin=False,
    )


def _upsert(db: Database, rule_id: str, name: str, type_: str, pattern: str, weight: float, confidence: float, enabled: bool) -> None:
    with db.write() as conn:
        conn.execute(
            """
            INSERT INTO rules (id, name, type, pattern, weight, confidence, enabled, updated_at)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              name=excluded.name, type=excluded.type, pattern=excluded.pattern,
              weight=excluded.weight, confidence=excluded.confidence,
              enabled=excluded.enabled, updated_at=excluded.updated_at
            """,
            (rule_id, name, type_, pattern, weight, confidence, 1 if enabled else 0, utcnow_iso()),
        )


@router.get("/rules", response_model=list[RuleOut], summary="规则列表（内置 + 自定义）")
def list_rules(db: Database = Depends(db_dep)) -> list[RuleOut]:
    overrides = _db_rows(db)
    items = [_builtin_out(rule, overrides.get(rule.name)) for rule in RULES]
    for rule_id, row in sorted(overrides.items()):
        if rule_id in _BUILTIN_IDS:
            continue
        items.append(_custom_out(row))
    return items


@router.post("/rules", response_model=RuleOut, status_code=status.HTTP_201_CREATED, summary="新增自定义规则")
def create_rule(payload: RuleIn, db: Database = Depends(db_dep)) -> RuleOut:
    if payload.id in _BUILTIN_IDS:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"规则 id 与内置规则重名: {payload.id}")
    _upsert(db, payload.id, payload.name, payload.type, payload.pattern, payload.weight, payload.confidence, payload.enabled)
    sync_active_rules(db)
    return _custom_out(_db_rows(db)[payload.id])


@router.patch("/rules/{rule_id}", response_model=RuleOut, summary="启用/停用规则")
def toggle_rule(rule_id: str, enabled: bool = Body(embed=True), db: Database = Depends(db_dep)) -> RuleOut:
    rows = _db_rows(db)
    if rule_id in _BUILTIN_IDS:
        rule = next(r for r in RULES if r.name == rule_id)
        _upsert(
            db,
            rule_id=rule.name,
            name=rule.name,
            type_=rule.type.value,
            pattern=rule.pattern.pattern,
            weight=rule.weight,
            confidence=rule.confidence,
            enabled=enabled,
        )
        sync_active_rules(db)
        return _builtin_out(rule, _db_rows(db).get(rule_id))

    row = rows.get(rule_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="规则不存在")
    _upsert(
        db,
        rule_id=row["id"],
        name=row["name"],
        type_=row["type"],
        pattern=row["pattern"],
        weight=float(row["weight"]),
        confidence=float(row["confidence"]),
        enabled=enabled,
    )
    sync_active_rules(db)
    return _custom_out(_db_rows(db)[rule_id])


@router.delete("/rules/{rule_id}", summary="删除自定义规则 / 恢复内置规则")
def delete_rule(rule_id: str, db: Database = Depends(db_dep)) -> dict[str, str]:
    with db.write() as conn:
        deleted = conn.execute("DELETE FROM rules WHERE id=?", (rule_id,)).rowcount
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="规则不存在")
    sync_active_rules(db)
    action = "恢复内置规则" if rule_id in _BUILTIN_IDS else "删除自定义规则"
    return {"id": rule_id, "action": action}
