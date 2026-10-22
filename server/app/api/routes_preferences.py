"""用户偏好接口：把主题选择存到服务端，支持多用户各自记住自己的主题。

设计取舍：
- 不引账号体系。本作品是一体机演示，身份用 ``X-Operator-Id`` 头表达（前端可在顶栏切换演示多用户）；
- 个人偏好优先于服务端默认值（``FLOWMASK_DEFAULT_THEME``），默认值只在用户没选过时生效；
- 只存 key/value 两列，后续要加别的偏好（密级过滤、默认算子）不用再改表结构。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..core.config import THEMES, Settings
from ..core.db import Database, utcnow_iso
from .deps import Operator, db_dep, operator_dep, settings_dep
from .schemas import (
    PreferenceIn,
    PreferencesOut,
    PreferenceUserOut,
    PreferenceUsersOut,
)

router = APIRouter(prefix="/preferences", tags=["preferences"])

THEME_KEY = "theme"


def _read_theme(db: Database, operator_id: str) -> str | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT value FROM user_prefs WHERE operator_id = ? AND key = ?",
            (operator_id, THEME_KEY),
        ).fetchone()
    return row["value"] if row else None


def _write_theme(db: Database, operator_id: str, theme: str) -> None:
    with db.write() as conn:
        conn.execute(
            """
            INSERT INTO user_prefs (operator_id, key, value, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(operator_id, key)
            DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (operator_id, THEME_KEY, theme, utcnow_iso()),
        )


def _out(settings: Settings, operator: Operator, theme: str | None) -> PreferencesOut:
    return PreferencesOut(
        operator_id=operator.operator_id,
        theme=theme,
        default_theme=settings.default_theme,
        available_themes=list(THEMES),
    )


@router.get("", response_model=PreferencesOut, summary="读取当前操作人的偏好")
def get_preferences(
    settings: Settings = Depends(settings_dep),
    db: Database = Depends(db_dep),
    operator: Operator = Depends(operator_dep),
) -> PreferencesOut:
    """``theme`` 为 null 表示该操作人没选过，前端应回落到 ``default_theme``。"""
    return _out(settings, operator, _read_theme(db, operator.operator_id))


@router.put("", response_model=PreferencesOut, summary="保存当前操作人的主题偏好")
def put_preferences(
    payload: PreferenceIn,
    settings: Settings = Depends(settings_dep),
    db: Database = Depends(db_dep),
    operator: Operator = Depends(operator_dep),
) -> PreferencesOut:
    theme = payload.theme.strip().lower()
    if theme not in THEMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"theme 仅支持 {' / '.join(THEMES)}",
        )
    _write_theme(db, operator.operator_id, theme)
    return _out(settings, operator, theme)


@router.delete("", summary="清除当前操作人的主题偏好")
def delete_preferences(
    db: Database = Depends(db_dep),
    operator: Operator = Depends(operator_dep),
) -> dict[str, object]:
    with db.write() as conn:
        cursor = conn.execute(
            "DELETE FROM user_prefs WHERE operator_id = ? AND key = ?",
            (operator.operator_id, THEME_KEY),
        )
    return {"operator_id": operator.operator_id, "deleted": cursor.rowcount > 0}


@router.get("/users", response_model=PreferenceUsersOut, summary="列出已保存偏好的用户")
def list_preference_users(
    limit: int = Query(default=50, ge=1, le=200),
    db: Database = Depends(db_dep),
) -> PreferenceUsersOut:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT operator_id, value, updated_at FROM user_prefs
            WHERE key = ? ORDER BY updated_at DESC LIMIT ?
            """,
            (THEME_KEY, limit),
        ).fetchall()
    users = [
        PreferenceUserOut(
            operator_id=row["operator_id"],
            theme=row["value"],
            updated_at=_iso(row["updated_at"]),
        )
        for row in rows
    ]
    return PreferenceUsersOut(users=users, total=len(users))


def _iso(value: str) -> datetime:
    """库里存的是 ISO8601 字符串，按契约转成 datetime 返回（前端直接可用）。"""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
