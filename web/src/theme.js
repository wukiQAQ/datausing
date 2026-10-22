import { ref, watch } from "vue";

const STORAGE_KEY = "flowmask-theme";
const DARK_QUERY = "(prefers-color-scheme: dark)";

// 当前主题："light" | "dark"。默认跟随系统，用户手动切换后记住选择。
export const theme = ref("light");

function storedTheme() {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value === "dark" || value === "light" ? value : null;
  } catch {
    return null;
  }
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

export function applyTheme(next) {
  const value = next === "dark" ? "dark" : "light";
  theme.value = value;
  const root = document.documentElement;
  root.classList.toggle("dark", value === "dark");
  root.dataset.theme = value;
  try {
    window.localStorage.setItem(STORAGE_KEY, value);
  } catch {
    /* 隐私模式下写不进去不影响使用 */
  }
  return value;
}

export function toggleTheme() {
  return applyTheme(theme.value === "dark" ? "light" : "dark");
}

export function initTheme() {
  const forced = queryTheme();
  const initial = forced || storedTheme() || systemTheme();
  if (forced) {
    theme.value = forced;
    const root = document.documentElement;
    root.classList.toggle("dark", forced === "dark");
    root.dataset.theme = forced;
  } else {
    applyTheme(initial);
  }
  // 没有手动选择过时，跟着系统外观变化走
  try {
    window.matchMedia(DARK_QUERY).addEventListener("change", (event) => {
      if (!forced && !storedTheme()) applyTheme(event.matches ? "dark" : "light");
    });
  } catch {
    /* 老浏览器没有 addEventListener，忽略即可 */
  }
  return initial;
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
