<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import * as echarts from "echarts";
import PageHeader from "../components/PageHeader.vue";
import RiskIndicator from "../components/RiskIndicator.vue";
import { api, downloadWithAuth, LEVEL_COLORS, LEVEL_LABELS } from "../api";
import { cssVar, watchTheme } from "../theme";
import { safeError } from "../workspace";
import {
  CONTENT_TYPE_LABELS,
  TYPE_LABELS,
  addLocalDays,
  buildStatsQuery,
  contentTypeLabel,
  fillDays,
  formatDateTime,
  formatPercent,
  localDateKey,
  sortTypes,
  startOfLocalDay,
  timeWindow,
  toLocalISO,
  typeLabel,
} from "../stats";

const LEVELS = ["L3", "L2", "L1", "SAFE"];

// 等级定义直接来自契约第 0 节 / server/app/identify/scoring.py，避免页面上出现“看起来像”的假定义
const LEVEL_DEFS = [
  { level: "L3", text: "score ≥ 0.85，或身份证 / 银行卡 / 密钥凭据且置信度 ≥ 0.90 或直接命中强证据。" },
  { level: "L2", text: "score ≥ 0.55，且未达到 L3。" },
  { level: "L1", text: "score ≥ 0.25，且未达到 L2。" },
  { level: "SAFE", text: "score < 0.25：识别到疑似候选但综合评分低于低危阈值，不产生告警；完全未命中的片段不落库。" },
];

const STATUS_OPTIONS = [
  { value: "all", label: "全部" },
  { value: "open", label: "待处理" },
  { value: "ack", label: "已认领" },
  { value: "ignored", label: "已忽略" },
];
const STATUS_LABELS = { open: "待处理", ack: "已认领", ignored: "已忽略" };
const STATUS_TAG_TYPES = { open: "danger", ack: "warning", ignored: "info" };
const SOURCE_LABELS = {
  rules: "规则分析",
  local_llm: "本机模型解读",
  degraded: "模型不可用 · 已回退规则分析",
};

// ------------------------------ 状态 ------------------------------
const stats = ref(null);
const statsLoading = ref(false);
const statsError = ref("");
const lastUpdated = ref("");

// 统一筛选：stats / findings / alerts / advise 共用同一份参数
const filter = reactive({
  mode: "days",
  days: 30,
  start: null,
  end: null,
  level: "",
  types: [],
  taskId: "",
  contentType: "",
});
const timeRadio = ref("30");
const customRange = ref(null);
const typeSort = ref("count");
const sectionTab = ref("trend");

const queueStatus = ref("open");
const queue = reactive({ items: [], total: 0, page: 1, pageSize: 10, loading: false, error: "" });

const records = reactive({ visible: false, items: [], total: 0, page: 1, pageSize: 10, loading: false, error: "" });

const drawer = reactive({ visible: false, alert: null, finding: null, loading: false, error: "" });

const advise = reactive({ loading: false, error: "", result: null });

// ------------------------------ 派生数据 ------------------------------
function numberOr0(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : 0;
}

const risk = computed(() => stats.value?.risk || {});
const byLevel = computed(() => stats.value?.by_level || {});
const totalFindings = computed(() => numberOr0(stats.value?.total_findings));
const totalHits = computed(() => numberOr0(stats.value?.total_hits));
const tasksCount = computed(() => numberOr0(stats.value?.tasks));
const tasksTotal = computed(() => numberOr0(stats.value?.tasks_total));

const levelRows = computed(() =>
  LEVELS.map((level) => ({ level, count: numberOr0(byLevel.value[level]) })),
);
const levelTotal = computed(() => levelRows.value.reduce((sum, row) => sum + row.count, 0));
const l3Count = computed(() => numberOr0(byLevel.value.L3));
const l3RatioText = computed(() => (levelTotal.value ? formatPercent(l3Count.value / levelTotal.value) : "—"));
const hasLevelData = computed(() => levelTotal.value > 0);

const openCount = computed(() => numberOr0(risk.value.open));
const riskTotal = computed(() => numberOr0(risk.value.total));
const handedCount = computed(() => {
  if (risk.value.handled !== undefined && risk.value.handled !== null) {
    return numberOr0(risk.value.handled);
  }
  return numberOr0(risk.value.ack) + numberOr0(risk.value.ignored);
});
const handledRatioText = computed(() => {
  if (riskTotal.value <= 0) return "—";
  const ratio = Number(risk.value.handled_ratio);
  if (Number.isFinite(ratio)) return formatPercent(ratio);
  return formatPercent(handedCount.value / riskTotal.value);
});
const openByLevelText = computed(() => {
  const levels = risk.value.open_by_level || {};
  const parts = ["L3", "L2", "L1"]
    .map((level) => [level, numberOr0(levels[level])])
    .filter(([, count]) => count > 0)
    .map(([level, count]) => `${level} ${count}`);
  if (parts.length) return `待处理：${parts.join(" · ")}`;
  return openCount.value > 0 ? "待处理明细见下方队列" : "当前无待处理风险";
});

const dimensions = computed(() => stats.value?.dimensions || {});
// dimensions 里的维度既可能是 [{ key, label }]，也可能直接是字符串键；这里统一归一化，
// 避免后端字段形态变化时筛选下拉出现空选项。
const typeOptions = computed(() => {
  const items = dimensions.value.types;
  if (Array.isArray(items) && items.length) {
    return items
      .map((item) => {
        if (typeof item === "string") return { key: item, label: typeLabel(item) };
        const key = item.key || item.value || item.type || "";
        return { key, label: item.label || typeLabel(key) };
      })
      .filter((item) => item.key);
  }
  return Object.entries(TYPE_LABELS).map(([key, label]) => ({ key, label }));
});
const levelOptions = computed(() => {
  const items = dimensions.value.levels;
  if (Array.isArray(items) && items.length) {
    return items
      .map((item) => {
        if (typeof item === "string") return { value: item, label: LEVEL_LABELS[item] || item };
        const value = item.value || item.level || item.key || item.type || "";
        return { value, label: item.label || LEVEL_LABELS[value] || value };
      })
      .filter((item) => item.value);
  }
  return LEVELS.map((level) => ({ value: level, label: LEVEL_LABELS[level] }));
});
const contentTypeOptions = computed(() => {
  const items = dimensions.value.content_types;
  if (Array.isArray(items) && items.length) {
    return items
      .map((item) => {
        if (typeof item === "string") return { value: item, label: contentTypeLabel(item) };
        const value = item.value || item.key || item.type || "";
        return { value, label: item.label || contentTypeLabel(value) };
      })
      .filter((item) => item.value);
  }
  return Object.entries(CONTENT_TYPE_LABELS).map(([value, label]) => ({ value, label }));
});
const taskOptions = computed(() => (Array.isArray(dimensions.value.tasks) ? dimensions.value.tasks : []));

const sortedTypes = computed(() => sortTypes(stats.value?.by_type || [], typeSort.value));
const typeTotal = computed(() => sortedTypes.value.reduce((sum, row) => sum + numberOr0(row.count), 0));
const hasTypeData = computed(() => sortedTypes.value.some((row) => numberOr0(row.count) > 0));

const scoreEntries = computed(() => Object.entries(stats.value?.score_histogram || {}));
const hasScoreData = computed(() => scoreEntries.value.some(([, count]) => numberOr0(count) > 0));

const timeText = computed(() => {
  if (filter.mode === "all") return "全部时间";
  if (filter.mode === "custom") {
    if (!filter.start || !filter.end) return "自定义时间（未选）";
    return `${localDateKey(filter.start)} 至 ${localDateKey(filter.end)}`;
  }
  return `近 ${filter.days} 天`;
});
// 给后端洞察文本用的范围前缀（自定义区间未选完整时按全部时间处理）
const scopeLabel = computed(() => {
  if (filter.mode === "custom" && (!filter.start || !filter.end)) return "全部时间";
  return timeText.value;
});

