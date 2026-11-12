import { buildQueryString } from "./stats.js";

const BASE = "/api/v1";

// 服务端配了 FLOWMASK_API_TOKEN 时，所有请求都要带 Bearer；
// 前端不内置登录页，运维在浏览器 localStorage 里写 flowmask_token 即可（README 有说明）。
export function authHeaders(base = {}) {
  const headers = { ...base };
  try {
    const token = window.localStorage.getItem("flowmask_token");
    if (token) headers.Authorization = `Bearer ${token}`;
  } catch {
    // 隐私模式禁用 localStorage 时忽略
  }
  return headers;
}

async function request(path, options = {}) {
  const res = await fetch(BASE + path, { ...options, headers: authHeaders(options.headers) });
  const text = await res.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { raw: text };
  }
  if (!res.ok) {
    let detail = body && (body.detail || body.raw) ? body.detail || body.raw : res.statusText;
    if (res.status === 401) {
      detail = "缺少或错误的 API Token：请在浏览器 localStorage 设置 flowmask_token（见 README）";
    }
    throw Object.assign(new Error(`${res.status} ${typeof detail === "string" ? detail : JSON.stringify(detail)}`), { status: res.status });
  }
  return body;
}

function jsonBody(data, method = "POST") {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  };
}

function formBody(file) {
  const fd = new FormData();
  fd.append("file", file);
  return { method: "POST", body: fd };
}

