"""pytest 公共夹具：每个用例一个独立的临时数据目录与干净的全局状态。

这些模块在测试里多处被复用（识别层、存储层、接口层），因此夹具集中放在这里，
保证用例之间不共享 SQLite 文件、不残留"自定义规则"或 L2/L3 客户端。
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def tmp_env(tmp_path, monkeypatch):
    """把数据目录指到临时路径，并重置所有进程内单例。"""
    monkeypatch.setenv("FLOWMASK_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FLOWMASK_DB_PATH", str(tmp_path / "data" / "flowmask.db"))
    monkeypatch.delenv("FLOWMASK_API_TOKEN", raising=False)

    from app.api.deps import reset_pipeline_for_tests
    from app.core.config import get_settings
    from app.core.db import reset_database_for_tests
    from app.identify.rules import set_active_rules

    get_settings.cache_clear()
    reset_database_for_tests()
    reset_pipeline_for_tests()
    set_active_rules(None)
    yield
    get_settings.cache_clear()
    reset_database_for_tests()
    reset_pipeline_for_tests()
    set_active_rules(None)


@pytest.fixture()
def database(tmp_env):
    """初始化好的空库（表已建、内置规则已生效）。"""
    from app.api.routes_rules import sync_active_rules
    from app.core.db import get_database

    db = get_database()
    db.init()
    sync_active_rules(db)
    return db


@pytest.fixture()
def client(tmp_env):
    """FastAPI 测试客户端（应用实例按临时配置现建，避免读到开发库）。"""
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
