const BASE = "/api/v1";

async function request(path, options = {}) {
  const res = await fetch(BASE + path, options);
  const text = await res.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { raw: text };
  }
  if (!res.ok) {
    const detail = body && (body.detail || body.raw) ? body.detail || body.raw : res.statusText;
    throw new Error(`${res.status} ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
  }
  return body;
}

function jsonBody(data) {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  };
}

function formBody(file) {
  const fd = new FormData();
  fd.append("file", file);
  return { method: "POST", body: fd };
}

export const api = {
  health: () => request("/health"),
  formats: () => request("/formats"),
  detectText: (payload) => request("/detect", jsonBody(payload)),
  detectFile: (file) => request("/detect/file", formBody(file)),

  createTaskText: (text) => request("/tasks/text", jsonBody({ text })),
  createTaskFile: (file) => request("/tasks", formBody(file)),
  tasks: (page = 1, pageSize = 20) => request(`/tasks?page=${page}&page_size=${pageSize}`),
  task: (id) => request(`/tasks/${id}`),
  taskFindings: (id, page = 1, pageSize = 50) =>
    request(`/tasks/${id}/findings?page=${page}&page_size=${pageSize}`),
  taskExportUrl: (id, fmt) => `${BASE}/tasks/${id}/export?format=${fmt}`,

  stats: (taskId) => request(`/stats${taskId ? `?task_id=${taskId}` : ""}`),
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

  trafficFormats: () => request("/traffic/formats"),
  trafficSampleUrl: `${BASE}/traffic/sample`,
  importTraffic: (file, opts = {}) => {
    const fd = new FormData();
    fd.append("pcap", file);
    const qs = new URLSearchParams();
    if (opts.enableL2) qs.set("enable_l2", "true");
    if (opts.enableL3) qs.set("enable_l3", "true");
    if (opts.maskLevel) qs.set("mask_level", opts.maskLevel);
    if (opts.minLevel) qs.set("min_level", opts.minLevel);
    return request(`/traffic/import?${qs.toString()}`, { method: "POST", body: fd });
  },

  // 实时旁路：服务端跟随一个持续增长的 pcap 文件（由 dumpcap/Wireshark/自研脚本写）
  liveList: () => request("/traffic/live"),
  liveStart: (payload) => request("/traffic/live/start", jsonBody(payload)),
  liveStop: (liveId) => request(`/traffic/live/${liveId}/stop`, { method: "POST" }),
  liveClear: () => request("/traffic/live", { method: "DELETE" }),
};

export const LEVEL_COLORS = { L3: "#dc2626", L2: "#ea580c", L1: "#d97706", SAFE: "#0d9488" };
export const LEVEL_LABELS = { L3: "高危", L2: "中危", L1: "低危", SAFE: "安全" };
