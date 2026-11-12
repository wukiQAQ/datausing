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
  // Python 偏移以 Unicode code point 计数，不能直接 slice UTF-16 字符串。
  const source = Array.from(String(text ?? ""));
  const list = [...spans]
    .filter(
      (s) =>
        Number.isInteger(s.start) &&
        Number.isInteger(s.end) &&
        s.start >= 0 &&
        s.end <= source.length &&
        s.end > s.start,
    )
    .sort((a, b) => a.start - b.start);
  let html = "";
  let cursor = 0;
  for (const span of list) {
    if (span.start < cursor) continue;
    html += escapeHtml(source.slice(cursor, span.start).join(""));
    const label = span.label || span.type || "敏感";
    const level = ["L3", "L2", "L1", "SAFE"].includes(span.level)
      ? span.level
      : "L3";
    const finding =
      Number.isInteger(span.finding) && span.finding >= 0
        ? ` data-finding="${span.finding}" role="button" tabindex="0"`
        : "";
    html += `<mark class="hit" data-level="${level}"${finding} title="${escapeHtml(label)}（${escapeHtml(span.rule || "")}）">${escapeHtml(
      source.slice(span.start, span.end).join(""),
    )}</mark>`;
    cursor = span.end;
  }
  html += escapeHtml(source.slice(cursor).join(""));
  return html || "（空）";
}

/** 把后端返回的 masked_document（字符串或结构化字典）转成可读文本。 */
export function maskedDocumentText(maskedDocument) {
  if (maskedDocument == null) return "";
  if (typeof maskedDocument === "string") return maskedDocument;
  if (Array.isArray(maskedDocument))
    return maskedDocument.map((item) => maskedDocumentText(item)).join("\n");
  if (typeof maskedDocument === "object") {
    if (typeof maskedDocument.masked === "string") return maskedDocument.masked;
    const segments = maskedDocument.segments;
    if (Array.isArray(segments)) {
      // 纯文本只有一个片段时不用带位置前缀，直接给脱敏正文
      if (segments.length === 1)
        return segments[0].masked ?? segments[0].text ?? "";
      return segments
        .map((seg) => `${seg.path}: ${seg.masked ?? seg.text ?? ""}`)
        .join("\n");
    }
    return JSON.stringify(maskedDocument, null, 2);
  }
  return String(maskedDocument);
}
