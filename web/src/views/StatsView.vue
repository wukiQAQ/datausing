<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";
import * as echarts from "echarts";
import { ElMessage } from "element-plus";
import { api, LEVEL_COLORS } from "../api";

const stats = ref(null);
const pieEl = ref(null);
const barEl = ref(null);
const histEl = ref(null);
const charts = [];
let resizeHandler = null;

function ensureChart(el) {
  if (!el) return null;
  let chart = echarts.getInstanceByDom(el);
  if (!chart) {
    chart = echarts.init(el);
    charts.push(chart);
  }
  return chart;
}

function render() {
  const data = stats.value;
  if (!data) return;

  const pie = ensureChart(pieEl.value);
  if (pie) {
    const rows = Object.entries(data.by_level || {})
      .filter(([, count]) => count > 0)
      .map(([level, count]) => ({
        name: `${level}`,
        value: count,
        itemStyle: { color: LEVEL_COLORS[level] || "#64748b" },
      }));
    pie.setOption(
      {
        tooltip: { trigger: "item" },
        legend: { bottom: 0 },
        series: [
          {
            type: "pie",
            radius: ["42%", "68%"],
            center: ["50%", "45%"],
            label: { formatter: "{b}: {c} ({d}%)" },
            data: rows.length ? rows : [{ name: "暂无数据", value: 1, itemStyle: { color: "#e2e8f0" } }],
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
        tooltip: { trigger: "axis" },
        grid: { left: 42, right: 16, top: 24, bottom: 48 },
        xAxis: { type: "category", data: rows, axisLabel: { rotate: 30 } },
        yAxis: { type: "value" },
        series: [{ type: "bar", data: counts, barMaxWidth: 34, itemStyle: { color: "#0d9488" } }],
      },
      true
    );
  }

  const hist = ensureChart(histEl.value);
  if (hist) {
    const entries = Object.entries(data.score_histogram || {});
    hist.setOption(
      {
        tooltip: { trigger: "axis" },
        grid: { left: 42, right: 16, top: 24, bottom: 40 },
        xAxis: { type: "category", data: entries.map(([key]) => key) },
        yAxis: { type: "value" },
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
});

onBeforeUnmount(() => {
  if (resizeHandler) window.removeEventListener("resize", resizeHandler);
  charts.forEach((chart) => chart.dispose());
  charts.length = 0;
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
