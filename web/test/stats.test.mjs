import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  buildQueryString,
  buildStatsQuery,
  contentTypeLabel,
  fillDays,
  formatDateTime,
  formatPercent,
  localDateKey,
  normalizeTypes,
  sortTypes,
  timeWindow,
  toLocalISO,
  typeLabel,
} from "../src/stats.js";

// 固定“当前时间”为本地 2026-11-12 09:00:00，避免测试受运行时刻影响
const NOW = new Date(2026, 10, 12, 9, 0, 0);

test("buildStatsQuery 预设天数只传 days 与 tz_offset，空值不出现", () => {
  const query = buildStatsQuery({ mode: "days", days: 30 }, NOW);
  assert.deepEqual(query, {
    tz_offset: -NOW.getTimezoneOffset(),
    days: 30,
  });
  assert.equal(query.start, undefined);
  assert.equal(query.end, undefined);
});

test("buildStatsQuery 自定义区间按本地零点换算，结束日整天包含（[start, end)）", () => {
  const query = buildStatsQuery(
    {
      mode: "custom",
      start: new Date(2026, 9, 1, 13, 30),
      end: new Date(2026, 10, 12, 8, 0),
    },
    NOW,
  );
  assert.match(query.start, /^2026-10-01T00:00:00[+-]\d{2}:\d{2}$/);
  // 结束日 11-12 整天包含 → 次日 00:00 作为开区间上界
  assert.match(query.end, /^2026-11-13T00:00:00[+-]\d{2}:\d{2}$/);
  assert.equal(query.tz_offset, -NOW.getTimezoneOffset());
});

test("buildStatsQuery 全部时间只保留 tz_offset，等级/类型/任务/来源正确序列化", () => {
  assert.deepEqual(buildStatsQuery({ mode: "all" }, NOW), {
    tz_offset: -NOW.getTimezoneOffset(),
  });
  const query = buildStatsQuery(
    {
      mode: "days",
      days: 7,
      level: "L3",
      types: ["phone", "", "email", "phone"],
      taskId: "task-1",
      contentType: "csv",
    },
    NOW,
  );
  assert.equal(query.level, "L3");
  assert.equal(query.types, "phone,email");
  assert.equal(query.task_id, "task-1");
  assert.equal(query.content_type, "csv");
  assert.equal(query.days, 7);
});

test("buildStatsQuery 剔除空字符串与空数组，无时间范围时没有 start/end/days", () => {
  const query = buildStatsQuery(
    { mode: "all", level: "", types: [], taskId: "", contentType: "" },
    NOW,
  );
  assert.deepEqual(Object.keys(query), ["tz_offset"]);
});

test("buildQueryString 跳过空值、数组用逗号连接、保留 0", () => {
  assert.equal(buildQueryString({}), "");
  assert.equal(
    buildQueryString({ page: 1, level: "", types: [], task_id: null, unused: undefined }),
    "?page=1",
  );
  assert.equal(
    buildQueryString({ types: ["phone", "email"], page: 0, status: "ack" }),
    "?types=phone%2Cemail&page=0&status=ack",
  );
});

test("fillDays 跨月补零并保留已有数据", () => {
  const rows = [
    { date: "2026-02-01", L3: 2, total: 2 },
    { date: "2026-01-31", L2: 1, total: 1 },
  ];
  const filled = fillDays(rows, "2026-01-30", "2026-02-02");
  assert.deepEqual(
    filled.map((row) => row.date),
    ["2026-01-30", "2026-01-31", "2026-02-01", "2026-02-02"],
  );
  assert.deepEqual(filled[0], {
    date: "2026-01-30",
    total: 0,
    L3: 0,
    L2: 0,
    L1: 0,
    SAFE: 0,
    open: 0,
    ack: 0,
    ignored: 0,
  });
  assert.equal(filled[1].L2, 1);
  assert.equal(filled[2].L3, 2);
  assert.equal(filled[3].total, 0);
});

test("fillDays 空数组与无范围时安全退化", () => {
  assert.deepEqual(fillDays([], "2026-01-01", "2026-01-03").length, 3);
  assert.deepEqual(fillDays([], null, null), []);
  assert.deepEqual(fillDays([], "2026-01-03", "2026-01-01"), []);
  const inferred = fillDays([{ date: "2026-03-02", total: 1 }], null, null);
  assert.deepEqual(inferred.map((row) => row.date), ["2026-03-02"]);
});

test("fillDays 合并处置趋势字段（open/ack/ignored）", () => {
  const filled = fillDays(
    [{ date: "2026-11-11", open: 2, ack: 1, ignored: 0, total: 3 }],
    "2026-11-11",
    "2026-11-12",
  );
  assert.equal(filled.length, 2);
  assert.equal(filled[0].open, 2);
  assert.equal(filled[0].ack, 1);
  assert.equal(filled[1].open, 0);
});

