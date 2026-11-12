<script setup>
import {
  PhShieldCheck,
  PhScan,
  PhFiles,
  PhArrowsLeftRight,
  PhListChecks,
  PhChartDonut,
  PhSlidersHorizontal,
  PhLockKey,
  PhGearSix,
  PhCaretLeft,
  PhCaretRight,
  PhSun,
  PhMoon,
  PhArrowRight,
  PhUploadSimple,
  PhPlay,
  PhCopy,
  PhCheck,
  PhTerminalWindow,
  PhInfo,
  PhWarning,
  PhFileText,
  PhMagnifyingGlass,
  PhTrash,
  PhCaretDown,
  PhArrowClockwise,
  PhDownloadSimple,
} from "@phosphor-icons/vue";
defineProps({ name: String, size: { type: Number, default: 18 } });
const icons = {
  shield: PhShieldCheck,
  detect: PhScan,
  batch: PhFiles,
  traffic: PhArrowsLeftRight,
  tasks: PhListChecks,
  stats: PhChartDonut,
  rules: PhSlidersHorizontal,
  dynamic: PhLockKey,
  settings: PhGearSix,
  left: PhCaretLeft,
  right: PhCaretRight,
  sun: PhSun,
  moon: PhMoon,
  arrow: PhArrowRight,
  upload: PhUploadSimple,
  play: PhPlay,
  copy: PhCopy,
  check: PhCheck,
  terminal: PhTerminalWindow,
  info: PhInfo,
  warning: PhWarning,
  file: PhFileText,
  search: PhMagnifyingGlass,
  trash: PhTrash,
  down: PhCaretDown,
  refresh: PhArrowClockwise,
  download: PhDownloadSimple,
};
</script>
<template>
  <component
    :is="icons[name] || icons.file"
    :size="size"
    :weight="'regular'"
    aria-hidden="true"
    class="app-icon"
  />
</template>
