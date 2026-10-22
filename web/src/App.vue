<script setup>
import { computed, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import DetectView from "./views/DetectView.vue";
import TasksView from "./views/TasksView.vue";
import BatchView from "./views/BatchView.vue";
import StatsView from "./views/StatsView.vue";
import RulesView from "./views/RulesView.vue";
import DynamicView from "./views/DynamicView.vue";
import TrafficView from "./views/TrafficView.vue";
import { api } from "./api";
import { operator, setOperator, syncThemeFromServer, theme, toggleTheme } from "./theme";

const tabs = [
  { key: "detect", label: "即时检测 · 左右对照", comp: DetectView },
  { key: "batch", label: "批量检测 · 多文件", comp: BatchView },
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
// 多用户演示：操作人可切换，主题按操作人存在服务端
const knownOperators = ref(["local"]);

const isDark = computed({
  get: () => theme.value === "dark",
  set: (value) => {
    if ((value ? "dark" : "light") !== theme.value) toggleTheme();
  },
});

function selectTab(key) {
  active.value = key;
  location.hash = key;
}

async function onChangeOperator(value) {
  const result = await setOperator(value);
  ElMessage.success(`已切换用户「${operator.value}」：主题 ${result.theme}`);
}

async function loadOperators() {
  try {
    const body = await api.preferenceUsers();
    const names = body.users.map((item) => item.operator_id);
    knownOperators.value = Array.from(new Set(["local", ...names, operator.value]));
  } catch {
    /* 拿不到列表不影响本机使用 */
  }
}

onMounted(async () => {
  try {
    health.value = await api.health();
  } catch (err) {
    healthError.value = String(err);
  }
  await syncThemeFromServer();
  await loadOperators();
});
</script>

<template>
  <el-container class="layout">
    <el-header class="topbar">
      <div class="brand">FlowMask<span>分级脱敏与审计溯源 · 本地一体机（数据不出机）</span></div>
      <div class="topbar-right">
        <div class="status">
          <template v-if="health">
            v{{ health.version }} · 运行 {{ health.uptime_s }}s · L2 {{ health.enable_l2 ? "已启用" : "未启用" }} ·
            L3 {{ health.enable_l3 ? "已启用" : "未启用" }}
          </template>
          <template v-else-if="healthError">服务未连接：{{ healthError }}</template>
          <template v-else>正在连接服务…</template>
        </div>
        <!-- 操作人：主题偏好按人存在服务端，切换即可演示多用户各记各的 -->
        <div class="operator" title="切换操作人：主题偏好按用户分别保存在服务端">
          <span>用户</span>
          <el-select
            :model-value="operator"
            size="small"
            filterable
            allow-create
            default-first-option
            style="width: 118px"
            @change="onChangeOperator"
          >
            <el-option v-for="name in knownOperators" :key="name" :label="name" :value="name" />
          </el-select>
        </div>
        <!-- 主题：服务端按用户记住，未选择时跟随系统/服务端默认值 -->
        <div class="theme-toggle" :title="isDark ? '切换到浅色模式' : '切换到深色模式'">
          <span>{{ isDark ? "🌙" : "☀️" }}</span>
          <el-switch v-model="isDark" size="small" active-text="深色" inactive-text="浅色" />
        </div>
      </div>
    </el-header>
    <el-container>
      <el-aside width="216px" class="side">
        <el-menu :default-active="active" @select="selectTab">
          <el-menu-item v-for="tab in tabs" :key="tab.key" :index="tab.key">{{ tab.label }}</el-menu-item>
        </el-menu>
      </el-aside>
      <el-main class="main">
        <!-- key 里带主题：切主题时重建当前页，避免 Element Plus 组件残留上一套配色 -->
        <component :is="current" :key="`${active}-${theme}`" />
      </el-main>
    </el-container>
  </el-container>
</template>
