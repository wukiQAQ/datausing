"""本地推理客户端共用的 HTTP 错误处理。"""

import json
import urllib.error
import urllib.request
from typing import Any


def post_json(endpoint: str, payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = json.loads(response.read())
    except urllib.error.HTTPError:
        raise
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise TimeoutError("推理请求超时") from exc
        raise ConnectionError("推理服务无法连接") from exc
    if not isinstance(body, dict) or body.get("degraded"):
        raise ValueError("推理服务返回无效或降级结果")
    return body
