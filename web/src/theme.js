import { ref, watch } from "vue";

import { api } from "./api";

const DARK_QUERY = "(prefers-color-scheme: dark)";
const THEME_KEY = "flowmask-theme";
const OPERATOR_KEY = "flowmask-operator";
const DEFAULT_OPERATOR = "local";

// 当前主题："light" | "dark"；操作人是多用户偏好的身份（服务端按它存主题）
export const theme = ref("light");
export const operator = ref(DEFAULT_OPERATOR);

function readLocal(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeLocal(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* 隐私模式下写不进去不影响使用 */
  }
}

// 每个操作人一份本地缓存，切用户时不会互相覆盖
function cacheKey() {
  return `${THEME_KEY}:${operator.value}`;
}

function storedTheme() {
  const value = readLocal(cacheKey());
  return value === "dark" || value === "light" || value === "system" ? value : null;
}

function systemTheme() {
  try {
    return window.matchMedia(DARK_QUERY).matches ? "dark" : "light";
  } catch {
    return "light";
  }
}

// 演示/截图用的临时覆盖：/app/?theme=dark 只对本次打开生效，不写进本地存储
function queryTheme() {
  try {
    const value = new URLSearchParams(window.location.search).get("theme");
    return value === "dark" || value === "light" ? value : null;
  } catch {
    return null;
  }
}

function resolve(mode) {
  return mode === "system" ? systemTheme() : mode === "dark" ? "dark" : "light";
}

function paint(mode) {
  const value = resolve(mode);
  theme.value = value;
  const root = document.documentElement;
  root.classList.toggle("dark", value === "dark");
  root.dataset.theme = value;
  return value;
}

export function applyTheme(next) {
  const mode = next === "dark" || next === "light" || next === "system" ? next : "light";
  const value = paint(mode);
  writeLocal(cacheKey(), mode);
  return value;
}

export function toggleTheme() {
  const next = theme.value === "dark" ? "light" : "dark";
  applyTheme(next);
  // 同步到服务端（按操作人存），失败只影响"换浏览器记住"，不影响本次使用
  api.savePreference(next, operator.value).catch(() => {});
  return next;
}

export function setOperator(next) {
  const value = (next || "").trim().slice(0, 64) || DEFAULT_OPERATOR;
  operator.value = value;
  writeLocal(OPERATOR_KEY, value);
  return syncThemeFromServer();
}

// 服务端偏好优先：有个人选择就用它，没有就用服务端默认主题（system 时跟随系统）
export async function syncThemeFromServer() {
  try {
    const body = await api.preferences(operator.value);
    if (body.theme) {
      applyTheme(body.theme);
    } else if (!storedTheme()) {
      const value = paint(body.default_theme || "system");
      writeLocal(cacheKey(), body.default_theme || "system");
      return { theme: value, source: "server-default" };
    }
    return { theme: theme.value, source: body.theme ? "user" : "cache" };
  } catch {
    return { theme: theme.value, source: "offline" };
  }
}

export function initTheme() {
  operator.value = readLocal(OPERATOR_KEY) || DEFAULT_OPERATOR;
  // 演示/截图：/app/?operator=alice 可直接以某个用户身份打开（不写本地存储）
  try {
    const fromQuery = new URLSearchParams(window.location.search).get("operator");
    if (fromQuery) operator.value = fromQuery.trim().slice(0, 64) || DEFAULT_OPERATOR;
  } catch {
    /* 老浏览器没有 URLSearchParams 也无妨 */
  }
  const forced = queryTheme();
  const initial = forced || storedTheme() || "system";
  if (forced) {
    paint(forced);
  } else {
    applyTheme(initial);
  }
  try {
    window.matchMedia(DARK_QUERY).addEventListener("change", (event) => {
      if (!forced && storedTheme() === "system") paint(event.matches ? "dark" : "light");
    });
  } catch {
    /* 老浏览器没有 addEventListener，忽略即可 */
  }
  return theme.value;
}

// ECharts 这类命令式渲染的组件用它订阅主题变化
export function watchTheme(handler) {
  return watch(theme, () => handler(theme.value), { flush: "post" });
}

// 读 style.css 里的主题变量，避免 JS 里再写一份颜色
export function cssVar(name, fallback = "") {
  try {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return value || fallback;
  } catch {
    return fallback;
  }
}
