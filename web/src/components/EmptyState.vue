<script setup>
import AppIcon from "./AppIcon.vue";
defineProps({
  title: String,
  description: String,
  icon: { type: String, default: "detect" },
});
</script>
<template>
  <div class="empty-state">
    <AppIcon :name="icon" :size="28" /><strong>{{ title }}</strong>
    <p v-if="description">{{ description }}</p>
    <slot />
  </div>
</template>