const filterChips = computed(() => {
  const chips = [{ key: "time", label: `时间：${timeText.value}` }];
  if (filter.level) {
    chips.push({ key: "level", label: `等级：${filter.level} ${LEVEL_LABELS[filter.level] || ""}`.trim() });
  }
  for (const type of filter.types || []) {
    chips.push({ key: `type:${type}`, label: `类型：${typeLabel(type)}` });
  }
  if (filter.taskId) {
    const task = taskOptions.value.find((item) => taskOptionId(item) === filter.taskId);
    chips.push({ key: "task", label: `任务：${task ? taskOptionLabel(task) : shortId(filter.taskId)}` });
  }
  if (filter.contentType) {
    chips.push({ key: "content", label: `来源：${contentTypeLabel(filter.contentType)}` });
  }
  return chips;
});

const availableTypeOptions = computed(() => {
  // 已选但不在 dimensions 里的类型也要能展示（例如旧数据/手工传入）
  const known = new Set(typeOptions.value.map((item) => item.key));
  const extra = (filter.types || []).filter((type) => !known.has(type)).map((type) => ({ key: type, label: typeLabel(type) }));
  return [...typeOptions.value, ...extra];
});

const queueEmptyText = computed(() => {
  if (filter.level === "SAFE") return "SAFE 安全记录不会产生告警；请切换等级或状态查看。";
  if (queueStatus.value === "open") return "当前筛选下没有待处理风险；可切换到已认领 / 已忽略查看历史。";
  if (queueStatus.value === "ack") return "当前筛选下暂无已认领风险。";
  if (queueStatus.value === "ignored") return "当前筛选下暂无已忽略风险。";
  return "当前筛选下暂无告警记录。";
});

const adviseFindings = computed(() => (Array.isArray(advise.result?.findings) ? advise.result.findings : []));
const adviseRecommendations = computed(() =>
  Array.isArray(advise.result?.recommendations) ? advise.result.recommendations : [],
);
const adviseSourceText = computed(() => {
  const source = advise.result?.source || "";
  return SOURCE_LABELS[source] || (source ? `来源：${source}` : "");
});

// ------------------------------ 筛选联动 ------------------------------
let reloadTimer = null;

function scheduleReload() {
  clearTimeout(reloadTimer);
  reloadTimer = setTimeout(() => {
    loadStats();
    loadQueue();
  }, 180);
}

watch(
  filter,
  () => {
    queue.page = 1;
    scheduleReload();
  },
  { deep: true },
);

watch(queueStatus, () => {
  queue.page = 1;
  loadQueue();
});

function onTimeRadio(value) {
  if (value === "custom") {
    filter.mode = "custom";
    filter.days = null;
    filter.start = customRange.value?.[0] || null;
    filter.end = customRange.value?.[1] || null;
    return;
  }
  if (value === "all") {
    filter.mode = "all";
    filter.days = null;
    filter.start = null;
    filter.end = null;
    return;
  }
  filter.mode = "days";
  filter.days = Number(value);
  filter.start = null;
  filter.end = null;
}

function onCustomRange(value) {
  customRange.value = value || null;
  if (timeRadio.value !== "custom") return;
  filter.mode = "custom";
  filter.start = value?.[0] || null;
  filter.end = value?.[1] || null;
}

function resetFilters() {
  timeRadio.value = "30";
  customRange.value = null;
  filter.mode = "days";
  filter.days = 30;
  filter.start = null;
  filter.end = null;
  filter.level = "";
  filter.types = [];
  filter.taskId = "";
  filter.contentType = "";
  queueStatus.value = "open";
  queue.page = 1;
}

function clearChip(chip) {
  if (chip.key === "time") {
    timeRadio.value = "30";
    filter.mode = "days";
    filter.days = 30;
    filter.start = null;
    filter.end = null;
    customRange.value = null;
    return;
  }
  if (chip.key === "level") filter.level = "";
  else if (chip.key === "task") filter.taskId = "";
  else if (chip.key === "content") filter.contentType = "";
  else if (chip.key.startsWith("type:")) {
    const type = chip.key.slice(5);
    filter.types = (filter.types || []).filter((item) => item !== type);
  }
}

function setLevel(level) {
  if (!level) return;
  filter.level = filter.level === level ? "" : level;
}

function toggleType(type) {
  if (!type) return;
  filter.types = (filter.types || []).includes(type)
    ? filter.types.filter((item) => item !== type)
    : [type];
}

// ------------------------------ 请求 ------------------------------
let statsSeq = 0;
let queueSeq = 0;
let recordsSeq = 0;
let adviseSeq = 0;
let detailSeq = 0;

function nowLabel() {
  return formatDateTime(new Date());
}

async function loadStats() {
  const seq = ++statsSeq;
  statsLoading.value = true;
  statsError.value = "";
  try {
    const body = await api.stats(buildStatsQuery(filter));
    if (seq !== statsSeq) return;
    stats.value = body || {};
    lastUpdated.value = nowLabel();
    await nextTick();
    renderCharts();
    runAdvise();
  } catch (error) {
    if (seq !== statsSeq) return;
    stats.value = null;
    statsError.value = `统计加载失败：${safeError(error)}`;
    disposeCharts();
  } finally {
    if (seq === statsSeq) statsLoading.value = false;
  }
}

async function loadQueue() {
  const seq = ++queueSeq;
  queue.loading = true;
  queue.error = "";
  try {
    const params = { ...buildStatsQuery(filter), page: queue.page, page_size: queue.pageSize };
    if (queueStatus.value !== "all") params.status = queueStatus.value;
    const body = await api.alerts(params);
    if (seq !== queueSeq) return;
    queue.items = Array.isArray(body?.items) ? body.items : [];
    queue.total = numberOr0(body?.total);
  } catch (error) {
    if (seq !== queueSeq) return;
    queue.items = [];
    queue.total = 0;
    queue.error = `风险队列加载失败：${safeError(error)}`;
  } finally {
    if (seq === queueSeq) queue.loading = false;
  }
}

async function loadRecords() {
  const seq = ++recordsSeq;
  records.loading = true;
  records.error = "";
  try {
    const params = { ...buildStatsQuery(filter), page: records.page, page_size: records.pageSize };
    const body = await api.findings(params);
    if (seq !== recordsSeq) return;
    records.items = Array.isArray(body?.items) ? body.items : [];
    records.total = numberOr0(body?.total);
  } catch (error) {
    if (seq !== recordsSeq) return;
    records.items = [];
    records.total = 0;
    records.error = `检测记录加载失败：${safeError(error)}`;
  } finally {
    if (seq === recordsSeq) records.loading = false;
  }
}

async function runAdvise() {
  if (!stats.value) return;
  const seq = ++adviseSeq;
  const current = stats.value;
  advise.loading = true;
  advise.error = "";
  try {
    const riskData = current.risk || {};
    const result = await api.adviseRisk({
      by_level: current.by_level || {},
      by_type: current.by_type || [],
      total_findings: numberOr0(current.total_findings),
      tasks: numberOr0(current.tasks),
      scope: scopeLabel.value,
      total_alerts: numberOr0(riskData.total),
      open_alerts: numberOr0(riskData.open),
      handled_ratio: Number.isFinite(Number(riskData.handled_ratio)) ? Number(riskData.handled_ratio) : null,
      open_by_level: riskData.open_by_level || {},
    });
    if (seq !== adviseSeq) return;
    advise.result = result || {};
  } catch (error) {
    if (seq !== adviseSeq) return;
    advise.result = null;
    advise.error = `风险洞察生成失败：${safeError(error)}`;
  } finally {
    if (seq === adviseSeq) advise.loading = false;
  }
}

