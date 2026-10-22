"""用户偏好（主题）接口：默认值、多用户隔离、非法值、以及服务端默认主题配置。"""

from __future__ import annotations

import pytest


def _headers(operator: str) -> dict[str, str]:
    return {"X-Operator-Id": operator}


def _set_default_theme(monkeypatch, value: str) -> None:
    """改服务端默认主题：改环境变量后清掉 Settings 缓存，请求时会重新读。"""
    from app.core.config import get_settings

    monkeypatch.setenv("FLOWMASK_DEFAULT_THEME", value)
    get_settings.cache_clear()


def test_defaults_follow_server_setting(client, monkeypatch):
    """没人选过时 theme 为 null，default_theme 来自 FLOWMASK_DEFAULT_THEME。"""
    _set_default_theme(monkeypatch, "dark")
    resp = client.get("/api/v1/preferences")
    assert resp.status_code == 200
    body = resp.json()
    assert body["theme"] is None
    assert body["default_theme"] == "dark"
    assert body["available_themes"] == ["light", "dark", "system"]


def test_bad_server_default_falls_back_to_system(client, monkeypatch):
    _set_default_theme(monkeypatch, "彩虹")
    assert client.get("/api/v1/preferences").json()["default_theme"] == "system"


def test_save_and_read_theme(client):
    resp = client.put("/api/v1/preferences", json={"theme": "dark"})
    assert resp.status_code == 200
    assert resp.json()["theme"] == "dark"
    assert client.get("/api/v1/preferences").json()["theme"] == "dark"

    # 大写/带空格也接受，统一存小写
    assert client.put("/api/v1/preferences", json={"theme": " LIGHT "}).json()["theme"] == "light"
    assert client.get("/api/v1/preferences").json()["theme"] == "light"


def test_invalid_theme_rejected(client):
    resp = client.put("/api/v1/preferences", json={"theme": "rainbow"})
    assert resp.status_code == 400
    assert "light" in resp.json()["detail"] and "dark" in resp.json()["detail"]


def test_preferences_are_per_operator(client):
    client.put("/api/v1/preferences", json={"theme": "dark"}, headers=_headers("alice"))
    client.put("/api/v1/preferences", json={"theme": "light"}, headers=_headers("bob"))

    assert client.get("/api/v1/preferences", headers=_headers("alice")).json()["theme"] == "dark"
    assert client.get("/api/v1/preferences", headers=_headers("bob")).json()["theme"] == "light"
    # 没有选过的操作人仍然返回 null，不会串到别人的偏好
    assert client.get("/api/v1/preferences", headers=_headers("carol")).json()["theme"] is None


def test_list_users_and_delete(client):
    client.put("/api/v1/preferences", json={"theme": "dark"}, headers=_headers("alice"))
    client.put("/api/v1/preferences", json={"theme": "system"}, headers=_headers("bob"))

    listing = client.get("/api/v1/preferences/users").json()
    operators = {item["operator_id"]: item["theme"] for item in listing["users"]}
    assert operators["alice"] == "dark"
    assert operators["bob"] == "system"
    assert listing["total"] == len(listing["users"])
    assert listing["users"][0]["updated_at"]

    deleted = client.delete("/api/v1/preferences", headers=_headers("alice")).json()
    assert deleted["deleted"] is True
    assert client.get("/api/v1/preferences", headers=_headers("alice")).json()["theme"] is None
    # 重复删除不算错误，只是 deleted=false
    assert client.delete("/api/v1/preferences", headers=_headers("alice")).json()["deleted"] is False


def test_list_users_respects_limit(client):
    for index in range(5):
        client.put("/api/v1/preferences", json={"theme": "dark"}, headers=_headers(f"u{index}"))
    assert len(client.get("/api/v1/preferences/users?limit=2").json()["users"]) <= 2


@pytest.mark.parametrize("theme", ["light", "dark", "system"])
def test_all_available_themes_roundtrip(client, theme):
    assert client.put("/api/v1/preferences", json={"theme": theme}).json()["theme"] == theme
