<script setup>
import { onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { api } from "../api";

const tasks = ref([]);
const findings = ref([]);
const audit = ref([]);
const verify = ref(null);
const current = ref(null);
const loading = ref(false);
const file = ref(null);
const textInput = ref("");

async function loadTasks() {
  loading.value = true;
  try {
    const body = await api.tasks(1, 20);
    tasks.value = body.items || [];
  } catch (err) {
    ElMessage.error(`加载任务失败：${err}`);
  } finally {
    loading.value = false;
  }
}

async function loadAudit() {
  try {
    const body = await api.audit(1, 20);
    audit.value = body.items || [];
    verify.value = await api.auditVerify();
  } catch (err) {
    ElMessage.error(`加载审计链失败：${err}`);
  }
}

async function openTask(row) {
  current.value = row;
  try {
    const body = await api.taskFindings(row.id, 1, 100);
    findings.value = body.items || [];
  } catch (err) {
    ElMessage.error(`加载任务详情失败：${err}`);
  }
}

async function createFromText() {
  if (!textInput.value.trim()) {
    ElMessage.warning("先输入文本");
    return;
  }
  try {
    const created = await api.createTaskText(textInput.value);
    ElMessage.success(`任务已创建：${created.task_id}`);
    textInput.value = "";
    await loadTasks();
  } catch (err) {
    ElMessage.error(`创建任务失败：${err}`);
  }
}

async function createFromFile(uploadFile) {
  file.value = uploadFile.raw;
  try {
    const created = await api.createTaskFile(uploadFile.raw);
    ElMessage.success(`任务已创建：${created.task_id}`);
    await loadTasks();
  } catch (err) {
    ElMessage.error(`上传失败：${err}`);
  }
}

function exportUrl(fmt) {
  return current.value ? api.taskExportUrl(current.value.id, fmt) : "#";
}

async function copyExport(fmt) {
  if (!current.value) return;
  try {
    const res = await fetch(api.taskExportUrl(current.value.id, fmt));
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    window.open(api.taskExportUrl(current.value.id, fmt), "_blank");
  } catch (err) {
    ElMessage.error(`导出失败：${err}`);
  }
}

onMounted(async () => {
  await loadTasks();
  await loadAudit();
});
</script>

<template>
  <div class="panel">
    <div class="panel-title">
      任务（原文不落库：只存脱敏副本与输入哈希）
      <span class="hint">批量检测、结果留痕、审计链可验签</span>
    </div>
    <el-input v-model="textInput" type="textarea" :rows="2" placeholder="粘贴文本创建检测任务" />
    <div class="toolbar">
      <el-button type="primary" @click="createFromText">提交任务（文本）</el-button>
      <el-upload :auto-upload="false" :show-file-list="false" :on-change="createFromFile">
        <el-button>上传文件建任务</el-button>
      </el-upload>
      <el-button :loading="loading" @click="loadTasks">刷新</el-button>
    </div>
    <el-table :data="tasks" stripe style="margin-top: 12px" @row-click="openTask">
      <el-table-column prop="id" label="任务" width="200" />
      <el-table-column prop="status" label="状态" width="90" />
      <el-table-column prop="content_type" label="类型" width="90" />
      <el-table-column prop="created_at" label="创建时间" width="200" />
      <el-table-column prop="total_findings" label="命中" width="80" />
      <el-table-column prop="error" label="错误" show-overflow-tooltip />
    </el-table>
  </div>

  <div class="panel" v-if="current">
    <div class="panel-title">
      任务 {{ current.id }} 的检测结果
      <span class="hint">
        <el-button size="small" @click="copyExport('jsonl')">导出 JSONL</el-button>
        <el-button size="small" @click="copyExport('csv')">导出 CSV</el-button>
        <el-button size="small" @click="copyExport('json')">导出 JSON</el-button>
        <el-button size="small" @click="copyExport('sql')">导出 SQL</el-button>
      </span>
    </div>
    <el-table :data="findings" stripe>
      <el-table-column prop="path" label="位置" width="170" />
      <el-table-column prop="level" label="级别" width="80" />
      <el-table-column prop="score" label="分数" width="80" />
      <el-table-column label="类别" width="200">
        <template #default="scope">{{ (scope.row.types || []).join(", ") }}</template>
      </el-table-column>
      <el-table-column prop="masked" label="脱敏副本" show-overflow-tooltip />
    </el-table>
    <div v-if="!findings.length" class="empty">该任务暂无可展示结果（可能仍在运行）</div>
  </div>

  <div class="panel">
    <div class="panel-title">
      审计链（哈希链 + HMAC 签名）
      <span class="hint" v-if="verify">
        校验：<b :style="{ color: verify.ok ? '#0d9488' : '#dc2626' }">{{ verify.ok ? "通过" : "失败" }}</b>
        · 共 {{ verify.total }} 条，已校验 {{ verify.checked }} 条
        <template v-if="verify.broken_at != null"> · 断点 {{ verify.broken_at }}</template>
      </span>
    </div>
    <div class="toolbar">
      <el-button size="small" @click="loadAudit">刷新并校验</el-button>
    </div>
    <el-table :data="audit" stripe style="margin-top: 12px" max-height="360">
      <el-table-column prop="id" label="#" width="60" />
      <el-table-column prop="ts" label="时间" width="200" />
      <el-table-column prop="task_id" label="任务" width="180" />
      <el-table-column prop="rule_id" label="规则" width="140" />
      <el-table-column prop="operator_id" label="操作人" width="120" />
      <el-table-column prop="input_hash" label="输入哈希" width="150" show-overflow-tooltip />
      <el-table-column prop="prev_hash" label="前序哈希" show-overflow-tooltip />
    </el-table>
  </div>
</template>