function reloadAll() {
  loadStats();
  loadQueue();
  if (records.visible) loadRecords();
}

// ------------------------------ 图表 ------------------------------
const pieEl = ref(null);
const barEl = ref(null);
const trendLevelEl = ref(null);
const trendRiskEl = ref(null);
const histEl = ref(null);
const charts = [];
let barRows = [];
let resizeObserver = null;
let resizeHandler = null;
let stopWatchTheme = null;
let resizeFrame = 0;

function prefersReducedMotion() {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

function chartPalette() {
  return {
    text: cssVar("--ink", "#24344d"),
    muted: cssVar("--muted", "#71839d"),
    border: cssVar("--border", "#dce7f5"),
    panel: cssVar("--panel", "#ffffff"),
    accent: cssVar("--accent", "#5b96ed"),
  };
}

function ensureChart(el) {
  if (!el || !el.offsetWidth || !el.offsetHeight) return null;
  let chart = echarts.getInstanceByDom(el);
  if (!chart) {
    chart = echarts.init(el);
    charts.push(chart);
  }
  return chart;
}

function clearChart(el) {
  if (!el) return;
  const chart = echarts.getInstanceByDom(el);
  chart?.clear();
}

function disposeCharts() {
  for (const chart of charts.splice(0)) {
    try {
      chart.dispose();
    } catch {
      // 容器已经卸载时忽略
    }
  }
}

function renderLevelChart() {
  const el = pieEl.value;
  if (!el) return;
  if (!levelTotal.value) {
    clearChart(el);
    return;
  }
  const chart = ensureChart(el);
  if (!chart) return;
  const palette = chartPalette();
  const total = levelTotal.value;
  const rows = levelRows.value.filter((row) => row.count > 0);
  chart.setOption(
    {
      animation: !prefersReducedMotion(),
      textStyle: { color: palette.text },
      tooltip: {
        trigger: "item",
        formatter: (params) =>
          `${params.marker}${params.name} ${LEVEL_LABELS[params.name] || ""}<br/>${params.value} 条 · 占 ${formatPercent(params.value / total)}`,
      },
      legend: {
        bottom: 0,
        left: "center",
        itemWidth: 10,
        itemHeight: 10,
        textStyle: { color: palette.muted, fontSize: 11 },
      },
      series: [
        {
          type: "pie",
          name: "风险等级",
          radius: ["42%", "66%"],
          center: ["50%", "44%"],
          avoidLabelOverlap: true,
          itemStyle: { borderColor: palette.panel, borderWidth: 2 },
          label: {
            color: palette.text,
            fontSize: 11,
            formatter: (params) => `${params.name} ${params.value} 条\n${formatPercent(params.value / total)}`,
          },
          labelLine: { length: 10, length2: 8, lineStyle: { color: palette.border } },
          data: rows.map((row) => ({
            name: row.level,
            value: row.count,
            itemStyle: { color: LEVEL_COLORS[row.level] },
          })),
        },
      ],
    },
    true,
  );
  if (!chart.__statsLevelClick) {
    chart.on("click", (params) => {
      if (params?.name) setLevel(params.name);
    });
    chart.__statsLevelClick = true;
  }
}

function renderTypeChart() {
  const el = barEl.value;
  if (!el) return;
  const rows = sortedTypes.value.filter((row) => numberOr0(row.count) > 0);
  if (!rows.length) {
    barRows = [];
    clearChart(el);
    return;
  }
  const chart = ensureChart(el);
  if (!chart) return;
  const palette = chartPalette();
  const total = typeTotal.value || 1;
  // 横向条形：数量最多的排在最上面，所以倒序喂给 category 轴
  const display = [...rows].reverse();
  barRows = display;
  const levelText = (row) =>
    ["L3", "L2", "L1"]
      .map((level) => [level, numberOr0(row?.levels?.[level])])
      .filter(([, count]) => count > 0)
      .map(([level, count]) => `${level}×${count}`)
      .join(" ");
  chart.setOption(
    {
      animation: !prefersReducedMotion(),
      textStyle: { color: palette.text },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (params) => {
          const row = display[params[0]?.dataIndex];
          if (!row) return "";
          const levels = LEVELS.map((level) => `${level} ${numberOr0(row.levels?.[level])}`).join(" · ");
          return (
            `${typeLabel(row.type)}<br/>` +
            `记录 ${numberOr0(row.count)} 条（${formatPercent(numberOr0(row.count) / total)}）<br/>` +
            `命中 ${numberOr0(row.hits)} 次<br/>${levels}`
          );
        },
      },
      grid: { left: 8, right: 142, top: 12, bottom: 24, containLabel: true },
      xAxis: {
        type: "value",
        minInterval: 1,
        axisLabel: { color: palette.muted, fontSize: 11 },
        splitLine: { lineStyle: { color: palette.border } },
        axisLine: { show: false },
      },
      yAxis: {
        type: "category",
        data: display.map((row) => typeLabel(row.type)),
        axisTick: { show: false },
        axisLine: { lineStyle: { color: palette.border } },
        axisLabel: { color: palette.text, fontSize: 11, width: 96, overflow: "truncate" },
      },
      series: [
        {
          type: "bar",
          name: "记录数",
          barMaxWidth: 16,
          data: display.map((row) => ({
            value: numberOr0(row.count),
            itemStyle: { color: palette.accent, borderRadius: [0, 4, 4, 0] },
          })),
          label: {
            show: true,
            position: "right",
            color: palette.muted,
            fontSize: 10,
            formatter: (params) => {
              const row = display[params.dataIndex];
              if (!row) return "";
              const levels = levelText(row);
              return `${numberOr0(row.count)} 条 ${formatPercent(numberOr0(row.count) / total)}${levels ? `  ${levels}` : ""}`;
            },
          },
        },
      ],
    },
    true,
  );
  if (!chart.__statsTypeClick) {
    chart.on("click", (params) => {
      const row = barRows[params.dataIndex];
      if (row) toggleType(row.type || row.key);
    });
    chart.__statsTypeClick = true;
  }
}

function trendRange() {
  const now = new Date();
  if (filter.mode === "days") {
    const days = Number(filter.days) || 0;
    if (days > 0) {
      return {
        startISO: toLocalISO(startOfLocalDay(addLocalDays(now, -(days - 1)))),
        endISO: toLocalISO(startOfLocalDay(now)),
      };
    }
  }
  if (filter.mode === "custom") {
    const window = timeWindow(filter, now);
    if (window.start && window.end) {
      // 后端区间是 [start, end)，展示时把结束日还原为最后一天
      return { startISO: toLocalISO(window.start), endISO: toLocalISO(addLocalDays(window.end, -1)) };
    }
    if (window.start) return { startISO: toLocalISO(window.start), endISO: toLocalISO(startOfLocalDay(now)) };
  }
  const byDay = stats.value?.by_day;
  if (Array.isArray(byDay) && byDay.length) {
    return { startISO: byDay[0].date, endISO: byDay[byDay.length - 1].date };
  }
  return { startISO: null, endISO: null };
}

const trendRows = computed(() => {
  const range = trendRange();
  return fillDays(stats.value?.by_day || [], range.startISO, range.endISO);
});
const riskTrendRows = computed(() => {
  const range = trendRange();
  return fillDays(stats.value?.risk?.by_day || [], range.startISO, range.endISO);
});
const hasTrendData = computed(() => trendRows.value.some((row) => row.total > 0));
const hasRiskTrendData = computed(() =>
  riskTrendRows.value.some((row) => row.open + row.ack + row.ignored > 0),
);

