<script setup>
import {
  computed,
  defineAsyncComponent,
  onMounted,
  onBeforeUnmount,
  ref,
} from "vue";
import { ElMessage } from "element-plus";
const DetectView = defineAsyncComponent(() => import("./views/DetectView.vue"));
const TasksView = defineAsyncComponent(() => import("./views/TasksView.vue"));
const BatchView = defineAsyncComponent(() => import("./views/BatchView.vue"));
const StatsView = defineAsyncComponent(() => import("./views/StatsView.vue"));
const RulesView = defineAsyncComponent(() => import("./views/RulesView.vue"));
const DynamicView = defineAsyncComponent(
  () => import("./views/DynamicView.vue"),
);
const TrafficView = defineAsyncComponent(
  () => import("./views/TrafficView.vue"),
);
const SettingsView = defineAsyncComponent(
  () => import("./views/SettingsView.vue"),
);
import AppIcon from "./components/AppIcon.vue";
import { api } from "./api";
import {
  operator,
  setOperator,
  syncThemeFromServer,
  theme,
  toggleTheme,
} from "./theme";
const groups = [
  {
    label: "工作台",
    name: "WORKSPACE",
    tabs: [
      { key: "detect", label: "实时检测", comp: DetectView },
      { key: "batch", label: "批量检测", comp: BatchView },
      { key: "traffic", label: "流量导入", comp: TrafficView },
    ],
  },
  {
    label: "管理",
    name: "MANAGEMENT",
    tabs: [
      { key: "tasks", label: "任务与审计", comp: TasksView },
      { key: "stats", label: "统计看板", comp: StatsView },
      { key: "rules", label: "识别规则", comp: RulesView },
    ],
  },
  {
    label: "系统",
    name: "SYSTEM",
    tabs: [
      { key: "dynamic", label: "动态脱敏与授权", comp: DynamicView },
      { key: "settings", label: "设置与策略", comp: SettingsView },
    ],
  },
];
const tabs = groups.flatMap((group) => group.tabs);
const active = ref("detect");
const mobileNavOpen = ref(false);
function readHash() {
  const key = location.hash.slice(1);
  active.value = tabs.some((tab) => tab.key === key) ? key : "detect";
  mobileNavOpen.value = false;
}
function focusMain() {
  document.getElementById("main-content")?.focus();
}
readHash();
const current = computed(() => tabs.find((tab) => tab.key === active.value));
const currentGroup = computed(() =>
  groups.find((group) => group.tabs.some((tab) => tab.key === active.value)),
);
const collapsed = ref(false);
try {
  collapsed.value = localStorage.getItem("flowmask-sidebar") === "collapsed";
} catch {
  /* 可用性不依赖存储 */
}
function collapse() {
  collapsed.value = !collapsed.value;
  try {
    localStorage.setItem(
      "flowmask-sidebar",
      collapsed.value ? "collapsed" : "expanded",
    );
  } catch {
    /* 忽略 */
  }
}
const health = ref(null);
const healthError = ref(false);
const refreshing = ref(false);
const knownOperators = ref(["local"]);
const isLocal = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
const serviceReady = computed(() => health.value?.status === "ok");
const serviceLabel = computed(() =>
  healthError.value
    ? "服务未连接"
    : health.value?.status === "ok"
      ? isLocal
        ? "本地服务已连接"
        : "服务已连接"
      : health.value ? "服务异常" : "正在连接",
);
let timer;
async function refreshHealth() {
  if (refreshing.value) return;
  refreshing.value = true;
  try {
    health.value = await api.health();
    healthError.value = false;
  } catch {
    health.value = null;
    healthError.value = true;
  } finally {
    refreshing.value = false;
  }
}
async function changeOperator(value) {
  const result = await setOperator(value);
  knownOperators.value = Array.from(
    new Set([...knownOperators.value, operator.value]),
  );
  ElMessage.success(
    `已切换操作人 ${operator.value} · ${result.source === "offline" ? "使用本机主题偏好" : "主题偏好已同步"}`,
  );
}
onMounted(async () => {
  window.addEventListener("hashchange", readHash);
  refreshHealth();
  timer = setInterval(refreshHealth, 30000);
  await syncThemeFromServer();
  try {
    const body = await api.preferenceUsers();
    knownOperators.value = Array.from(
      new Set([
        "local",
        ...body.users.map((item) => item.operator_id),
        operator.value,
      ]),
    );
  } catch {
    /* 不影响本机使用 */
  }
});
onBeforeUnmount(() => {
  clearInterval(timer);
  window.removeEventListener("hashchange", readHash);
});
</script>
<template>
  <div class="workspace-shell" :class="{ 'sidebar-collapsed': collapsed }">
    <a class="skip-link" href="#main-content" @click.prevent="focusMain"
      >跳到工作区</a
    >
    <aside class="workspace-sidebar" :class="{ 'mobile-nav-open': mobileNavOpen }" aria-label="主导航">
      <a class="workspace-brand" href="#detect" title="FlowMask 工作台"
        ><span class="brand-mark"><AppIcon name="shield" :size="25" /></span
        ><span class="brand-word"
          >FlowMask<small>数据安全工作台</small></span
        ></a
      >
      <button class="mobile-menu" type="button" aria-controls="workspace-nav" :aria-expanded="mobileNavOpen"
        @click="mobileNavOpen = !mobileNavOpen">
        <AppIcon name="down" :size="16" />导航
      </button>
      <div class="workspace-label">
        <span class="workspace-avatar">F</span
        ><span>本地工作空间<small>隐私与数据安全</small></span>
      </div>
      <nav id="workspace-nav" class="nav-groups">
        <section
          v-for="group in groups"
          :key="group.name"
          class="nav-group"
          :aria-label="group.label"
        >
          <div class="nav-group-label">
            {{ group.label }}<small>{{ group.name }}</small>
          </div>
          <a
            v-for="tab in group.tabs"
            :key="tab.key"
            :href="`#${tab.key}`"
            class="nav-item"
            :class="{ active: active === tab.key }"
            :aria-current="active === tab.key ? 'page' : undefined"
            :title="collapsed ? tab.label : undefined"
            @click="mobileNavOpen = false"
            ><AppIcon :name="tab.key" /><span>{{ tab.label }}</span
            ><i v-if="active === tab.key" class="nav-active-dot"
          /></a>
        </section>
      </nav>
      <div class="sidebar-bottom">
        <div class="local-note">
          <AppIcon name="dynamic" :size="16" /><span
            >本地优先，谨慎流转<small>即时检测不保存原文</small></span
          >
        </div>
        <button
          class="sidebar-collapse"
          :aria-label="collapsed ? '展开导航栏' : '折叠导航栏'"
          :aria-expanded="!collapsed"
          @click="collapse"
        >
          <AppIcon :name="collapsed ? 'right' : 'left'" :size="16" /><span
            >折叠导航</span
          ><kbd>‹</kbd>
        </button>
      </div>
    </aside>
    <div class="workspace-body">
      <header class="workspace-topbar">
        <div class="breadcrumbs">
          <span>{{ currentGroup.label }}</span
          ><span>/</span><strong>{{ current.label }}</strong>
        </div>
        <div class="global-tools">
          <el-popover placement="bottom-end" :width="304" trigger="click">
            <template #reference
              ><button
                class="service-status"
                :class="{ online: serviceReady, offline: healthError || (health && !serviceReady) }"
                aria-label="查看服务与引擎状态"
              >
                <i />{{ serviceLabel
                }}<AppIcon name="down" :size="12" /></button
            ></template>
            <div class="system-popover">
              <strong>服务与检测引擎</strong>
              <dl class="kv">
                <dt>服务状态</dt>
                <dd>{{ serviceLabel }}</dd>
                <dt>服务版本</dt>
                <dd>{{ health?.version || "—" }}</dd>
                <dt>运行时间</dt>
                <dd>{{ health ? `${health.uptime_s}s` : "—" }}</dd>
                <dt>L2 语义配置</dt>
                <dd>
                  {{
                    health ? (health.enable_l2 ? "已启用" : "未启用") : "未知"
                  }}
                </dd>
                <dt>L3 复核配置</dt>
                <dd>
                  {{
                    health ? (health.enable_l3 ? "已启用" : "未启用") : "未知"
                  }}
                </dd>
                <dt>NER</dt>
                <dd>
                  {{ health ? (health.ner ? health.ner_backend || "已就绪" : "未就绪") : "未知" }}
                </dd>
                <dt>OCR</dt>
                <dd>{{ health ? (health.ocr ? "已就绪" : "未就绪") : "未知" }}</dd>
              </dl>
              <p class="hint">
                L2/L3 配置不代表模型在线，实际使用与降级以检测结果为准。
              </p>
              <el-button
                size="small"
                :loading="refreshing"
                @click="refreshHealth"
                >刷新状态</el-button
              >
            </div>
          </el-popover>
          <el-popover placement="bottom-end" :width="280" trigger="click">
            <template #reference>
              <button class="icon-button" aria-label="使用帮助" title="使用帮助">
                <AppIcon name="info" :size="18" />
              </button>
            </template>
            <div class="system-popover">
              <strong>工作台使用帮助</strong>
              <p>粘贴文本或上传文件，核验脱敏副本后复制或导出。检测快捷键为 Ctrl / ⌘ + Enter。</p>
              <a href="#tasks">查看任务与审计</a>
            </div>
          </el-popover>
          <span class="tool-divider" />
          <el-select
            class="operator-select"
            :model-value="operator"
            aria-label="当前操作人"
            size="small"
            filterable
            allow-create
            default-first-option
            @change="changeOperator"
            ><el-option
              v-for="name in knownOperators"
              :key="name"
              :value="name"
              :label="name"
          /></el-select>
          <button
            class="icon-button"
            :title="theme === 'dark' ? '切换浅色主题' : '切换深色主题'"
            :aria-label="theme === 'dark' ? '切换浅色主题' : '切换深色主题'"
            @click="toggleTheme"
          >
            <AppIcon :name="theme === 'dark' ? 'sun' : 'moon'" />
          </button>
        </div>
      </header>
      <main id="main-content" class="workspace-main" tabindex="-1">
        <component :is="current.comp" :key="active" />
      </main>
      <footer class="workspace-footer">
        <span
          ><i class="status-dot" :class="{ online: serviceReady, offline: healthError || (health && !serviceReady) }" />{{
            serviceLabel
          }}</span
        ><span
          >检测结果请审查后导出
          <span class="footer-separator">·</span> FlowMask{{
            health ? ` v${health.version}` : ""
          }}</span
        >
      </footer>
    </div>
  </div>
</template>
