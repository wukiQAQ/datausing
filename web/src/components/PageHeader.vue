<script setup>
defineProps({ title: String, description: String, index: String });
</script>
<template>
  <header class="page-heading">
    <div>
      <div class="page-eyebrow">
        FLOWMASK <span>/</span> {{ index || "WORKSPACE" }}
      </div>
      <h1>{{ title }}</h1>
      <p>{{ description }}</p>
    </div>
    <div class="page-heading-actions"><slot /></div>
  </header>
</template>