function baseTrendOption(palette, dates, series) {
  return {
    animation: !prefersReducedMotion(),
    textStyle: { color: palette.text },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    legend: {
      top: 0,
      right: 0,
      itemWidth: 10,
      itemHeight: 10,
      textStyle: { color: palette.muted, fontSize: 11 },
    },
    grid: { left: 8, right: 12, top: 36, bottom: 24, containLabel: true },
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: palette.border } },
      axisTick: { show: false },
      axisLabel: { color: palette.muted, fontSize: 10, formatter: (value) => String(value).slice(5) },
    },
    yAxis: {
      type: "value",
      minInterval: 1,
      axisLabel: { color: palette.muted, fontSize: 11 },
      splitLine: { lineStyle: { color: palette.border } },
    },
    series,
  };
}

function renderTrendCharts() {
  const palette = chartPalette();
  const rows = trendRows.value;
  if (trendLevelEl.value && hasTrendData.value) {
    const chart = ensureChart(trendLevelEl.value);
    if (chart) {
      const dates = rows.map((row) => row.date);
      chart.setOption(
        baseTrendOption(
          palette,
          dates,
          LEVELS.map((level) => ({
            name: `${level} ${LEVEL_LABELS[level]}`,
            type: "bar",
            stack: "level",
            barMaxWidth: 18,
            itemStyle: { color: LEVEL_COLORS[level] },
            data: rows.map((row) => row[level] || 0),
          })),
        ),
        true,
      );
    }
  }
  const riskRows = riskTrendRows.value;
  if (trendRiskEl.value && hasRiskTrendData.value) {
    const chart = ensureChart(trendRiskEl.value);
    if (chart) {
      const dates = riskRows.map((row) => row.date);
      chart.setOption(
        baseTrendOption(palette, dates, [
          {
            name: "待处理",
            type: "bar",
            stack: "risk",
            barMaxWidth: 18,
            itemStyle: { color: cssVar("--danger", "#cf4b62") },
            data: riskRows.map((row) => row.open),
          },
          {
            name: "已认领",
            type: "bar",
            stack: "risk",
            barMaxWidth: 18,
            itemStyle: { color: cssVar("--warning", "#b87927") },
            data: riskRows.map((row) => row.ack),
          },
          {
            name: "已忽略",
            type: "bar",
            stack: "risk",
            barMaxWidth: 18,
            itemStyle: { color: cssVar("--faint", "#a7b7cc") },
            data: riskRows.map((row) => row.ignored),
          },
        ]),
        true,
      );
    }
  }
}

function scoreColor(label) {
  const lower = Number.parseFloat(String(label).split(/[-–~至]/)[0]);
  if (!Number.isFinite(lower)) return cssVar("--accent", "#5b96ed");
  if (lower >= 0.85) return cssVar("--danger", "#cf4b62");
  if (lower >= 0.55) return cssVar("--warning", "#b87927");
  if (lower >= 0.25) return cssVar("--info", "#5b96ed");
  return cssVar("--success", "#26996e");
}

function renderScoreChart() {
  const el = histEl.value;
  if (!el) return;
  if (!hasScoreData.value) {
    clearChart(el);
    return;
  }
  const chart = ensureChart(el);
  if (!chart) return;
  const palette = chartPalette();
  const entries = scoreEntries.value;
  chart.setOption(
    {
      animation: !prefersReducedMotion(),
      textStyle: { color: palette.text },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (params) => {
          const label = entries[params[0]?.dataIndex]?.[0] || "";
          return `分数区间 ${label}<br/>${params[0]?.value ?? 0} 条检测记录`;
        },
      },
      grid: { left: 8, right: 16, top: 24, bottom: 24, containLabel: true },
      xAxis: {
        type: "category",
        data: entries.map(([label]) => label),
        axisLine: { lineStyle: { color: palette.border } },
        axisTick: { show: false },
        axisLabel: {
          color: palette.muted,
          fontSize: 10,
          interval: 0,
          rotate: entries.length > 8 ? 30 : 0,
        },
      },
      yAxis: {
        type: "value",
        minInterval: 1,
        axisLabel: { color: palette.muted, fontSize: 11 },
        splitLine: { lineStyle: { color: palette.border } },
      },
      series: [
        {
          type: "bar",
          name: "记录数",
          barMaxWidth: 36,
          data: entries.map(([label, count]) => ({
            value: numberOr0(count),
            itemStyle: { color: scoreColor(label), borderRadius: [4, 4, 0, 0] },
          })),
          label: { show: entries.length <= 12, position: "top", color: palette.muted, fontSize: 10 },
        },
      ],
    },
    true,
  );
}

function renderCharts() {
  renderLevelChart();
  renderTypeChart();
  renderTrendCharts();
  renderScoreChart();
}

function resizeCharts() {
  for (const chart of charts) {
    try {
      chart.resize();
    } catch {
      // 容器不可见时忽略
    }
  }
}

watch(sectionTab, async () => {
  await nextTick();
  renderCharts();
  resizeCharts();
});

watch(typeSort, () => {
  renderTypeChart();
});

// ------------------------------ 队列操作 ------------------------------
function rowTypeLabels(row) {
  if (Array.isArray(row?.type_labels) && row.type_labels.length) return row.type_labels;
  const raw = Array.isArray(row?.types) ? row.types : String(row?.types || "").split(",");
  return raw.filter(Boolean).map(typeLabel);
}

function shortId(value) {
  const text = String(value || "");
  return text ? `#${text.slice(0, 8)}${text.length > 8 ? "…" : ""}` : "—";
}

function taskOptionId(task) {
  if (task === null || task === undefined) return "";
  if (typeof task === "string" || typeof task === "number") return String(task);
  return String(task.id || task.task_id || "");
}

function taskOptionLabel(task) {
  if (task === null || task === undefined || task === "") return "—";
  if (typeof task === "string" || typeof task === "number") return shortId(task);
  const id = taskOptionId(task);
  const name = task.source || task.title || task.name || "";
  return name ? `${name} · ${shortId(id)}` : shortId(id);
}

function statusLabel(status) {
  return STATUS_LABELS[status] || status || "未知";
}

function statusTagType(status) {
  return STATUS_TAG_TYPES[status] || "info";
}

function briefMasked(value) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return "—";
  return text.length > 80 ? `${text.slice(0, 80)}…` : text;
}

function onQueuePage(page) {
  queue.page = page;
  loadQueue();
}

function onQueueSize(size) {
  queue.pageSize = size;
  queue.page = 1;
  loadQueue();
}

function onRecordsPage(page) {
  records.page = page;
  loadRecords();
}

function onRecordsSize(size) {
  records.pageSize = size;
  records.page = 1;
  loadRecords();
}

async function openRecords() {
  records.visible = true;
  records.page = 1;
  await loadRecords();
}

async function openDetail(row) {
  drawer.alert = row;
  drawer.finding = null;
  drawer.error = "";
  drawer.visible = true;
  if (!row?.finding_id) {
    drawer.loading = false;
    drawer.error = "该告警未关联检测记录（finding_id 缺失），仅展示告警信息。";
    return;
  }
  const seq = ++detailSeq;
  drawer.loading = true;
  try {
    const finding = await api.finding(row.finding_id);
    if (seq !== detailSeq) return;
    drawer.finding = finding;
  } catch (error) {
    if (seq !== detailSeq) return;
    drawer.error = `检测记录加载失败：${safeError(error)}`;
  } finally {
    if (seq === detailSeq) drawer.loading = false;
  }
}

async function refreshAfterAction() {
  await Promise.all([loadStats(), loadQueue()]);
  if (records.visible) await loadRecords();
}

async function acknowledge(row) {
  try {
    const updated = await api.ackAlert(row.id, "ack");
    ElMessage.success("已认领该风险");
    if (drawer.visible && drawer.alert?.id === row.id && updated) drawer.alert = updated;
    await refreshAfterAction();
  } catch (error) {
    ElMessage.error(`认领失败：${safeError(error)}`);
  }
}

