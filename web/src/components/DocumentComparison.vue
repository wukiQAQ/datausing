<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from "vue";
import { ElMessage } from "element-plus";
import { api, downloadWithAuth } from "../api";
import { prepareComparison, mapLine } from "../comparison";
import { typeLabel } from "../stats";
import DocumentPane from "./DocumentPane.vue";
import RiskIndicator from "./RiskIndicator.vue";

// 与服务端脱敏策略键一致；批量任务实际出现的是 block/partial，其余留给后续策略复用。
const STRATEGY_LABELS = {
  block: "全屏蔽",
  partial: "部分遮蔽",
  hash: "加盐哈希",
  generalize: "泛化",
  pseudonym: "化名",
  encrypt: "可逆加密",
};
// 只根据状态码给固定文案，不回显服务端错误体，避免把输入反射进界面。
const ERROR_TEXT = {
  401: "无权读取原始文档，请检查登录凭据。",
  403: "当前会话无权读取该任务原文。",
  404: "任务不存在或已被清理。",
  409: "任务未完成、脱敏失败或策略已变化，请重新检测。",
  410: "对比快照已过期、重启失效或未保留，请重新提交文件。",
  413: "文件过大，无法打开对比，请缩小文件后重试。",
  503: "对比服务繁忙（临时快照容量不足），请稍后重试或重新提交文件。",
};

