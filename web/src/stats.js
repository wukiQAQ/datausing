// 统计看板纯逻辑：不依赖 Vue / DOM / 浏览器全局，可直接用 node:test 覆盖。
// 口径依据：.team/stats-contract.md 第 0、1、7 节。
// 时间一律由前端按浏览器本地时区算好，以 ISO 8601 带本地偏移（如 2026-11-05T00:00:00+08:00）传给后端；
// 日聚合用 tz_offset（分钟）= -new Date().getTimezoneOffset() 让趋势图的日期与本地日期一致。

// 10 类敏感数据英文键 → 中文（与 server/app/identify/types.py 保持一致）
export const TYPE_LABELS = {
  phone: "手机号",
  id_card: "身份证号",
  bank_card: "银行卡号",
  email: "邮箱",
  ip: "IP地址",
  secret: "密钥凭据",
  password: "密码",
  name: "姓名",
  address: "地址",
  medical: "医疗信息",
};

// 数据来源维度 = tasks.content_type
export const CONTENT_TYPE_LABELS = {
  text: "文本",
  json: "JSON",
  csv: "CSV",
  sql: "SQL",
  docx: "Word",
  xlsx: "Excel",
  pdf: "PDF",
  image: "图片",
  pcap: "流量包",
};

export const LEVELS = ["L3", "L2", "L1", "SAFE"];

const DAY_MS = 24 * 60 * 60 * 1000;

function pad2(value) {
  return String(value).padStart(2, "0");
}

function toCount(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : 0;
}

// 统一解析日期：纯日期（YYYY-MM-DD）按本地零点解析，避免 new Date("2026-11-01") 被当成 UTC。
export function parseDate(value) {
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : new Date(value.getTime());
  }
  if (typeof value !== "string" || !value.trim()) return null;
  const text = value.trim();
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if (dateOnly) {
    return new Date(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3]));
  }
  const date = new Date(text);
  return Number.isNaN(date.getTime()) ? null : date;
}