async function ignore(row) {
  const result = await ElMessageBox.prompt("请填写忽略原因（最多 200 字，会写入处置备注）", "忽略该风险", {
    confirmButtonText: "忽略",
    cancelButtonText: "取消",
    inputType: "textarea",
    inputPlaceholder: "例如：已确认为测试数据，无需处理",
    inputValidator: (value) => (value && value.trim() ? true : "请填写忽略原因"),
  }).catch(() => null);
  if (!result) return;
  try {
    const note = String(result.value || "").trim().slice(0, 200);
    const updated = await api.ackAlert(row.id, "ignored", note);
    ElMessage.success("已忽略该风险");
    if (drawer.visible && drawer.alert?.id === row.id && updated) drawer.alert = updated;
    await refreshAfterAction();
  } catch (error) {
    ElMessage.error(`忽略失败：${safeError(error)}`);
  }
}

async function exportTask(row) {
  if (!row?.task_id) {
    ElMessage.warning("该记录缺少任务 ID，无法导出");
    return;
  }
  try {
    await downloadWithAuth(api.taskExportUrl(row.task_id, "json"), `flowmask-${row.task_id}.json`);
    ElMessage.success("已开始导出任务报告（JSON）");
  } catch (error) {
    ElMessage.error(`导出失败：${safeError(error)}`);
  }
}

function focusQueue() {
  nextTick(() => {
    const behavior = prefersReducedMotion() ? "auto" : "smooth";
    document.getElementById("stats-risk-queue")?.scrollIntoView({ behavior, block: "start" });
  });
}

function onMetricCard(key) {
  if (key === "total") {
    openRecords();
    return;
  }
  if (key === "l3") {
    filter.level = "L3";
    return;
  }
  if (key === "open") {
    queueStatus.value = "open";
    queue.page = 1;
    focusQueue();
    return;
  }
  if (key === "handled") {
    queueStatus.value = "all";
    queue.page = 1;
    focusQueue();
  }
}

function showL3Records() {
  filter.level = "L3";
  focusQueue();
}

function openPendingQueue() {
  queueStatus.value = "open";
  focusQueue();
}

function goHash(hash) {
  window.location.hash = hash;
}

function formatInsightItem(item) {
  if (item === null || item === undefined) return "—";
  if (typeof item === "string") return item;
  if (typeof item === "object") {
    return item.text || item.title || item.summary || item.content || JSON.stringify(item);
  }
  return String(item);
}

// ------------------------------ 生命周期 ------------------------------
function onWindowResize() {
  if (resizeFrame) return;
  resizeFrame = requestAnimationFrame(() => {
    resizeFrame = 0;
    resizeCharts();
  });
}

onMounted(async () => {
  await nextTick();
  loadStats();
  loadQueue();

  resizeHandler = onWindowResize;
  window.addEventListener("resize", resizeHandler);

  if (typeof ResizeObserver !== "undefined") {
    resizeObserver = new ResizeObserver(onWindowResize);
    for (const el of [pieEl.value, barEl.value, trendLevelEl.value, trendRiskEl.value, histEl.value]) {
      if (el?.parentElement) resizeObserver.observe(el.parentElement);
    }
  }

  stopWatchTheme = watchTheme(() => {
    disposeCharts();
    nextTick(() => renderCharts());
  });
});

onBeforeUnmount(() => {
  statsSeq += 1;
  queueSeq += 1;
  recordsSeq += 1;
  adviseSeq += 1;
  detailSeq += 1;
  clearTimeout(reloadTimer);
  if (resizeFrame) cancelAnimationFrame(resizeFrame);
  resizeObserver?.disconnect();
  if (resizeHandler) window.removeEventListener("resize", resizeHandler);
  stopWatchTheme?.();
  disposeCharts();
});
</script>

