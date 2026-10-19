<script setup>
import { computed, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { api, LEVEL_COLORS, LEVEL_LABELS } from "../api";
import { maskedDocumentText, renderHighlight } from "../highlight";

const SAMPLE = `客户张三，手机 13800138000，身份证 11010519491231002X，收货地址：福建省福州市鼓楼区五四路 100 号。`;

const text = ref(SAMPLE);
const maskLevel = ref("");
const minLevel = ref("SAFE");
const enableL2 = ref(false);
const enableL3 = ref(false);
const loading = ref(false);
const result = ref(null);
const file = ref(null);

const findings = computed(() => (result.value ? result.value.findings : []));
const byLevel = computed(() => (result.value ? result.value.by_level : {}));
const originalHtml = computed(() => (result.value ? renderHighlight(text.value, flattenSpans()) : ""));
const maskedHtml = computed(() => escape(maskedDocumentText(result.value?.masked_document)));

function escape(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function flattenSpans() {
  const spans = [];
  for (const item of findings.value) {
    for (const span of item.spans || []) spans.push(span);
  }
  return spans;
}

async function runDetect() {
  if (!text.value.trim()) {
    ElMessage.warning("先输入要检测的文本");
    return;
  }
  loading.value = true;
  try {
    result.value = await api.detectText({
      text: text.value,
      enable_l2: enableL2.value,
      enable_l3: enableL3.value,
      mask_level: maskLevel.value || null,
      min_level: minLevel.value,
    });
  } catch (err) {
    ElMessage.error(`检测失败：${err}`);
  } finally {
    loading.value = false;
  }
}

async function upload(fileEntry) {
  if (!fileEntry) return;
  loading.value = true;
  try {
    result.value = await api.detectFile(fileEntry);
    text.value = `${fileEntry.name}（${result.value.content_type}，${result.value.segments} 个片段）`;
    ElMessage.success("文件解析并检测完成");
  } catch (err) {
    ElMessage.error(`上传失败：${err}`);
  } finally {
    loading.value = false;
  }
}

function onFileChange(uploadFile) {
  file.value = uploadFile.raw;
  upload(uploadFile.raw);
}

function levelChip(level) {
  return { background: LEVEL_COLORS[level] || "#64748b" };
}

onMounted(() => {
  // 打开即跑一遍示例：答辩/演示时页面一落地就有内容
  runDetect();
});
</script>

<template>
  <div class="panel">
    <div class="panel-title">
      内容检测（文本 / JSON / CSV / SQL 自动识别）
      <span class="hint">命中处黄色高亮，右侧是可直接写库/导出的脱敏副本</span>
    </div>
    <el-input v-model="text" type="textarea" :rows="5" placeholder="粘贴文本，或点击下方上传文件" />
    <div class="toolbar">
      <el-select v-model="maskLevel" placeholder="脱敏级别（按实际）" style="width: 170px">
        <el-option label="按实际级别" value="" />
        <el-option label="强制 L3" value="L3" />
        <el-option label="强制 L2" value="L2" />
        <el-option label="强制 L1" value="L1" />
      </el-select>
      <el-select v-model="minLevel" style="width: 150px">
        <el-option label="只显示 ≥ SAFE" value="SAFE" />
        <el-option label="只显示 ≥ L1" value="L1" />
        <el-option label="只显示 ≥ L2" value="L2" />
        <el-option label="只显示 ≥ L3" value="L3" />
      </el-select>
      <el-checkbox v-model="enableL2">L2 语义</el-checkbox>
      <el-checkbox v-model="enableL3">L3 大模型</el-checkbox>
      <el-button type="primary" :loading="loading" @click="runDetect">检测</el-button>
      <el-upload :auto-upload="false" :show-file-list="false" :on-change="onFileChange">
        <el-button>上传文件</el-button>
      </el-upload>
      <el-button @click="text = SAMPLE">恢复示例</el-button>
    </div>
  </div>

  <div class="panel">
    <div class="panel-title">
      左右对照
      <span class="hint" v-if="result">
        {{ result.content_type }} · {{ result.segments }} 个片段 · 耗时 {{ result.duration_ms }}ms
        <template v-if="result.degraded"> · 已降级</template>
      </span>
    </div>
    <div class="compare">
      <div class="pane">
        <div class="pane-head">原文（命中处高亮；悬停看类别与规则）</div>
        <div class="pane-body" v-html="originalHtml"></div>
      </div>
      <div class="pane">
        <div class="pane-head">脱敏副本（写库/导出用的就是它）</div>
        <div class="pane-body" v-html="maskedHtml"></div>
      </div>
    </div>
    <div class="legend">
      <span
        v-for="level in ['L3', 'L2', 'L1', 'SAFE']"
        :key="level"
        class="chip"
        :style="levelChip(level)"
      >
        {{ level }} {{ LEVEL_LABELS[level] }} {{ byLevel[level] || 0 }}
      </span>
    </div>
  </div>

  <div class="panel">
    <div class="panel-title">检测结果明细</div>
    <el-table v-if="findings.length" :data="findings" stripe>
      <el-table-column prop="path" label="位置" width="160" />
      <el-table-column label="级别" width="90">
        <template #default="scope">
          <span class="chip" :style="levelChip(scope.row.level)">{{ scope.row.level }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="score" label="分数" width="80" />
      <el-table-column label="类别" width="220">
        <template #default="scope">{{ (scope.row.types || []).join(", ") }}</template>
      </el-table-column>
      <el-table-column prop="hits" label="命中" width="70" />
      <el-table-column prop="model_chain" label="模型链" width="110" />
      <el-table-column prop="masked" label="脱敏副本" show-overflow-tooltip />
    </el-table>
    <div v-else class="empty">还没有检测结果</div>
  </div>
</template>
