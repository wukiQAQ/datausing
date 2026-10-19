<script setup>
import { computed, onMounted, ref } from "vue";
import DetectView from "./views/DetectView.vue";
import TasksView from "./views/TasksView.vue";
import StatsView from "./views/StatsView.vue";
import RulesView from "./views/RulesView.vue";
import DynamicView from "./views/DynamicView.vue";
import TrafficView from "./views/TrafficView.vue";
import { api } from "./api";

const tabs = [
  { key: "detect", label: "即时检测 · 左右对照", comp: DetectView },
  { key: "traffic", label: "流量导入 · pcap", comp: TrafficView },
  { key: "tasks", label: "任务与审计链", comp: TasksView },
  { key: "stats", label: "统计看板", comp: StatsView },
  { key: "rules", label: "识别规则", comp: RulesView },
  { key: "dynamic", label: "动态脱敏 · 授权切换", comp: DynamicView },
];

const hashTab = (location.hash || '').replace('#', '');
const active = ref(tabs.some((tab) => tab.key === hashTab) ? hashTab : 'detect');
const current = computed(() => (tabs.find((t) => t.key === active.value) || tabs[0]).comp);
const health = ref(null);
const healthError = ref("");

function selectTab(key) {
  active.value = key;
  location.hash = key;
}

onMounted(async () => {
  try {
    health.value = await api.health();
  } catch (err) {
    healthError.value = String(err);
  }
});
</script>

<template>
  <el-container class="layout">
    <el-header class="topbar">
      <div class="brand">FlowMask<span>分级脱敏与审计溯源 · 本地一体机（数据不出机）</span></div>
      <div class="status">
        <template v-if="health">
          v{{ health.version }} · 运行 {{ health.uptime_s }}s · L2 {{ health.enable_l2 ? "已启用" : "未启用" }} ·
          L3 {{ health.enable_l3 ? "已启用" : "未启用" }}
        </template>
        <template v-else-if="healthError">服务未连接：{{ healthError }}</template>
        <template v-else>正在连接服务…</template>
      </div>
    </el-header>
    <el-container>
      <el-aside width="216px" class="side">
        <el-menu :default-active="active" @select="selectTab">
          <el-menu-item v-for="tab in tabs" :key="tab.key" :index="tab.key">{{ tab.label }}</el-menu-item>
        </el-menu>
      </el-aside>
      <el-main class="main">
        <component :is="current" />
      </el-main>
    </el-container>
  </el-container>
</template>