test("sortTypes 三种排序且不改动入参数组", () => {
  const rows = [
    { type: "phone", count: 5, hits: 9, levels: { L3: 0 } },
    { type: "id_card", count: 3, hits: 12, levels: { L3: 2 } },
    { type: "email", count: 8, hits: 1, levels: { L3: 0 } },
  ];
  assert.deepEqual(
    sortTypes(rows, "count").map((row) => row.type),
    ["email", "phone", "id_card"],
  );
  assert.deepEqual(
    sortTypes(rows, "hits").map((row) => row.type),
    ["id_card", "phone", "email"],
  );
  assert.deepEqual(
    sortTypes(rows, "risk").map((row) => row.type),
    ["id_card", "email", "phone"],
  );
  assert.deepEqual(
    sortTypes(rows, "unknown-mode").map((row) => row.type),
    ["email", "phone", "id_card"],
  );
  assert.deepEqual(
    rows.map((row) => row.type),
    ["phone", "id_card", "email"],
  );
  assert.deepEqual(sortTypes([], "count"), []);
});

test("sortTypes 同分时按中文名稳定排序", () => {
  const rows = [
    { type: "email", label: "邮箱", count: 2 },
    { type: "phone", label: "手机号", count: 2 },
  ];
  const sorted = sortTypes(rows, "count").map((row) => row.label);
  assert.deepEqual(sorted, ["手机号", "邮箱"].sort((a, b) => a.localeCompare(b, "zh-Hans-CN")));
});

test("contentTypeLabel / typeLabel 中文映射与未知值回退", () => {
  assert.equal(contentTypeLabel("text"), "文本");
  assert.equal(contentTypeLabel("XLSX"), "Excel");
  assert.equal(contentTypeLabel("pcap"), "流量包");
  assert.equal(contentTypeLabel("custom"), "custom");
  assert.equal(contentTypeLabel(""), "—");
  assert.equal(typeLabel("id_card"), "身份证号");
  assert.equal(typeLabel("unknown_type"), "unknown_type");
  assert.equal(typeLabel(""), "未知类型");
});

test("normalizeTypes 接受数组与逗号字符串并去重", () => {
  assert.deepEqual(normalizeTypes(["phone", " email ", "phone"]), ["phone", "email"]);
  assert.deepEqual(normalizeTypes("phone, email,"), ["phone", "email"]);
  assert.deepEqual(normalizeTypes([]), []);
  assert.deepEqual(normalizeTypes(null), []);
});

test("formatPercent 处理 0 / 比例 / 非法值", () => {
  assert.equal(formatPercent(0.5), "50%");
  assert.equal(formatPercent(0), "0%");
  assert.equal(formatPercent(1), "100%");
  assert.equal(formatPercent(1 / 3), "33.3%");
  assert.equal(formatPercent(null), "—");
  assert.equal(formatPercent(undefined), "—");
  assert.equal(formatPercent("bad"), "—");
  assert.equal(formatPercent(-0.2), "—");
});

test("formatDateTime 把 UTC ISO 转成本地时间", () => {
  const local = new Date(2026, 10, 12, 9, 5, 6);
  assert.equal(formatDateTime(local.toISOString()), "2026-11-12 09:05:06");
  assert.equal(formatDateTime(""), "—");
  assert.equal(formatDateTime("not-a-date"), "—");
});

test("timeWindow 与 localDateKey 时区语义一致", () => {
  const days = timeWindow({ mode: "days", days: 7 }, NOW);
  assert.equal(days.end.getTime(), NOW.getTime());
  assert.equal(days.start.getTime(), NOW.getTime() - 7 * 24 * 60 * 60 * 1000);
  assert.equal(localDateKey(days.start), "2026-11-05");

  const custom = timeWindow(
    { mode: "custom", start: new Date(2026, 9, 1), end: new Date(2026, 10, 12) },
    NOW,
  );
  assert.equal(localDateKey(custom.start), "2026-10-01");
  assert.equal(localDateKey(custom.end), "2026-11-13");

  assert.deepEqual(timeWindow({ mode: "all" }, NOW), { start: null, end: null });
  assert.deepEqual(timeWindow({ mode: "custom" }, NOW), { start: null, end: null });
});

test("toLocalISO 输出带本地偏移的秒级时间", () => {
  const iso = toLocalISO(new Date(2026, 0, 2, 3, 4, 5));
  assert.match(iso, /^2026-01-02T03:04:05[+-]\d{2}:\d{2}$/);
  assert.equal(toLocalISO("bad"), "");
});

// 契约第 8 节：只断言页面真实接入了能力，且不出现虚构的「重新检测」入口。
test("StatsView 使用真实统计 / 告警 / 洞察接口，且没有虚构的重新检测按钮", () => {
  const view = readFileSync(new URL("../src/views/StatsView.vue", import.meta.url), "utf8");
  for (const call of [
    "api.stats(",
    "api.findings(",
    "api.finding(",
    "api.alerts(",
    "api.ackAlert(",
    "api.adviseRisk(",
  ]) {
    assert.ok(view.includes(call), `StatsView 缺少真实接口调用 ${call}`);
  }
  assert.ok(!view.includes("重新检测"), "StatsView 不应出现虚构的「重新检测」入口");
});