<template>
  <div class="stats-view">
    <PageHeader
      title="统计看板"
      description="检测记录、风险等级与处置进展的统一视图；所有图表与服务端统计共用同一筛选条件。"
      index="STATS"
    >
      <span class="hint">{{ lastUpdated ? `更新于 ${lastUpdated}` : "尚未加载" }}</span>
      <el-button size="small" :loading="statsLoading" @click="reloadAll">刷新</el-button>
    </PageHeader>

    <!-- 统一筛选条 -->
    <section class="panel filter-panel">
      <div class="panel-title">
        筛选条件
        <span class="hint">统计、检测记录、风险队列与风险洞察共用这一份筛选</span>
      </div>
      <div class="filter-grid">
        <div class="filter-item">
          <span class="filter-label">时间范围</span>
          <el-radio-group v-model="timeRadio" size="small" class="filter-radio" @change="onTimeRadio">
            <el-radio-button value="7">近 7 天</el-radio-button>
            <el-radio-button value="30">近 30 天</el-radio-button>
            <el-radio-button value="90">近 90 天</el-radio-button>
            <el-radio-button value="all">全部</el-radio-button>
            <el-radio-button value="custom">自定义</el-radio-button>
          </el-radio-group>
        </div>
        <div class="filter-item">
          <span class="filter-label">风险等级</span>
          <el-select v-model="filter.level" clearable placeholder="全部等级" size="small" class="filter-control">
            <el-option v-for="item in levelOptions" :key="item.value" :value="item.value" :label="`${item.value} ${item.label}`" />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">敏感类型</span>
          <el-select
            v-model="filter.types"
            multiple
            clearable
            filterable
            collapse-tags
            collapse-tags-tooltip
            placeholder="全部类型"
            size="small"
            class="filter-control"
          >
            <el-option v-for="item in availableTypeOptions" :key="item.key" :value="item.key" :label="item.label" />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">任务</span>
          <el-select v-model="filter.taskId" clearable filterable placeholder="全部任务" size="small" class="filter-control">
            <el-option v-for="task in taskOptions" :key="taskOptionId(task)" :value="taskOptionId(task)" :label="taskOptionLabel(task)" />
            <template #empty>
              <div class="empty">暂无可选任务</div>
            </template>
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">数据来源</span>
          <el-select v-model="filter.contentType" clearable placeholder="全部来源" size="small" class="filter-control">
            <el-option v-for="item in contentTypeOptions" :key="item.value" :value="item.value" :label="item.label" />
          </el-select>
        </div>
      </div>
      <div v-if="timeRadio === 'custom'" class="filter-extra">
        <el-date-picker
          v-model="customRange"
          type="daterange"
          size="small"
          unlink-panels
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          @change="onCustomRange"
        />
        <span v-if="!filter.start || !filter.end" class="hint">请选择完整的开始 / 结束日期；未选完整时按全部时间统计。</span>
      </div>
      <div class="active-filters">
        <span class="hint">生效筛选</span>
        <el-tag
          v-for="chip in filterChips"
          :key="chip.key"
          size="small"
          type="info"
          closable
          @close="clearChip(chip)"
        >
          {{ chip.label }}
        </el-tag>
        <el-button link type="primary" size="small" @click="resetFilters">重置</el-button>
        <span v-if="stats?.filter?.trend_limited" class="hint">
          未限定时间范围，趋势图仅展示最近 90 天。
        </span>
      </div>
    </section>

    <div v-if="statsError" class="hint-line error-text stats-error">
      {{ statsError }}
      <el-button link type="primary" size="small" @click="reloadAll">重试</el-button>
    </div>

    <!-- 4 个可点击指标卡 -->
    <div class="stat-grid">
      <button type="button" class="stat-card" :class="{ 'is-active': records.visible }" @click="onMetricCard('total')">
        <span class="stat-top">
          <span class="stat-label">检测数据总量</span>
          <el-tooltip content="筛选时间内被检测的文本片段（findings 行数，含 SAFE）；命中次数与记录条数不是一回事。" placement="top">
            <span class="stat-help" @click.stop>?</span>
          </el-tooltip>
        </span>
        <strong class="stat-value">{{ statsError ? "—" : statsLoading && !stats ? "…" : totalFindings }}<i v-if="!statsError">条</i></strong>
        <span class="stat-sub">
          {{ statsError ? "加载失败，可刷新重试" : `${totalHits} 次敏感命中 · 覆盖 ${tasksCount} 个任务${tasksTotal > tasksCount ? `（全库 ${tasksTotal}）` : ""}` }}
        </span>
      </button>

      <button type="button" class="stat-card" data-tone="danger" :class="{ 'is-active': filter.level === 'L3' }" @click="onMetricCard('l3')">
        <span class="stat-top">
          <span class="stat-label">L3 高危命中</span>
          <el-tooltip content="L3：score ≥ 0.85，或身份证 / 银行卡 / 密钥凭据且置信度 ≥ 0.90 或直接命中强证据。" placement="top">
            <span class="stat-help" @click.stop>?</span>
          </el-tooltip>
        </span>
        <strong class="stat-value">{{ statsError ? "—" : statsLoading && !stats ? "…" : l3Count }}<i v-if="!statsError">条</i></strong>
        <span class="stat-sub">占检测记录 {{ l3RatioText }} · 点击筛选 L3</span>
      </button>

      <button type="button" class="stat-card" data-tone="warning" :class="{ 'is-active': queueStatus === 'open' }" @click="onMetricCard('open')">
        <span class="stat-top">
          <span class="stat-label">待处理风险</span>
          <el-tooltip content="alerts 中状态为 open（待处理）的告警数；已认领只表示有人跟进，不等于完成脱敏。" placement="top">
            <span class="stat-help" @click.stop>?</span>
          </el-tooltip>
        </span>
        <strong class="stat-value">{{ statsError ? "—" : statsLoading && !stats ? "…" : openCount }}<i v-if="!statsError">条</i></strong>
        <span class="stat-sub">{{ statsError ? "加载失败，可刷新重试" : `${openByLevelText} · 点击打开队列` }}</span>
      </button>

      <button type="button" class="stat-card" data-tone="success" :class="{ 'is-active': queueStatus === 'all' }" @click="onMetricCard('handled')">
        <span class="stat-top">
          <span class="stat-label">已处理比例</span>
          <el-tooltip content="已处理比例 =（已认领 + 已忽略）/ 告警总数；告警总数为 0 时显示 —。" placement="top">
            <span class="stat-help" @click.stop>?</span>
          </el-tooltip>
        </span>
        <strong class="stat-value">{{ statsError ? "—" : statsLoading && !stats ? "…" : handledRatioText }}</strong>
        <span class="stat-sub">{{ riskTotal ? `已处理 ${handedCount} / 告警 ${riskTotal} 条` : "当前筛选下暂无告警" }}</span>
      </button>
    </div>

    <!-- 等级饼图 + 真实业务定义 -->
    <section class="panel">
      <div class="panel-title">
        风险等级分布
        <span class="hint">单位：检测记录（findings 片段）；点击扇区按等级筛选</span>
      </div>
      <div class="level-layout">
        <div class="level-chart-wrap">
          <div v-if="!hasLevelData" class="empty-state">
            <strong>{{ statsLoading && !stats ? "正在加载检测记录…" : "当前筛选下暂无检测记录" }}</strong>
            <p v-if="!(statsLoading && !stats)">调整时间范围、等级或类型后重试。</p>
          </div>
          <div v-show="hasLevelData" ref="pieEl" class="chart"></div>
          <p v-if="hasLevelData" class="hint chart-caption">共 {{ levelTotal }} 条检测记录 · L3 占 {{ l3RatioText }}</p>
        </div>
        <div class="level-side">
          <ul class="level-list">
            <li v-for="row in levelRows" :key="row.level" class="level-row" :class="{ active: filter.level === row.level }">
              <button type="button" class="level-button" @click="setLevel(row.level)">
                <RiskIndicator :level="row.level" />
                <b>{{ row.count }}</b>
                <span class="hint">{{ levelTotal ? formatPercent(row.count / levelTotal) : "—" }}</span>
              </button>
            </li>
          </ul>
          <dl class="level-defs">
            <template v-for="def in LEVEL_DEFS" :key="def.level">
              <dt><RiskIndicator :level="def.level" /></dt>
              <dd>{{ def.text }}</dd>
            </template>
          </dl>
        </div>
      </div>
    </section>

    <!-- 敏感类型横向条形图 -->
    <section class="panel">
      <div class="panel-title">
        敏感类型分布
        <span class="hint">点击条形按类型筛选，再次点击取消；标签里的 L3/L2/L1 为各等级记录数</span>
      </div>
      <div class="type-toolbar">
        <span class="hint">排序</span>
        <el-radio-group v-model="typeSort" size="small">
          <el-radio-button value="count">按数量</el-radio-button>
          <el-radio-button value="hits">按命中次数</el-radio-button>
          <el-radio-button value="risk">按高危占比</el-radio-button>
        </el-radio-group>
        <span class="hint">高危占比按 L3 记录数优先排序</span>
      </div>
      <div v-if="!hasTypeData" class="empty-state">
        <strong>{{ statsLoading && !stats ? "正在加载敏感类型…" : "当前筛选下暂无敏感类型命中" }}</strong>
        <p v-if="!(statsLoading && !stats)">可以扩大时间范围，或重置类型 / 等级筛选。</p>
      </div>
      <div v-show="hasTypeData" ref="barEl" class="chart type-chart"></div>
      <p v-if="hasTypeData" class="hint">共 {{ typeTotal }} 条记录，涉及 {{ sortedTypes.length }} 类敏感数据；颜色仅作辅助，等级以文本标注。</p>
    </section>

    <!-- 趋势 / 分数分布 -->
    <section class="panel">
      <div class="panel-title">
        趋势与分数分布
        <span class="hint">按本地日期补零；没有数据的日期显示 0，而不是断线或空白坐标系</span>
      </div>
      <el-tabs v-model="sectionTab">
        <el-tab-pane label="风险趋势" name="trend">
          <div class="chart-grid">
            <div class="pane">
              <div class="pane-head">按等级的新增检测记录</div>
              <div v-if="!hasTrendData" class="empty">当前筛选下暂无趋势数据</div>
              <div v-show="hasTrendData" ref="trendLevelEl" class="chart"></div>
            </div>
            <div class="pane">
              <div class="pane-head">风险处置趋势（待处理 / 已认领 / 已忽略）</div>
              <div v-if="!hasRiskTrendData" class="empty">当前筛选下暂无处置数据</div>
              <div v-show="hasRiskTrendData" ref="trendRiskEl" class="chart"></div>
            </div>
          </div>
          <p class="hint-line">已认领仅表示有人跟进，不等于完成脱敏；已处理比例 =（已认领 + 已忽略）/ 告警总数。</p>
        </el-tab-pane>
        <el-tab-pane label="敏感度分数分布" name="score">
          <div v-if="!hasScoreData" class="empty">当前筛选下暂无分数分布数据</div>
          <div v-show="hasScoreData" ref="histEl" class="chart"></div>
          <p class="hint-line">横轴为评分区间，纵轴为检测记录数；L3 / L2 / L1 阈值分别为 0.85 / 0.55 / 0.25。</p>
        </el-tab-pane>
      </el-tabs>
    </section>

    <!-- 风险处置队列 -->
    <section id="stats-risk-queue" class="panel">
      <div class="panel-title">
        风险处置队列
        <span class="hint">与上方筛选联动；服务端分页，操作会写入审计</span>
      </div>
      <div class="queue-toolbar">
        <el-radio-group v-model="queueStatus" size="small">
          <el-radio-button v-for="item in STATUS_OPTIONS" :key="item.value" :value="item.value">{{ item.label }}</el-radio-button>
        </el-radio-group>
        <span class="hint">共 {{ queue.total }} 条 · 范围 {{ timeText }}</span>
        <el-button size="small" :loading="queue.loading" @click="loadQueue">刷新队列</el-button>
      </div>
      <div v-if="queue.error" class="hint-line error-text">
        {{ queue.error }}
        <el-button link type="primary" size="small" @click="loadQueue">重试</el-button>
      </div>
      <el-table v-loading="queue.loading" :data="queue.items" :empty-text="queueEmptyText" row-key="id" size="small">
        <el-table-column label="等级" width="118">
          <template #default="{ row }">
            <RiskIndicator :level="row.level" />
          </template>
        </el-table-column>
        <el-table-column label="敏感类型" min-width="150">
          <template #default="{ row }">
            <template v-if="rowTypeLabels(row).length">
              <span v-for="label in rowTypeLabels(row)" :key="label" class="chip pale queue-chip">{{ label }}</span>
            </template>
            <span v-else class="hint">—</span>
          </template>
        </el-table-column>
        <el-table-column label="来源 / 任务" min-width="190">
          <template #default="{ row }">
            <div class="queue-source">{{ row.source || "—" }}</div>
            <div class="hint">{{ shortId(row.task_id) }} · {{ contentTypeLabel(row.content_type) }}</div>
          </template>
        </el-table-column>
        <el-table-column label="检测时间" width="158">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag size="small" effect="plain" :type="statusTagType(row.status)">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <div class="row-actions">
              <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
              <el-button v-if="row.status === 'open'" link type="primary" size="small" @click="acknowledge(row)">认领</el-button>
              <el-button v-if="row.status !== 'ignored'" link type="warning" size="small" @click="ignore(row)">忽略</el-button>
              <el-button link size="small" @click="exportTask(row)">导出</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
      <div class="table-footer">
        <span>{{ queue.error ? "加载失败" : `第 ${queue.page} 页 · 共 ${queue.total} 条` }}</span>
        <el-pagination
          background
          layout="sizes, prev, pager, next"
          :total="queue.total"
          :current-page="queue.page"
          :page-size="queue.pageSize"
          :page-sizes="[10, 20, 50]"
          @current-change="onQueuePage"
          @size-change="onQueueSize"
        />
      </div>
    </section>

    <!-- 风险洞察 -->
    <section class="panel">
      <div class="panel-title">
        风险洞察
        <span class="hint">基于当前筛选的聚合统计生成，不上传原文；建议只用于导航，不会自动执行处置</span>
      </div>
      <div class="toolbar advise-toolbar">
        <span v-if="adviseSourceText" class="source-badge" :data-source="advise.result?.source || ''">
          {{ adviseSourceText }}
        </span>
        <el-button size="small" :loading="advise.loading" :disabled="!stats" @click="runAdvise">重新解读</el-button>
        <span class="hint">范围：{{ scopeLabel }}</span>
      </div>
      <div v-if="advise.error" class="hint-line error-text">
        {{ advise.error }}
        <el-button link type="primary" size="small" @click="runAdvise">重试</el-button>
      </div>
      <div v-if="advise.loading && !advise.result" class="empty">正在生成风险洞察…</div>
      <template v-else-if="advise.result">
        <div class="pane advise-summary">
          <div class="pane-head">结论</div>
          <div class="pane-body advise-text">{{ advise.result.summary || "暂无结论" }}</div>
        </div>
        <div class="compare advise-grid">
          <div class="pane">
            <div class="pane-head">发现</div>
            <ul v-if="adviseFindings.length" class="advise-list">
              <li v-for="(item, index) in adviseFindings" :key="index">{{ formatInsightItem(item) }}</li>
            </ul>
            <div v-else class="empty">暂无额外发现</div>
          </div>
          <div class="pane">
            <div class="pane-head">建议</div>
            <ul v-if="adviseRecommendations.length" class="advise-list">
              <li v-for="(item, index) in adviseRecommendations" :key="index">{{ formatInsightItem(item) }}</li>
            </ul>
            <div v-else class="empty">暂无建议</div>
          </div>
        </div>
        <div class="toolbar advise-actions">
          <el-button size="small" @click="showL3Records">查看 L3 记录</el-button>
          <el-button size="small" @click="openPendingQueue">打开待处理队列</el-button>
          <el-button size="small" @click="goHash('#rules')">前往识别规则</el-button>
          <el-button size="small" @click="goHash('#tasks')">任务与审计</el-button>
        </div>
        <p class="hint-line">已认领不等于完成脱敏；请结合队列备注与趋势判断处理效果。</p>
      </template>
      <div v-else-if="!advise.loading" class="empty">当前筛选下暂无风险洞察</div>
    </section>

    <!-- 检测记录抽屉 -->
    <el-drawer v-model="records.visible" title="检测记录（当前筛选）" size="min(760px, 100vw)" :destroy-on-close="true">
      <p class="hint-line">共 {{ records.total }} 条检测记录；包含 SAFE 安全记录，展示脱敏后的文本片段。</p>
      <div v-if="records.error" class="hint-line error-text">
        {{ records.error }}
        <el-button link type="primary" size="small" @click="loadRecords">重试</el-button>
      </div>
      <el-table v-loading="records.loading" :data="records.items" size="small" empty-text="当前筛选下暂无检测记录">
        <el-table-column label="等级" width="112">
          <template #default="{ row }"><RiskIndicator :level="row.level" /></template>
        </el-table-column>
        <el-table-column label="敏感类型" min-width="128">
          <template #default="{ row }">
            <span v-for="label in rowTypeLabels(row)" :key="label" class="chip pale queue-chip">{{ label }}</span>
          </template>
        </el-table-column>
        <el-table-column label="位置" min-width="120">
          <template #default="{ row }"><span class="mono queue-path">{{ row.path || "—" }}</span></template>
        </el-table-column>
        <el-table-column label="任务" width="112">
          <template #default="{ row }"><span class="mono">{{ shortId(row.task_id) }}</span></template>
        </el-table-column>
        <el-table-column label="检测时间" width="156">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="脱敏预览" min-width="200">
          <template #default="{ row }"><span class="masked-cell">{{ briefMasked(row.masked) }}</span></template>
        </el-table-column>
      </el-table>
      <div class="table-footer">
        <span>第 {{ records.page }} 页 · 共 {{ records.total }} 条</span>
        <el-pagination
          background
          layout="prev, pager, next"
          :total="records.total"
          :current-page="records.page"
          :page-size="records.pageSize"
          @current-change="onRecordsPage"
        />
      </div>
    </el-drawer>

    <!-- 风险详情抽屉 -->
    <el-drawer v-model="drawer.visible" title="风险详情" size="min(480px, 100vw)" :destroy-on-close="true">
      <template v-if="drawer.alert">
        <div class="drawer-head">
          <RiskIndicator :level="drawer.alert.level" />
          <el-tag size="small" effect="plain" :type="statusTagType(drawer.alert.status)">
            {{ statusLabel(drawer.alert.status) }}
          </el-tag>
          <span class="hint">告警 {{ shortId(drawer.alert.id) }}</span>
        </div>
        <dl class="kv">
          <dt>敏感类型</dt>
          <dd>
            <span v-for="label in rowTypeLabels(drawer.alert)" :key="label" class="chip pale queue-chip">{{ label }}</span>
            <span v-if="!rowTypeLabels(drawer.alert).length">—</span>
          </dd>
          <dt>检测时间</dt>
          <dd>{{ formatDateTime(drawer.alert.created_at) }}</dd>
          <dt>任务</dt>
          <dd class="mono">{{ drawer.alert.task_id || "—" }}</dd>
          <dt>片段位置</dt>
          <dd class="mono">{{ drawer.alert.path || "—" }}</dd>
          <dt>风险分数</dt>
          <dd>{{ drawer.alert.score ?? "—" }}</dd>
          <dt>处置建议</dt>
          <dd>{{ drawer.alert.suggestion || "—" }}</dd>
        </dl>

        <div class="drawer-section">
          <strong>脱敏预览</strong>
          <div v-if="drawer.loading" class="empty">检测记录加载中…</div>
          <div v-else-if="drawer.error" class="hint-line error-text">{{ drawer.error }}</div>
          <template v-else-if="drawer.finding">
            <div class="detail-content">{{ drawer.finding.masked || "—" }}</div>
            <dl class="kv finding-meta">
              <dt>检测链路</dt>
              <dd class="mono">{{ drawer.finding.model_chain || "—" }}</dd>
              <dt>命中规则</dt>
              <dd class="mono">{{ drawer.finding.rule_id || "—" }}</dd>
              <dt>模型状态</dt>
              <dd>{{ drawer.finding.degraded ? "已降级（规则回退）" : "正常" }}</dd>
              <dt>命中次数</dt>
              <dd>{{ drawer.finding.hits ?? "—" }}</dd>
              <dt>原文哈希</dt>
              <dd class="mono">{{ drawer.finding.original_hash || "—" }}</dd>
            </dl>
          </template>
          <div v-else class="empty">暂无检测记录详情</div>
        </div>

        <div class="drawer-section">
          <strong>处理信息</strong>
          <dl class="kv">
            <dt>状态</dt>
            <dd>{{ statusLabel(drawer.alert.status) }}</dd>
            <dt>处理人</dt>
            <dd>{{ drawer.alert.handled_by || "—" }}</dd>
            <dt>处理时间</dt>
            <dd>{{ drawer.alert.handled_at ? formatDateTime(drawer.alert.handled_at) : "—" }}</dd>
            <dt>备注</dt>
            <dd>{{ drawer.alert.note || "—" }}</dd>
          </dl>
        </div>

        <div class="drawer-actions">
          <el-button v-if="drawer.alert.status === 'open'" type="primary" size="small" @click="acknowledge(drawer.alert)">认领</el-button>
          <el-button v-if="drawer.alert.status !== 'ignored'" type="warning" plain size="small" @click="ignore(drawer.alert)">忽略</el-button>
          <el-button size="small" @click="exportTask(drawer.alert)">导出任务报告</el-button>
        </div>
      </template>
      <div v-else class="empty">请选择一条告警查看详情。</div>
    </el-drawer>
  </div>
