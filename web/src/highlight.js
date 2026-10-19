export function escapeHtml(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * 把命中区间渲染成带高亮的 HTML。
 * spans 的 start/end 是字符下标（不是字节），与后端 SpanOut 约定一致。
 */
export function renderHighlight(text, spans = []) {
  const source = String(text ?? "");
  const list = [...spans].filter((s) => s.end > s.start).sort((a, b) => a.start - b.start);
  let html = "";
  let cursor = 0;
  for (const span of list) {
    if (span.start < cursor) continue;
    html += escapeHtml(source.slice(cursor, span.start));
    const label = span.label || span.type || "敏感";
    html += `<mark class="hit" title="${escapeHtml(label)}（${escapeHtml(span.rule || "")}）">${escapeHtml(
      source.slice(span.start, span.end)
    )}</mark>`;
    cursor = span.end;
  }
  html += escapeHtml(source.slice(cursor));
  return html || "（空）";
}

/** 把后端返回的 masked_document（字符串或结构化字典）转成可读文本。 */
export function maskedDocumentText(maskedDocument) {
  if (maskedDocument == null) return "";
  if (typeof maskedDocument === "string") return maskedDocument;
  if (Array.isArray(maskedDocument)) return maskedDocument.map((item) => maskedDocumentText(item)).join("\n");
  if (typeof maskedDocument === "object") {
    const segments = maskedDocument.segments;
    if (Array.isArray(segments)) {
      // 纯文本只有一个片段时不用带位置前缀，直接给脱敏正文
      if (segments.length === 1) return segments[0].masked ?? segments[0].text ?? "";
      return segments
        .map((seg) => `${seg.path}: ${seg.masked ?? seg.text ?? ""}`)
        .join("\n");
    }
    return JSON.stringify(maskedDocument, null, 2);
  }
  return String(maskedDocument);
}
