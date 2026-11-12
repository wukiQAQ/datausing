<script setup>
import { computed, ref } from "vue";
import AppIcon from "./AppIcon.vue";
const props = defineProps({
  title: String,
  subtitle: String,
  html: String,
  text: { type: String, default: "" },
  masked: Boolean,
});
const emit = defineEmits(["scroll", "hit"]);
const viewport = ref(null);
const numbers = computed(() =>
  Array.from(
    { length: Math.max(1, props.text.split("\n").length) },
    (_, i) => i + 1,
  ).join("\n"),
);
function onHit(event) {
  const hit = event.target.closest("[data-finding]");
  if (hit) {
    event.preventDefault();
    emit("hit", Number(hit.dataset.finding));
  }
}
function setScroll(ratio, horizontal) {
  const el = viewport.value;
  if (!el) return;
  const top = ratio * Math.max(0, el.scrollHeight - el.clientHeight);
  if (Math.abs(el.scrollTop - top) > 1) el.scrollTop = top;
  if (Math.abs(el.scrollLeft - horizontal) > 1) el.scrollLeft = horizontal;
}
function scroll() {
  const el = viewport.value;
  emit(
    "scroll",
    el.scrollTop / Math.max(1, el.scrollHeight - el.clientHeight),
    el.scrollLeft,
  );
}
defineExpose({ setScroll });
</script>
<template>
  <section
    class="editor-panel"
    :class="{ 'is-masked': masked }"
    :aria-label="title"
  >
    <header class="editor-head">
      <span
        ><AppIcon :name="masked ? 'shield' : 'file'" :size="16" /><strong>{{
          title
        }}</strong
        ><small>{{ subtitle }}</small></span
      ><slot name="actions" />
    </header>
    <div
      v-if="text"
      ref="viewport"
      class="editor-viewport"
      tabindex="0"
      :aria-label="`${title}，可滚动查看`"
      @scroll="scroll"
      @click="onHit"
      @keydown.enter="onHit"
      @keydown.space="onHit"
    >
      <pre class="line-numbers" aria-hidden="true">{{ numbers }}</pre>
      <pre class="editor-code" v-html="html" />
    </div>
    <div v-else class="editor-placeholder">
      <slot
        ><AppIcon name="file" :size="24" /><span
          >检测完成后在此查看{{ title }}</span
        ></slot
      >
    </div>
  </section>
</template>