</template>

<style scoped>
.stats-view {
  min-width: 0;
}

.filter-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 14px 18px;
  align-items: end;
}

.filter-item {
  display: flex;
  flex-direction: column;
  gap: 7px;
  min-width: 0;
}

.filter-label {
  color: var(--muted);
  font-size: 12px;
}

.filter-control {
  width: 100%;
}

.filter-radio {
  flex-wrap: wrap;
  row-gap: 6px;
}

.filter-extra {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-top: 14px;
}

.active-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed var(--border);
}

.active-filters :deep(.el-tag),
.active-filters .el-tag {
  max-width: 100%;
}

.stats-error {
  margin: -4px 0 16px;
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 20px;
}

.stat-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px 18px;
  text-align: left;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  color: var(--ink);
  font: inherit;
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease;
}

.stat-card:hover {
  border-color: var(--accent);
  background: var(--elevated);
}

.stat-card:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.stat-card.is-active {
  border-color: var(--accent);
  box-shadow: inset 0 0 0 1px var(--accent);
}

.stat-top {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
}

.stat-label {
  color: var(--muted);
  font-size: 12px;
}

.stat-help {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border: 1px solid var(--border);
  border-radius: 50%;
  color: var(--muted);
  font-size: 10px;
  cursor: help;
}