const props = defineProps({ taskId: String, filename: String });
const emit = defineEmits(["close"]);
const media = window.matchMedia("(max-width: 760px)");
const open = ref(true), loading = ref(true), error = ref(""), model = shallowRef(null);
const sync = ref(true), wrap = ref(false), selected = ref(-1), details = ref(false);
const narrow = ref(media.matches), autoNarrow = ref(media.matches);
// 视图下拉的展开状态由组件事件维护：ESC 先交给下拉收起，再按一次才关弹窗。
const selectOpen = ref(false);
// 窄屏直接落单栏；从宽屏缩窄时自动切到「仅查看原文」，并记住以便恢复双栏。
const view = ref(media.matches ? "original" : "split");
const original = ref(null), masked = ref(null);
const effectiveView = computed(() => narrow.value && view.value === "split" ? "original" : view.value);
const active = computed(() => model.value?.changes[selected.value]);
const controller = new AbortController();
const returnFocus = document.activeElement;
let closing = false;
function entityLabel(value) {
  const text = String(value || "").trim();
  if (!text) return "敏感信息";
  return text.split(",").map((key) => typeLabel(key.trim())).join("、");
}
function anySelectOpen() { return selectOpen.value; }
function onSelectVisible(value) { selectOpen.value = value; }
function escape(event) {
  if (event.key !== "Escape" || event.defaultPrevented) return;
  if (anySelectOpen()) return; // 先让下拉自己收起，再按一次才关弹窗
  event.preventDefault();
  event.stopPropagation();
  requestClose();
}
function resize() {
  const next = media.matches;
  if (next === narrow.value) return;
  narrow.value = next;
  if (next) {
    if (view.value === "split") { view.value = "original"; autoNarrow.value = true; }
  } else if (autoNarrow.value) {
    view.value = "split";
    autoNarrow.value = false;
  }
}
// 用户手动改视图后不再自动恢复双栏，尊重当前选择。
function onViewChange() { autoNarrow.value = false; }
async function load() {
  try {
    const payload = await api.taskComparison(props.taskId, controller.signal);
    if (!controller.signal.aborted) model.value = prepareComparison(payload);
  } catch (err) {
    if (controller.signal.aborted) return;
    error.value = ERROR_TEXT[err.status] || "无法获取对比文档，请稍后重试。";
  } finally { loading.value = false; }
}
// source 为空表示差异导航（两侧同时定位）；否则是点击某侧高亮，只把对侧带到可视区。
function select(id, showDetails = true, source = "") {
  selected.value = id;
  details.value = showDetails;
  const change = model.value?.changes[id];
  if (!change) return;
  if (!source) {
    original.value?.locate(change.originalLine);
    masked.value?.locate(change.maskedLine);
  } else if (source === "original") {
    masked.value?.ensureVisible(change.maskedLine);
  } else {
    original.value?.ensureVisible(change.originalLine);
  }
}
function navigate(delta) {
  const count = model.value.changes.length;
  if (count) select(selected.value < 0 ? (delta > 0 ? 0 : count - 1) : (selected.value + delta + count) % count, details.value);
}
function position(side, value) {
  if (!sync.value || effectiveView.value !== "split") return;
  const target = side === "original" ? masked : original;
  target.value?.locate(mapLine(model.value.anchors, side, Math.max(0, value)));
}
async function download() {
  try { await downloadWithAuth(api.taskComparisonDownloadUrl(props.taskId), "flowmask-masked.txt"); }
  catch { ElMessage.error("下载失败，快照可能已过期或当前会话无权访问。"); }
}
function cleanup() { controller.abort(); model.value = null; details.value = false; selected.value = -1; }
// 交给 Element Plus 走完关闭过渡；真正销毁由 @closed 触发，避免卸载时丢焦点恢复。
function requestClose() {
  if (closing) return;
  closing = true;
  cleanup();
  open.value = false;
}
function beforeClose(done) { requestClose(); done(); }
function onClosed() {
  emit("close");
  nextTick(() => { if (returnFocus?.isConnected) returnFocus.focus(); });
}
// 切换单栏/双栏后恢复选中项的定位与同步（pane 用 v-show 保挂载，滚动位置不丢）。
watch(effectiveView, async () => {
  await nextTick();
  const change = model.value?.changes[selected.value];
  if (!change) return;
  original.value?.locate(change.originalLine);
  masked.value?.locate(change.maskedLine);
}, { flush: "post" });
onMounted(() => { document.addEventListener("keydown", escape, true); resize(); media.addEventListener("change", resize); load(); });
onBeforeUnmount(() => { document.removeEventListener("keydown", escape, true); cleanup(); media.removeEventListener("change", resize); });
</script>
<template>
  <el-dialog v-model="open" class="document-comparison" width="90vw" top="6vh" destroy-on-close :close-on-click-modal="false" :before-close="beforeClose" @closed="onClosed">
    <template #header="{ titleId }"><div :id="titleId" class="compare-title">文档对比 · {{ filename }}</div></template>
    <div v-if="loading" class="compare-state" role="status">正在加载受权限保护的文档快照…</div>
    <el-alert v-else-if="error" :title="error" type="warning" :closable="false" show-icon />
    <template v-else-if="model">
      <div class="compare-toolbar">
        <el-tag type="success" size="small">{{ model.status === 'done' ? '检测完成 · 已脱敏' : (model.status || '状态未知') }}</el-tag>
        <span :title="'统计全部检测到的命中（不受结果最低级别筛选影响）'">{{ model.hits }} 次敏感命中 · {{ model.changes.length }} 处差异</span>
        <el-tag v-if="model.coverage !== 'complete'" type="warning" size="small">检测覆盖不完整</el-tag>
        <el-select v-model="view" aria-label="文档对比视图" style="width: 155px" size="small" @change="onViewChange" @visible-change="onSelectVisible">
          <el-option v-if="!narrow" label="左右对比" value="split" />
          <el-option label="仅查看原文" value="original" />
          <el-option label="仅查看脱敏文档" value="masked" />
        </el-select>
        <el-checkbox v-model="sync" :disabled="effectiveView !== 'split'">同步滚动</el-checkbox>
        <el-checkbox v-model="wrap">自动换行</el-checkbox>
        <el-button size="small" :disabled="!model.changes.length" @click="navigate(-1)">上一处</el-button>
        <span class="diff-count" aria-live="polite" :aria-label="`第 ${selected + 1} 处，共 ${model.changes.length} 处差异`">{{ selected + 1 }} / {{ model.changes.length }}</span>
        <el-button size="small" :disabled="!model.changes.length" @click="navigate(1)">下一处</el-button>
        <el-button size="small" type="primary" plain @click="download">下载脱敏文档</el-button>
      </div>
      <p class="compare-note">按解析片段展示，保留片段内换行；结构化及 Office/PDF 文档不还原原文件排版。下载为与右侧内容一致的 UTF-8 文本。</p>
      <div v-if="!model.changes.length" class="compare-note" role="status">未发现需要替换的敏感信息，两侧内容一致。</div>
      <div class="compare-body" :class="{ single: effectiveView !== 'split' }">
        <DocumentPane v-show="effectiveView !== 'masked'" ref="original" :lines="model.original" side="original" :selected="selected" :wrap="wrap" @select="(id) => select(id, true, 'original')" @position="position('original', $event)" />
        <DocumentPane v-show="effectiveView !== 'original'" ref="masked" :lines="model.masked" side="masked" :selected="selected" :wrap="wrap" @select="(id) => select(id, true, 'masked')" @position="position('masked', $event)" />
        <aside v-if="details && active" class="field-details" role="status" aria-label="字段信息">
          <el-button class="detail-close" link aria-label="关闭字段信息" @click="details = false">关闭</el-button>
          <div class="detail-row"><span class="detail-label">类别</span><span :title="active.type || ''">{{ entityLabel(active.type) }}</span></div>
          <div class="detail-row"><span class="detail-label">实际检测级别</span><RiskIndicator v-if="active.level" :level="active.level" /><span v-else>未知</span></div>
          <div class="detail-row"><span class="detail-label">策略</span><span>{{ STRATEGY_LABELS[active.strategy] || active.strategy }}</span></div>
          <div class="detail-row"><span class="detail-label">状态</span><span>{{ active.status }}</span></div>
          <div v-if="active.full_segment">无精确位置证据，按安全策略屏蔽整个片段。</div>
        </aside>
      </div>
    </template>
  </el-dialog>
</template>
<style scoped>
.compare-title { font-size: 17px; font-weight: 600; overflow-wrap: anywhere; padding-right: 24px; }
.compare-toolbar { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
.compare-toolbar .el-button { margin-left: 0; }
.compare-note { font-size: 12px; color: var(--muted); margin: 10px 0; }
.compare-state { padding: 32px; color: var(--muted); }
.diff-count { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
.compare-body { position: relative; display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; flex: 1; min-height: 0; }
.compare-body.single { grid-template-columns: minmax(0, 1fr); }
.field-details { position: absolute; right: 16px; bottom: 16px; width: min(260px, calc(100% - 32px)); padding: 12px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--panel); color: var(--ink); font-size: 12px; line-height: 1.8; overflow-wrap: anywhere; }
.detail-close { float: right; }
.detail-row { display: flex; align-items: center; gap: 8px; }
.detail-label { color: var(--muted); flex: 0 0 auto; }
</style>
<style>
.el-dialog.document-comparison { height: 86vh; margin-bottom: 0; display: flex; flex-direction: column; border-radius: var(--radius-lg); }
.document-comparison .el-dialog__body { flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: auto; }
</style>
