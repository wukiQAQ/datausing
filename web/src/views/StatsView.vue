<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";
import * as echarts from "echarts";
import { ElMessage } from "element-plus";
import { api, LEVEL_COLORS } from "../api";
import { cssVar, watchTheme } from "../theme";

const stats = ref(null);
const pieEl = ref(null);
const barEl = ref(null);
const histEl = ref(null);
const charts = [];
let resizeHandler = null;
let stopWatchTheme = null;

function ensureChart(el) {
  if (!el) return null;
  let chart = echarts.getInstanceByDom(el);
  if (!chart) {
    chart = echarts.init(el);
    charts.push(chart);
  }
  return chart;
}

// ECharts 是 canvas 渲染，颜色取自当前主题变量；切换主题时重建图表
function axisTheme() {
  return {
    text: cssVar("--ink", "#0f172a"),
    muted: cssVar("--muted", "#64748b"),
    border: cssVar("--border", "#e2e8f0"),
  };
}

function disposeCharts() {
  charts.forEach((chart) => chart.dispose());
  charts.length = 0;
}

function render() {
  const data = stats.value;
  if (!data) return;
  const palette = axisTheme();

  const pie = ensureChart(pieEl.value);
  if (pie) {
    const rows = Object.entries(data.by_level || {})
      .filter(([, count]) => count > 0)
      .map(([level, count]) => ({
        name: `${level}`,
        value: count,
        itemStyle: { color: LEVEL_COLORS[level] || palette.muted },
      }));
    pie.setOption(
      {
        textStyle: { color: palette.text },
        tooltip: { trigger: "item" },
        legend: { bottom: 0, textStyle: { color: palette.muted } },
        series: [
          {
            type: "pie",
            radius: ["42%", "68%"],
            center: ["50%", "45%"],
            label: { formatter: "{b}: {c} ({d}%)", color: palette.text },
            data: rows.length ? rows : [{ name: "暂无数据", value: 1, itemStyle: { color: palette.border } }],
          },
        ],
      },
      true
    );
  }

  const bar = ensureChart(barEl.value);
  if (bar) {
    const rows = (data.by_type || []).map((item) => item.key || item.type || item.name);
    const counts = (data.by_type || []).map((item) => item.count ?? item.value ?? 0);
    bar.setOption(
      {
        textStyle: { color: palette.text },
        tooltip: { trigger: "axis" },
        grid: { left: 42, right: 16, top: 24, bottom: 48 },
        xAxis: {
          type: "category",
          data: rows,
          axisLabel: { rotate: 30, color: palette.muted },
          axisLine: { lineStyle: { color: palette.border } },
        },
        yAxis: {
          type: "value",
          axisLabel: { color: palette.muted },
          splitLine: { lineStyle: { color: palette.border } },
        },
        series: [{ type: "bar", data: counts, barMaxWidth: 34, itemStyle: { color: cssVar("--accent", "#0d9488") } }],
      },
      true
    );
  }

  const hist = ensureChart(histEl.value);
  if (hist) {
    const entries = Object.entries(data.score_histogram || {});
    hist.setOption(
      {
        textStyle: { color: palette.text },
        tooltip: { trigger: "axis" },
        grid: { left: 42, right: 16, top: 24, bottom: 40 },
        xAxis: {
          type: "category",
          data: entries.map(([key]) => key),
          axisLabel: { color: palette.muted },
          axisLine: { lineStyle: { color: palette.border } },
        },
        yAxis: {
          type: "value",
          axisLabel: { color: palette.muted },
          splitLine: { lineStyle: { color: palette.border } },
        },
        series: [
          {
            type: "bar",
            data: entries.map(([, count]) => count),
            barMaxWidth: 46,
            itemStyle: { color: "#ea580c" },
          },
        ],
      },
      true
    );
  }
}

async function load() {
  try {
    stats.value = await api.stats();
    render();
  } catch (err) {
    ElMessage.error(`加载统计失败：${err}`);
  }
}

onMounted(() => {
  load();
  resizeHandler = () => charts.forEach((chart) => chart.resize());
  window.addEventListener("resize", resizeHandler);
  // 换主题时旧 canvas 颜色不会自动更新，干脆重建
  stopWatchTheme = watchTheme(() => {
    disposeCharts();
    render();
  });
});

onBeforeUnmount(() => {
  if (resizeHandler) window.removeEventListener("resize", resizeHandler);
  if (stopWatchTheme) stopWatchTheme();
  disposeCharts();
});
</script>

<template>
  <div class="panel">
    <div class="panel-title">
      统计看板
      <span class="hint" v-if="stats">
        命中 {{ stats.total_findings }} 条 · 任务 {{ stats.tasks }} 个（全部任务聚合）
      </span>
    </div>
    <el-button size="small" @click="load">刷新</el-button>
    <div class="chart-row" style="margin-top: 14px">
      <div class="pane">
        <div class="pane-head">分级分布（饼图）</div>
        <div ref="pieEl" class="chart"></div>
      </div>
      <div class="pane">
        <div class="pane-head">敏感类别 TopN（柱状图）</div>
        <div ref="barEl" class="chart"></div>
      </div>
      <div class="pane">
        <div class="pane-head">敏感度分数直方图</div>
        <div ref="histEl" class="chart"></div>
      </div>
    </div>
  </div>
</template>
