<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { estimateWrappedHeight, needsScroll } from "../comparison";
const props = defineProps({ lines: Array, side: String, selected: Number, wrap: Boolean });
const emit = defineEmits(["position", "select"]);
const LINE_HEIGHT = 26;
const viewport = ref(null), top = ref(0), height = ref(500), width = ref(0), heights = ref({});
let observer, frame = 0, release = 0, programmatic = false, estimates = null;
// 行号栏按最大行数定宽，避免 6~7 位行号逐行撑宽导致正文左边缘抖动。
const gutter = computed(() => 55 + Math.max(0, String(props.lines.length).length - 5) * 8);
function estimatedHeight(index) {
  if (!props.wrap) return LINE_HEIGHT;
  if (!estimates) estimates = new Map();
  if (!estimates.has(index)) {
    const text = props.lines[index]?.text || "";
    const available = Math.max(120, (width.value || 600) - gutter.value - 28);
    estimates.set(index, estimateWrappedHeight(text, available, LINE_HEIGHT));
  }
  return estimates.get(index);
}
function lineHeight(index) { return heights.value[index] || estimatedHeight(index); }
const offsets = computed(() => {
  const values = new Array(props.lines.length + 1);
  values[0] = 0;
  for (let i = 0; i < props.lines.length; i++) values[i + 1] = values[i] + lineHeight(i);
  return values;
});
function indexAt(y) {
  let lo = 0, hi = props.lines.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (offsets.value[mid + 1] <= y) lo = mid + 1; else hi = mid; }
  return Math.min(lo, Math.max(0, props.lines.length - 1));
}
const visible = computed(() => {
  const start = Math.max(0, indexAt(top.value) - 6), end = Math.min(props.lines.length, indexAt(top.value + height.value) + 8);
  return props.lines.slice(start, end).map((line, i) => ({ line, index: start + i }));
});
function measure() {
  if (!viewport.value) return;
  height.value = viewport.value.clientHeight;
  const updates = { ...heights.value };
  let changed = false;
  viewport.value.querySelectorAll("[data-line]").forEach(node => {
    const size = node.getBoundingClientRect().height, key = node.dataset.line;
    if (updates[key] !== size) { updates[key] = size; changed = true; }
  });
  if (changed) heights.value = updates;
}
function scroll() {
  if (frame) return;
  frame = requestAnimationFrame(() => {
    frame = 0;
    top.value = viewport.value.scrollTop;
    if (!programmatic) {
      const index = indexAt(top.value);
      emit("position", index + (top.value - offsets.value[index]) / (lineHeight(index) || LINE_HEIGHT));
    }
  });
}
// 行几何：逻辑行可带小数，用于在两个文档行数不一致时按比例落到中间位置。
function geometry(value) {
  if (!props.lines.length) return null;
  const index = Math.min(Math.max(0, Math.floor(value)), props.lines.length - 1);
  const size = lineHeight(index);
  return { offset: Math.max(0, offsets.value[index] + (value - index) * size), lineHeight: size };
}
async function locate(value) {
  if (!props.lines.length) return;
  const target = Number.isFinite(value) ? Math.max(0, value) : 0;
  programmatic = true;
  // 换行模式下行高按估算起算；滚动后测量实际行高并校正，最多两轮即可收敛。
  for (let attempt = 0; attempt < 3; attempt++) {
    top.value = geometry(target).offset;
    await nextTick();
    if (!viewport.value) break;
    viewport.value.scrollTop = top.value;
    if (!props.wrap) break;
    await nextTick();
    measure();
    if (Math.abs(geometry(target).offset - top.value) < 1) break;
  }
  cancelAnimationFrame(release);
  release = requestAnimationFrame(() => { release = requestAnimationFrame(() => { programmatic = false; }); });
}
// 对侧联动：目标仍在视口内时不滚动，避免点击高亮把当前阅读位置顶走。
function ensureVisible(value) {
  const target = geometry(value);
  if (!viewport.value || !target) return;
  if (needsScroll(target.offset, target.lineHeight, viewport.value.scrollTop, viewport.value.clientHeight)) locate(value);
}
watch(() => props.wrap, () => { estimates = null; heights.value = {}; nextTick(measure); });
watch(() => props.lines, () => { estimates = null; heights.value = {}; nextTick(measure); });
watch(visible, () => nextTick(measure));
onMounted(() => {
  observer = new ResizeObserver(() => {
    const nextWidth = viewport.value.clientWidth;
    if (nextWidth !== width.value) { width.value = nextWidth; estimates = null; heights.value = {}; }
    nextTick(measure);
  });
  observer.observe(viewport.value);
  width.value = viewport.value.clientWidth;
  measure();
});
onBeforeUnmount(() => { observer?.disconnect(); cancelAnimationFrame(frame); cancelAnimationFrame(release); });
defineExpose({ locate, ensureVisible });
</script>
<template>
  <section class="document-pane" :class="side" :style="{ '--gutter': gutter + 'px' }">
    <div class="pane-heading">{{ side === 'original' ? '原始文档' : '脱敏后的文档' }}</div>
    <div ref="viewport" class="document-scroll" tabindex="0" :aria-label="side === 'original' ? '原始文档内容' : '脱敏文档内容'" @scroll="scroll">
      <div class="line-space" :style="{ height: offsets.at(-1) + 'px' }">
        <div v-for="{ line, index } in visible" :key="index" :data-line="index" class="document-line" :class="{ wrap }" :style="{ top: offsets[index] + 'px' }">
          <span class="line-number" aria-hidden="true">{{ index + 1 }}</span><span class="line-text"><template v-for="(part, i) in line.parts" :key="i"><button v-if="part.id !== undefined" class="change" :class="{ selected: selected === part.id }" :aria-label="'查看差异 ' + (part.id + 1)" @click="emit('select', part.id)">{{ part.text || '∅' }}</button><span v-else>{{ part.text }}</span></template></span>
        </div>
      </div>
    </div>
  </section>
</template>
<style scoped>
.document-pane { min-width: 0; min-height: 0; display: flex; flex-direction: column; border: 1px solid var(--border); border-radius: var(--radius-lg); overflow: hidden; background: var(--panel); }
.pane-heading { padding: 10px 16px; background: var(--pane-head); border-bottom: 1px solid var(--border); font-weight: 600; }
.document-scroll { flex: 1; overflow: auto; position: relative; }
.line-space { position: relative; min-width: 100%; }
.document-line { position: absolute; min-width: 100%; display: flex; align-items: flex-start; font: 13px/26px var(--font-mono); white-space: pre; }
.line-number { position: sticky; left: 0; flex: 0 0 var(--gutter, 55px); text-align: right; padding-right: 12px; color: var(--muted); background: var(--panel); user-select: none; }
.line-text { padding-right: 16px; }
.wrap { width: 100%; white-space: pre-wrap; overflow-wrap: anywhere; }
.wrap .line-text { min-width: 0; flex: 1; }
.change { display: inline; border: 0; border-radius: 3px; padding: 0; font: inherit; color: inherit; white-space: inherit; overflow-wrap: inherit; cursor: pointer; background: var(--danger-soft); }
.masked .change { background: var(--accent-soft); }
.change.selected, .change:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
</style>
