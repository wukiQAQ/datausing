<script setup>
import {
  CollapsibleContent,
  CollapsibleRoot,
  CollapsibleTrigger,
} from "reka-ui";
import AppIcon from "./AppIcon.vue";
defineProps({ title: { type: String, default: "高级检测选项" } });
</script>
<template>
  <CollapsibleRoot class="advanced-options">
    <CollapsibleTrigger class="disclosure-trigger"
      ><AppIcon name="rules" :size="16" />{{ title
      }}<AppIcon class="disclosure-caret" name="down" :size="14"
    /></CollapsibleTrigger>
    <CollapsibleContent class="disclosure-content"
      ><div class="advanced-grid"><slot /></div
    ></CollapsibleContent>
  </CollapsibleRoot>
</template>
