import test from "node:test";
import assert from "node:assert/strict";
import { renderHighlight, maskedDocumentText } from "../src/highlight.js";
import { sourceSegments, countHits, safeError, snapshotClipboardText, snapshotOutputIssue } from "../src/workspace.js";

test("clipboard consumes complete masked values and omits potentially sensitive paths", () => {
  const result = {
    result_id: "snapshot", policy_version: "v1", source_fingerprint: "hash",
    coverage: "complete", segments: 2, content_type: "json", by_level: { L3: 1 },
    masked_document: { segments: [{ path: '$["SECRET_KEY"]', masked: "[屏蔽]", text: "SECRET_VALUE" }] },
  };
  assert.equal(snapshotClipboardText(result), "[屏蔽]");
  assert.match(snapshotOutputIssue({ ...result, by_level: { L3: 2 } }), /截断或丢段/);
  assert.equal(snapshotOutputIssue({
    ...result, content_type: "text", by_level: { L3: 0 },
    masked_document: { masked: "无命中文本", segments: [] },
  }), "");
  assert.equal(snapshotClipboardText({ ...result, coverage: "partial" }), "");
  assert.equal(snapshotClipboardText({ ...result, result_id: null }), "");
  assert.equal(snapshotClipboardText({ ...result, masked_document: { segments: [{ text: "SECRET" }] } }), "");
});

test("JSON special keys and numeric values follow unambiguous backend paths", () => {
  const index = sourceSegments('{"a.b":"one","a":{"b":"two"},"":13800138000}', "json");
  assert.equal(index.get('$["a.b"]'), "one");
  assert.equal(index.get('$.a.b'), "two");
  assert.equal(index.get('$[""]'), "13800138000");
});

test("duplicate CSV headers map by coordinates", () => {
  const index = sourceSegments("phone,phone\nfirst,second", "csv");
  assert.equal(index.get("row1[0]"), "first");
  assert.equal(index.get("row1[1]"), "second");
});

test("Python Unicode offsets highlight correctly after emoji", () => {
  const html = renderHighlight("😀电话13800138000", [
    { start: 3, end: 14, label: "手机号", level: "L2", finding: 2 },
  ]);
  assert.match(html, /😀电话<mark[^>]*>13800138000<\/mark>/);
  assert.match(html, /data-level="L2"/);
  assert.match(html, /data-finding="2"/);
});
test("sensitive text and untrusted span labels are escaped", () => {
  const html = renderHighlight("<script>", [
    { start: 0, end: 8, label: '"><img src=x>', rule: '"onfocus="x' },
  ]);
  assert.ok(!html.includes("<script>") && !html.includes("<img"));
  assert.match(html, /&lt;script&gt;/);
});
test("invalid and overlapping spans cannot duplicate text or inject attributes", () => {
  const html = renderHighlight("abcdef", [
    { start: -1, end: 3 },
    { start: 0, end: 2, level: '"><script>' },
    { start: 1, end: 4 },
    { start: 5, end: 100 },
  ]);
  assert.equal(html.replace(/<[^>]+>/g, ""), "abcdef");
  assert.equal((html.match(/<mark/g) || []).length, 1);
  assert.ok(!html.includes("<script>"));
});
test("masked document prefers full safe text and retains filtered segments", () => {
  assert.equal(
    maskedDocumentText({
      masked: "完整安全段落",
      segments: [{ masked: "高危片段" }],
    }),
    "完整安全段落",
  );
});
test("JSON indexes decoded values by backend paths, including arrays", () => {
  const index = sourceSegments(
    '{"phone":"13800138000","items":[{"name":"张三"}]}',
    "json",
  );
  assert.equal(index.get("$.phone"), "13800138000");
  assert.equal(index.get("$.items[0].name"), "张三");
  assert.equal(sourceSegments("{bad", "json").size, 0);
});
test("CSV maps quoted commas, newlines and escaped quotes", () => {
  const index = sourceSegments('name,note\r\n张三,"a,b\n""c"""\r\n', "csv");
  assert.equal(index.get("row1.name"), "张三");
  assert.equal(index.get("row1.note"), 'a,b\n"c"');
});
test("TSV recognizes header and missing column labels", () => {
  const index = sourceSegments("name\t\n张三\t13800138000", "csv");
  assert.equal(index.get("row1.col2"), "13800138000");
});
test("SQL ignores DDL and preserves literal paths and quoted semicolons", () => {
  const index = sourceSegments(
    "CREATE TABLE t(a TEXT DEFAULT 'ignore'); INSERT INTO t VALUES ('a;b','13800138000'); UPDATE t SET a='x';",
    "sql",
  );
  assert.deepEqual(
    [...index.keys()],
    ["stmt1.literal1", "stmt1.literal2", "stmt2.literal3"],
  );
  assert.equal(index.get("stmt1.literal2"), "13800138000");
});
test("hit counts are separate from segment counts", () => {
  assert.equal(countHits([{ hits: 4 }, { hits: 0 }, { hits: 2 }]), 6);
  assert.equal(countHits([]), 0);
});
test("error messages never echo backend validation inputs", () => {
  const error = new Error('422 [{"input":"secret original content"}]');
  assert.ok(!safeError(error).includes("secret"));
  assert.match(safeError(error), /输入或参数/);
});