// 文件名：优先 Content-Disposition 的 filename*=UTF-8''（支持中文名），退化到 filename="..."。
export function fileNameFromDisposition(header) {
  if (!header) return "";
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (utf8) {
    try {
      return decodeURIComponent(utf8[1].trim());
    } catch {
      /* 编码异常就退回 filename= */
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain ? plain[1].trim() : "";
}

// 导出/下载统一入口：fetch 带 Authorization 拿 Blob，再用 <a download> 落盘。
// 不用 window.open 或裸 <a href>：配了 FLOWMASK_API_TOKEN 时它们带不上 Authorization，
// 且 window.open 在部分浏览器会被弹窗拦截。
export async function downloadWithAuth(url, fallbackName) {
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body && body.detail) detail = body.detail;
    } catch {
      /* 非 JSON 错误体，保留 statusText */
    }
    throw new Error(`${res.status} ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
  }
  const blob = await res.blob();
  const name = fileNameFromDisposition(res.headers.get("Content-Disposition")) || fallbackName;
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // click 后立刻 revoke 在部分浏览器会取不到内容，放到下一轮事件循环再清理
  setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
  return name;
}

// 脱敏文件导出走「带请求体的下载」：POST + JSON 或 multipart，响应是二进制附件，
// 不能复用 request()（它会按 JSON 解析），也不能复用 downloadWithAuth（它只发 GET）。
// 鉴权头照抄 authHeaders()，文件名优先取 Content-Disposition。
export async function downloadWithBody(path, options, fallbackName = "flowmask-export.bin", validateResponse) {
  const res = await fetch(BASE + path, { ...options, headers: authHeaders(options.headers) });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body && body.detail) detail = body.detail;
    } catch {
      /* 非 JSON 错误体，保留 statusText */
    }
    if (res.status === 401) {
      detail = "缺少或错误的 API Token：请在浏览器 localStorage 设置 flowmask_token（见 README）";
    }
    throw Object.assign(new Error(`${res.status} ${typeof detail === "string" ? detail : JSON.stringify(detail)}`), { status: res.status });
  }
  const blob = await res.blob();
  validateResponse?.(res);
  const name = fileNameFromDisposition(res.headers.get("Content-Disposition")) || fallbackName;
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  // click 之后立刻 revoke 在部分浏览器会取不到内容，推迟到下一轮事件循环
  setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
  return name;
}

// 统计接口兼容旧调用 api.stats(taskIdString)（BatchView 还在用），也接受对象查询；
// 其余新增接口一律对象参数，查询串统一交给 buildQueryString 处理空值/数组。
function normalizeStatsParams(params) {
  if (typeof params === "string") return params ? { task_id: params } : {};
  return params && typeof params === "object" ? params : {};
}

export const api = {
  health: () => request("/health"),
  formats: () => request("/formats"),
  detectText: (payload) => request("/detect", jsonBody(payload)),
  detectFile: (file, opts = {}) => {
    const fd = new FormData();
    fd.append("file", file);
    const qs = new URLSearchParams();
    if (typeof opts.enableL2 === "boolean") qs.set("enable_l2", String(opts.enableL2));
    if (typeof opts.enableL3 === "boolean") qs.set("enable_l3", String(opts.enableL3));
    if (opts.enableNer !== undefined && opts.enableNer !== null) {
      qs.set("enable_ner", opts.enableNer ? "true" : "false");
    }
    if (opts.maskLevel) qs.set("mask_level", opts.maskLevel);
    if (opts.minLevel) qs.set("min_level", opts.minLevel);
    return request(`/detect/file?${qs.toString()}`, { method: "POST", body: fd });
  },

  createTaskText: (text) => request("/tasks/text", jsonBody({ text })),
  createTaskFile: (file) => request("/tasks", formBody(file)),
  tasks: (page = 1, pageSize = 20) => request(`/tasks?page=${page}&page_size=${pageSize}`),
  task: (id) => request(`/tasks/${id}`),
  taskFindings: (id, page = 1, pageSize = 50) =>
    request(`/tasks/${id}/findings?page=${page}&page_size=${pageSize}`),
  taskComparison: (id, signal) => request(`/tasks/${id}/comparison`, { signal, cache: "no-store" }),
  taskComparisonDownloadUrl: (id) => `${BASE}/tasks/${id}/comparison/download`,
  taskExportUrl: (id, fmt) => `${BASE}/tasks/${id}/export?format=${fmt}`,
  privacyExport: (id, epsilon, l) =>
    request(`/tasks/${id}/export/privacy?format=json&epsilon=${epsilon}&l=${l}`),
  privacyExportUrl: (id, epsilon, l) =>
    `${BASE}/tasks/${id}/export/privacy?format=jsonl&epsilon=${epsilon}&l=${l}`,

  // 统计看板：参数含 task_id/level/types/content_type/start/end/days/tz_offset（见 web/src/stats.js）。
  stats: (params = {}) => request(`/stats${buildQueryString(normalizeStatsParams(params))}`),
  // 检测记录（findings）：看板「全部检测记录」与告警详情抽屉共用同一套筛选参数
  findings: (params = {}) => request(`/findings${buildQueryString(params)}`),
  finding: (id) => request(`/findings/${encodeURIComponent(id)}`),
  // 风险处置队列：服务端分页 + 状态筛选（open/ack/ignored），参数与统计一致
  alerts: (params = {}) => request(`/alerts${buildQueryString(params)}`),
  alertSummary: (params = {}) => request(`/alerts/summary${buildQueryString(params)}`),
  // 认领/忽略告警：操作人取自主题模块的 operator，放请求头 X-Operator-Id（与后端审计口径一致）。
  // 这里用动态 import("./theme.js") 而非静态 import：theme.js 目前用无扩展名的 "./api" 引用本文件，
  // Node 的 ESM 测试（api-snapshot.test.mjs）无法解析这种写法的依赖，静态 import 会让测试加载失败。
  ackAlert: async (id, status, note) => {
    const { operator } = await import("./theme.js");
    const payload = { status };
    const trimmed = note === undefined || note === null ? "" : String(note).trim();
    if (trimmed) payload.note = trimmed.slice(0, 200);
    return request(`/alerts/${encodeURIComponent(id)}/ack`, {
      ...jsonBody(payload),
      headers: { "Content-Type": "application/json", "X-Operator-Id": operator?.value || "local" },
    });
  },
  audit: (page = 1, pageSize = 20) => request(`/audit?page=${page}&page_size=${pageSize}`),
  auditVerify: () => request("/audit/verify"),

  rules: () => request("/rules"),
  addRule: (rule) => request("/rules", jsonBody(rule)),
  toggleRule: (id, enabled) =>
    request(`/rules/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ enabled }) }),
  deleteRule: (id) => request(`/rules/${id}`, { method: "DELETE" }),

  dynamicRender: (payload, operator) =>
    request("/dynamic/render", {
      ...jsonBody(payload),
      headers: { "Content-Type": "application/json", "X-Operator-Id": operator || "web-console" },
    }),
  dynamicPolicy: () => request("/dynamic/policy"),

  // 用户偏好：主题按操作人存服务端，换浏览器/换用户都能记住
  preferences: (operatorId) =>
    request("/preferences", { headers: operatorId ? { "X-Operator-Id": operatorId } : {} }),
  savePreference: (theme, operatorId) =>
    request("/preferences", {
      ...jsonBody({ theme }, "PUT"),
      headers: { "Content-Type": "application/json", "X-Operator-Id": operatorId || "local" },
    }),
  resetPreference: (operatorId) =>
    request("/preferences", {
      method: "DELETE",
      headers: { "X-Operator-Id": operatorId || "local" },
    }),
  preferenceUsers: () => request("/preferences/users?limit=20"),

  trafficFormats: () => request("/traffic/formats"),
  trafficSampleUrl: `${BASE}/traffic/sample`,
  importTraffic: (file, opts = {}) => {
    const fd = new FormData();
    fd.append("pcap", file);
    const qs = new URLSearchParams();
    if (typeof opts.enableL2 === "boolean") qs.set("enable_l2", String(opts.enableL2));
    if (typeof opts.enableL3 === "boolean") qs.set("enable_l3", String(opts.enableL3));
    if (opts.maskLevel) qs.set("mask_level", opts.maskLevel);
    if (opts.minLevel) qs.set("min_level", opts.minLevel);
    return request(`/traffic/import?${qs.toString()}`, { method: "POST", body: fd });
  },

  // 实时旁路：服务端跟随一个持续增长的 pcap 文件（由 dumpcap/Wireshark/自研脚本写）
  liveList: () => request("/traffic/live"),
  liveStart: (payload) => request("/traffic/live/start", jsonBody(payload)),
  liveStop: (liveId) => request(`/traffic/live/${liveId}/stop`, { method: "POST" }),
  liveClear: () => request("/traffic/live", { method: "DELETE" }),

  // 批量检测：一次上传多个文件，每个文件一个任务（坏文件不会拖垮整批）
  batchTasks: (files, opts = {}) => {
    const fd = new FormData();
    for (const file of files) fd.append("files", file);
    const qs = new URLSearchParams();
    if (typeof opts.enableL2 === "boolean") qs.set("enable_l2", String(opts.enableL2));
    if (typeof opts.enableL3 === "boolean") qs.set("enable_l3", String(opts.enableL3));
    if (opts.enableNer) qs.set("enable_ner", "true");
    if (opts.maskLevel && opts.maskLevel !== "auto") qs.set("mask_level", opts.maskLevel);
    if (opts.minLevel) qs.set("min_level", opts.minLevel);
    return request(`/tasks/batch?${qs.toString()}`, { method: "POST", body: fd });
  },

  // ---------------------------------------------------------------------
  // 第一次优化：脱敏策略 / 场景模板 / 脱敏文件导出 / AI 助手
  // ---------------------------------------------------------------------

  // 脱敏策略清单：下拉选项、说明、是否可逆、示例全部由服务端给，前端不再各写一份
  fetchMaskStrategies: () => request("/mask-strategies"),
  // 场景模板：一次套用推荐策略 + 级别 + L2/L3/NER 开关 + 数据保留说明
  fetchScenarios: () => request("/scenarios"),

  // 即时检测的脱敏文件导出（文本入口）：返回二进制附件并直接触发下载
  exportDetectText: (payload, fallbackName = "flowmask-masked.txt") =>
    downloadWithBody("/detect/export", jsonBody(payload), fallbackName),
  previewSnapshot: (result, strategy) =>
    request(`/detect/results/${encodeURIComponent(result.result_id)}/preview`, jsonBody({
      strategy, policy_version: result.policy_version,
    })),
  exportSnapshot: (result, strategy, fmt, fallbackName = "flowmask-masked.txt", isCurrent) =>
    downloadWithBody('/detect/export', jsonBody({
      result_id: result.result_id, policy_version: result.policy_version, strategy, fmt,
    }), fallbackName, (response) => {
      if ((isCurrent && !isCurrent()) ||
          response.headers.get("X-FlowMask-Result-Id") !== result.result_id ||
          response.headers.get("X-FlowMask-Policy-Version") !== result.policy_version) {
        throw Object.assign(new Error("409 snapshot changed"), { status: 409 });
      }
    }),
  // 文件入口：multipart，字段按契约平铺（strategy/fmt/enable_l2/enable_l3/enable_ner/enable_keywords）
  exportDetectFile: (file, opts = {}, fallbackName = "flowmask-masked.bin") => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("strategy", opts.strategy || "partial");
    fd.append("fmt", opts.fmt || "auto");
    if (typeof opts.enableL2 === "boolean") fd.append("enable_l2", String(opts.enableL2));
    if (typeof opts.enableL3 === "boolean") fd.append("enable_l3", String(opts.enableL3));
    fd.append("enable_ner", opts.enableNer ? "true" : "false");
    if (opts.enableKeywords !== undefined && opts.enableKeywords !== null) {
      fd.append("enable_keywords", opts.enableKeywords ? "true" : "false");
    }
    return downloadWithBody("/detect/file/export", { method: "POST", body: fd }, fallbackName);
  },

  // 还原「加密」策略产出的 enc: 密文（只在本机服务端还原）
  decryptText: (ciphertext) => request("/detect/decrypt", jsonBody({ ciphertext })),

  // 本地 AI 助手：推荐脱敏方案 / 风险研判（rules 模式纯本地规则，ollama 模式连本机模型）
  suggestMasking: (payload) => request("/assistant/suggest", jsonBody(payload)),
  adviseRisk: (payload) => request("/assistant/advise", jsonBody(payload)),
  fetchAssistantHealth: () => request("/assistant/health"),
  fetchAssistantModels: () => request("/assistant/models"),
};

export const LEVEL_COLORS = { L3: "#cf4b62", L2: "#b87927", L1: "#548bd0", SAFE: "#26996e" };
export const LEVEL_LABELS = { L3: "高危", L2: "中危", L1: "低危", SAFE: "安全" };