// 本地日期键 YYYY-MM-DD（趋势图按本地日期补零，与后端 tz_offset 口径一致）
export function localDateKey(value) {
  const date = parseDate(value);
  if (!date) return "";
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

export function startOfLocalDay(value) {
  const date = parseDate(value);
  if (!date) return null;
  date.setHours(0, 0, 0, 0);
  return date;
}

export function addLocalDays(value, days) {
  const date = parseDate(value);
  if (!date) return null;
  const amount = Number(days);
  date.setDate(date.getDate() + (Number.isFinite(amount) ? amount : 0));
  return date;
}

// Date → ISO 8601 带本地偏移（2026-11-05T00:00:00+08:00），交给后端换算 UTC
export function toLocalISO(value) {
  const date = parseDate(value);
  if (!date) return "";
  const offset = -date.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const absolute = Math.abs(offset);
  return (
    `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}` +
    `T${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}` +
    `${sign}${pad2(Math.floor(absolute / 60))}:${pad2(absolute % 60)}`
  );
}

export function normalizeTypes(value) {
  if (!value) return [];
  const list = Array.isArray(value) ? value : String(value).split(",");
  return [
    ...new Set(
      list
        .map((item) => String(item || "").trim())
        .filter(Boolean),
    ),
  ];
}

export function contentTypeLabel(value) {
  const key = String(value || "").trim();
  if (!key) return "—";
  return CONTENT_TYPE_LABELS[key.toLowerCase()] || key;
}

export function typeLabel(key) {
  return TYPE_LABELS[key] || String(key || "") || "未知类型";
}

function resolveMode(filter) {
  if (filter?.mode === "days" || filter?.mode === "all" || filter?.mode === "custom") {
    return filter.mode;
  }
  return filter?.days ? "days" : "all";
}

// 前端渲染用的时间窗（图表补零、文案提示都从这里取，保证与查询口径一致）：
// - days：start = now - days*24h，end = now（后端按 days 计算）
// - custom：start = 起始日零点，end = 结束日次日零点（接口区间 [start, end)，结束日整天包含）
// - all：不限制
export function timeWindow(filter = {}, now = new Date()) {
  const mode = resolveMode(filter);
  const end = now instanceof Date ? new Date(now) : new Date();
  if (mode === "custom") {
    const start = startOfLocalDay(filter.start);
    const rawEnd = startOfLocalDay(filter.end);
    const endExclusive = rawEnd ? addLocalDays(rawEnd, 1) : null;
    if (!start && !endExclusive) return { start: null, end: null };
    return { start, end: endExclusive };
  }
  if (mode === "days") {
    const days = Number(filter.days);
    if (!Number.isFinite(days) || days <= 0) return { start: null, end: null };
    return { start: new Date(end.getTime() - days * DAY_MS), end };
  }
  return { start: null, end: null };
}

// 统一筛选参数（/stats、/findings、/alerts、/alerts/summary 共用）：
// 时间预设 days 直接传 days（后端按 now-days 计算）；自定义区间传本地时区 ISO 边界；
// 空值全部剔除，types 数组 join 成逗号分隔；tz_offset 始终带上。
export function buildStatsQuery(filter = {}, now = new Date()) {
  const date = now instanceof Date ? now : new Date();
  const query = { tz_offset: -date.getTimezoneOffset() };
  const mode = resolveMode(filter);
  if (mode === "days") {
    const days = Number(filter.days);
    if (Number.isFinite(days) && days > 0) query.days = days;
  } else if (mode === "custom") {
    const window = timeWindow(filter, date);
    if (window.start) query.start = toLocalISO(window.start);
    if (window.end) query.end = toLocalISO(window.end);
  }
  if (filter.level) query.level = filter.level;
  const types = normalizeTypes(filter.types);
  if (types.length) query.types = types.join(",");
  if (filter.taskId) query.task_id = filter.taskId;
  if (filter.contentType) query.content_type = filter.contentType;
  return query;
}

// 查询对象 → 查询串：null / undefined / "" 剔除，数组用逗号连接（空数组剔除），0 等假值保留。
export function buildQueryString(params = {}) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params || {})) {
    if (value === null || value === undefined || value === "") continue;
    if (Array.isArray(value)) {
      const items = value
        .map((item) => (item === null || item === undefined ? "" : String(item).trim()))
        .filter(Boolean);
      if (!items.length) continue;
      search.set(key, items.join(","));
      continue;
    }
    if (typeof value === "number" && !Number.isFinite(value)) continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

// by_day 补零：startISO / endISO 为本地日期（含首尾），缺失时退化为数据自身的首尾日期。
// 输出固定包含 total / L3 / L2 / L1 / SAFE 与处置趋势的 open / ack / ignored 字段，方便图表直接取用。
export function fillDays(byDay = [], startISO, endISO) {
  const rows = new Map();
  for (const item of byDay || []) {
    const key = localDateKey(item?.date);
    if (key) rows.set(key, item);
  }
  const keys = [...rows.keys()].sort();
  let start = startOfLocalDay(startISO);
  let end = startOfLocalDay(endISO);
  if (!start && keys.length) start = startOfLocalDay(keys[0]);
  if (!end && keys.length) end = startOfLocalDay(keys[keys.length - 1]);
  if (!start || !end || start.getTime() > end.getTime()) return [];
  const output = [];
  // 看板最多展示 90 天；3650 天上限只作为防御，避免异常输入造成死循环。
  for (let guard = 0; guard < 4000; guard++) {
    const key = localDateKey(start);
    const row = rows.get(key) || {};
    output.push({
      date: key,
      total: toCount(row.total),
      L3: toCount(row.L3),
      L2: toCount(row.L2),
      L1: toCount(row.L1),
      SAFE: toCount(row.SAFE),
      open: toCount(row.open),
      ack: toCount(row.ack),
      ignored: toCount(row.ignored),
    });
    if (start.getTime() >= end.getTime()) break;
    start = addLocalDays(start, 1);
  }
  return output;
}

// 排序：count 数量 / hits 命中次数 / risk 高危（levels.L3）优先再按数量。
// 不改动入参数组；同分按中文名稳定排序，避免图表每次刷新顺序抖动。
export function sortTypes(byType = [], mode = "count") {
  const rows = [...(byType || [])];
  const number = (value) => {
    const result = Number(value);
    return Number.isFinite(result) ? result : 0;
  };
  const labelOf = (row) => String(row?.label || row?.type || row?.key || "");
  const highRisk = (row) => number(row?.levels?.L3);
  const sorters = {
    count: (a, b) => number(b.count) - number(a.count),
    hits: (a, b) => number(b.hits) - number(a.hits) || number(b.count) - number(a.count),
    risk: (a, b) => highRisk(b) - highRisk(a) || number(b.count) - number(a.count),
  };
  const sorter = sorters[mode] || sorters.count;
  return rows.sort((a, b) => {
    const result = sorter(a, b);
    if (result) return result;
    return labelOf(a).localeCompare(labelOf(b), "zh-Hans-CN");
  });
}

// 0.5 → 50%；null / 非数字 → "—"（total=0 等无意义比值由调用方决定是否传入）
export function formatPercent(value, digits = 1) {
  const number = Number(value);
  if (value === null || value === undefined || !Number.isFinite(number) || number < 0) {
    return "—";
  }
  const factor = 10 ** digits;
  const percent = Math.round(number * 100 * factor) / factor;
  return `${Number.isInteger(percent) ? percent : percent.toFixed(digits)}%`;
}

// UTC ISO → 本地 "YYYY-MM-DD HH:mm:ss"；无法解析时显示 "—"，不把原始字符串直接抛给用户。
export function formatDateTime(value) {
  const date = parseDate(value);
  if (!date) return "—";
  return (
    `${localDateKey(date)} ` +
    `${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`
  );
}
