<script setup>
import { LEVEL_LABELS } from "../api";
defineProps({
  level: { type: String, default: "SAFE" },
  count: [Number, String],
});
</script>
<template>
  <span class="risk-badge" :data-level="level"
    ><i />{{ level }} <span>{{ LEVEL_LABELS[level] || level }}</span
    ><b v-if="count !== undefined">{{ count }}</b></span
  >
</template>
