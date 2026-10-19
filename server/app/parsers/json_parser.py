"""JSON 文件解析：把对象/数组里的每个字符串值抽成 Segment。

路径用 ``$`` 开头的简化 JSONPath（``$.user.phone``、``$.items[0].name``），
前端左侧高亮与右侧脱敏结果都靠这个路径对齐。
"""

from __future__ import annotations

import json

from .base import Document, ParseError, Segment


def _walk(node: object, path: str, out: list[Segment]) -> None:
    if isinstance(node, str):
        if node.strip():
            out.append(Segment(path=path, text=node))
        return
    if isinstance(node, dict):
        for key, value in node.items():
            # 键名本身不参与检测（键通常是字段名而不是数据），只递归值
            _walk(value, path + "." + str(key), out)
        return
    if isinstance(node, list):
        for index, value in enumerate(node):
            _walk(value, f"{path}[{index}]", out)


def parse_json(text: str, source: str = "data.json") -> Document:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ParseError(f"JSON 解析失败：{exc}") from exc

    segments: list[Segment] = []
    _walk(data, "$", segments)
    if not segments:
        raise ParseError("JSON 中没有可检测的字符串值")
    return Document(
        source=source,
        content_type="json",
        segments=segments,
        raw=text,
        meta={"root_type": type(data).__name__},
    )
