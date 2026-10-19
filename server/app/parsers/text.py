"""纯文本与文本粘贴接入。"""

from __future__ import annotations

from .base import Document, Segment


def parse_text(text: str, source: str = "text") -> Document:
    """整段文本作为唯一 Segment（保留原文与偏移，便于导出时整体回写）。"""
    return Document(
        source=source,
        content_type="text",
        segments=[Segment(path="$", text=text, offset=0)],
        raw=text,
    )
