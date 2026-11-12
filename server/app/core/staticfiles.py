"""前端静态资源的缓存策略。

``/app`` 是 Vite 打包产物：``index.html`` 里引用的是带内容哈希的
``assets/index-<hash>.js`` 文件，而入口 HTML 本身不带哈希。如果让浏览器
按启发式缓存入口页，前端重新构建后短时间内仍会拿到旧页面（用户要强刷
才看到新版）。因此这里统一约定：

- HTML 入口：``no-cache``（每次重新验证，命中 304 仍然很便宜）
- ``assets/`` 下的哈希产物：``immutable`` + 一年长缓存
- 其他静态文件（favicon 等）：一小时缓存
"""

from __future__ import annotations

from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

_HTML_CACHE = "no-cache"
_ASSET_CACHE = "public, max-age=31536000, immutable"
_DEFAULT_CACHE = "public, max-age=3600"


def cache_control_for(path: str, *, is_html: bool = False) -> str:
    """按相对路径返回 ``Cache-Control`` 值（导出以便单测）。

    ``is_html`` 供调用方根据响应的 Content-Type 兜底：目录请求在 Windows
    上会传进来 ``"."`` 或带反斜杠的路径，单看后缀判断不出来。
    """
    normalized = (path or "").replace("\\", "/").split("?", 1)[0].lstrip("/")
    if is_html or not normalized or normalized in {".", "./"} or normalized.endswith(".html"):
        return _HTML_CACHE
    if normalized.startswith("assets/"):
        return _ASSET_CACHE
    return _DEFAULT_CACHE


class FrontendStaticFiles(StaticFiles):
    """在静态文件响应上补齐缓存头的前端挂载点。"""

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            is_html = "text/html" in response.headers.get("content-type", "")
            response.headers.setdefault("cache-control", cache_control_for(path, is_html=is_html))
        return response
