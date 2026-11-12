"""前端静态资源缓存策略的回归测试。

背景：``/app/`` 是 Vite 构建产物，入口 ``index.html`` 文件名不带哈希。
早先没有显式 ``Cache-Control``，浏览器按启发式缓存入口页，前端重新构建
后短时间内刷新仍会看到旧页面。这里固定两条约定：

- 入口 HTML → ``no-cache``（每次都重新验证）
- ``assets/`` 下带内容哈希的产物 → 一年 ``immutable``
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.staticfiles import FrontendStaticFiles, cache_control_for


def test_cache_control_for_entry_and_assets() -> None:
    assert cache_control_for("") == "no-cache"
    assert cache_control_for("index.html") == "no-cache"
    assert cache_control_for("assets/index-DCnX_ewy.js").startswith("public, max-age=31536000")
    # Windows 上 StaticFiles 传进来的是反斜杠相对路径，也要判定为资产
    assert cache_control_for("assets\\index-DCnX_ewy.js").startswith("public, max-age=31536000")
    assert cache_control_for("favicon.ico").startswith("public, max-age=")


def test_frontend_static_response_headers(tmp_path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><div id=\"app\"></div>", encoding="utf-8")
    (assets / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")

    app = FastAPI()
    app.mount("/app", FrontendStaticFiles(directory=tmp_path, html=True), name="frontend")
    client = TestClient(app)

    entry = client.get("/app/")
    assert entry.status_code == 200
    assert entry.headers["cache-control"] == "no-cache"

    asset = client.get("/app/assets/index-abc123.js")
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
