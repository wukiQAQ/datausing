// 服务端偏移为 Unicode 码点；使用 Array.from 避免中文/emoji 的 UTF-16 偏移错误。
export function prepareComparison(payload) {
  const sides = { original: [], masked: [] };
  const changes = [];
  const anchors = [];
  for (const segment of payload.segments) {
    const base = { original: sides.original.length, masked: sides.masked.length };
    anchors.push({ originalLine: base.original, maskedLine: base.masked });
    const local = {};
    for (const side of Object.keys(sides)) {
      let offset = 0;
      local[side] = segment[side].split("\n").map(text => {
        const line = { text, start: offset, end: offset + Array.from(text).length, marks: [], path: segment.path };
        offset = line.end + 1;
        return line;
      });
      for (const line of local[side]) sides[side].push(line);
    }
    for (const change of segment.changes) {
      const id = changes.length;
      const entry = { ...change, id, path: segment.path };
      for (const side of Object.keys(sides)) {
        const start = side === "original" ? change.start : change.masked_start;
        const end = side === "original" ? change.end : change.masked_end;
        let first = null;
        let lo = 0, hi = local[side].length;
        while (lo < hi) { const mid = (lo + hi) >> 1; if (local[side][mid].end < start) lo = mid + 1; else hi = mid; }
        for (let index = lo; index < local[side].length && local[side][index].start <= end; index++) {
          const line = local[side][index];
          if ((start < line.end && end > line.start) || (start === end && start >= line.start && start <= line.end)) {
            line.marks.push({ id, start: Math.max(0, start - line.start), end: Math.min(line.end, end) - line.start });
            if (first === null) first = base[side] + index;
          }
        }
        entry[side + "Line"] = first ?? base[side];
      }
      changes.push(entry);
      anchors.push(entry);
    }
    anchors.push({ originalLine: sides.original.length - 1, maskedLine: sides.masked.length - 1 });
  }
  for (const lines of Object.values(sides)) {
    for (const line of lines) {
      const chars = Array.from(line.text);
      // 后端保证升序不重叠；这里仍排序并裁剪，避免契约漂移时复制/错位文本。
      line.marks.sort((a, b) => a.start - b.start || a.end - b.end);
      let cursor = 0;
      line.parts = [];
      for (const mark of line.marks) {
        if (mark.end < cursor) continue;
        const start = Math.max(cursor, Math.min(mark.start, chars.length));
        const end = Math.max(start, Math.min(mark.end, chars.length));
        line.parts.push({ text: chars.slice(cursor, start).join("") });
        line.parts.push({ text: chars.slice(start, end).join(""), id: mark.id });
        cursor = end;
      }
      line.parts.push({ text: chars.slice(cursor).join("") });
    }
  }
  return { ...sides, changes, anchors, hits: payload.hits, coverage: payload.coverage, status: payload.status };
}

export function mapLine(anchors, from, value) {
  const a = from + "Line", b = (from === "original" ? "masked" : "original") + "Line";
  if (!anchors.length) return 0;
  if (!Number.isFinite(value)) value = 0;
  let lo = 0, hi = anchors.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (anchors[mid][a] < value) lo = mid + 1; else hi = mid; }
  // 同一行可能有多个锚点（整段屏蔽/多处命中）：取行首，避免滚到该段的最后一行。
  if (lo < anchors.length && anchors[lo][a] === value) return anchors[lo][b];
  if (lo === 0) return anchors[0][b];
  if (lo >= anchors.length) return anchors[anchors.length - 1][b];
  const left = anchors[lo - 1], right = anchors[lo];
  const span = right[a] - left[a];
  return span ? left[b] + (value - left[a]) / span * (right[b] - left[b]) : left[b];
}

// 点击高亮时只在目标行贴近视口边缘或不可见时才定位，避免把当前视口整块顶走。
export function needsScroll(offset, lineHeight, scrollTop, viewportHeight, marginLines = 2) {
  const margin = lineHeight * marginLines;
  return offset < scrollTop + margin || offset + lineHeight > scrollTop + viewportHeight - margin;
}

// 估算自动换行后的行高：CJK/全角按 2 个半角单位计，用于虚拟滚动下未测量行的位置。
export function estimateWrappedHeight(text, availableWidth, lineHeight = 26, charWidth = 7.8) {
  let units = 0;
  for (const ch of text) units += ch.codePointAt(0) > 0x2e80 ? 2 : 1;
  const perLine = Math.max(1, Math.floor(Math.max(1, availableWidth) / charWidth));
  return Math.max(1, Math.ceil(units / perLine)) * lineHeight;
}