.stat-value {
  font: 28px/1.5 var(--font-mono);
  overflow-wrap: anywhere;
}

.stat-value i {
  margin-left: 4px;
  color: var(--muted);
  font: 12px/1 var(--font-ui);
  font-style: normal;
}

.stat-card[data-tone="danger"] .stat-value {
  color: var(--danger);
}

.stat-card[data-tone="warning"] .stat-value {
  color: var(--warning);
}

.stat-card[data-tone="success"] .stat-value {
  color: var(--success);
}

.stat-sub {
  color: var(--muted);
  font-size: 11px;
  line-height: 1.6;
}

.level-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(0, 1fr);
  gap: 20px;
  align-items: start;
}

.level-chart-wrap {
  min-width: 0;
}

.chart-caption {
  margin: 8px 0 0;
  text-align: center;
}

.level-side {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}

.level-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 0;
  margin: 0;
  list-style: none;
}

.level-button {
  display: flex;
  gap: 10px;
  align-items: center;
  width: 100%;
  padding: 7px 10px;
  text-align: left;
  background: var(--bg);
  border: 1px solid transparent;
  border-radius: 8px;
  color: var(--ink);
  font: inherit;
  cursor: pointer;
}

.level-button:hover {
  border-color: var(--border);
  background: var(--elevated);
}

.level-row.active .level-button {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.level-button b {
  margin-left: auto;
  font: 14px var(--font-mono);
}

.level-button .hint {
  min-width: 48px;
  text-align: right;
}

.level-defs {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 6px 10px;
  padding-top: 12px;
  margin: 0;
  border-top: 1px dashed var(--border);
  color: var(--muted);
  font-size: 11px;
}

.level-defs dt {
  margin: 0;
}

.level-defs dd {
  margin: 0;
  line-height: 1.7;
}

.type-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-bottom: 12px;
}

.type-chart {
  height: 340px;
}

.chart-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
}

.queue-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-bottom: 12px;
}

.queue-toolbar .hint {
  margin-left: auto;
}

.queue-chip {
  margin: 0 4px 4px 0;
}

.queue-source {
  color: var(--ink);
  font-size: 12px;
}

.queue-path {
  font-size: 11px;
}

.row-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 8px;
  align-items: center;
}

.masked-cell {
  display: block;
  max-width: 280px;
  overflow: hidden;
  color: var(--secondary);
  font: 11px/1.6 var(--font-mono);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.drawer-head {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-bottom: 14px;
}

.drawer-section {
  margin-top: 18px;
}

.drawer-section > strong {
  display: block;
  margin-bottom: 8px;
  font-size: 13px;
}

.drawer-section .kv {
  padding: 12px;
  background: var(--bg);
  border-radius: 8px;
}

.finding-meta {
  margin-top: 12px;
}

.drawer-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 20px;
}

.advise-toolbar {
  margin: 0 0 12px;
}

.source-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 9px;
  border-radius: 999px;
  background: var(--info-soft);
  color: var(--info);
  font-size: 11px;
}

.source-badge[data-source="local_llm"] {
  background: var(--success-soft);
  color: var(--success);
}

.source-badge[data-source="degraded"] {
  background: var(--warning-soft);
  color: var(--warning);
}

.advise-text {
  max-height: 240px;
  min-height: 0;
  font: 13px/1.9 var(--font-ui);
  white-space: pre-wrap;
}

.advise-grid {
  margin-top: 14px;
}

.advise-grid .pane-body,
.advise-list {
  font: 12px/1.9 var(--font-ui);
  color: var(--secondary);
}

.advise-list {
  padding: 14px 20px 14px 34px;
  margin: 0;
}

.advise-actions {
  margin-top: 14px;
}

.advise-actions .el-button + .el-button {
  margin-left: 0;
}

@media (max-width: 1200px) {
  .stat-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .level-layout,
  .chart-grid {
    grid-template-columns: minmax(0, 1fr);
  }

  .queue-toolbar .hint {
    margin-left: 0;
  }
}

@media (max-width: 720px) {
  .stat-grid,
  .filter-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
