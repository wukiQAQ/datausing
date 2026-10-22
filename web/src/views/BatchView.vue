<script setup>
import { computed, onBeforeUnmount, reactive, ref } from "vue";
import { ElMessage } from "element-plus";
import { api, LEVEL_COLORS } from "../api";

const MAX_FILES = 20;
const files = ref([]);
const running = ref(false);
const rows = ref([]);
const activeTask = ref("");
const findings = ref([]);
const findingsTotal = ref(0);
const options = reactive({
  maskLevel: "auto",
  minLevel: "SAFE",
  enableL2: false,
  enableL3: false,
  enableNer: false,
});
let pollTimer = null;

const canRun = computed(() => files.value.length > 0 && !running.value);
const doneCount = computed(() => rows.value.filter((row) => ["done", "failed", "解析失败"].includes(row.status)).length);

function onChange(uploadFile, uploadFiles) {
  files.value = uploadFiles.slice(0, MAX_FILES).map((item) => item.raw).filter(Boolean);
  if (uploadFiles.length > MAX_FILES) {
    ElMessage.warning(`一次最多 ${MAX_FILES} 个文件，已截断`);
  }
}

function clearAll() {
  files.value = [];
  rows.value = [];
  findings.value = [];
  activeTask.value = "";
  findingsTotal.value = 0;
  stopPolling();
}

function rowFor(filename, item) {
  return {
    filename,
    status: item.error ? "解析失败" : "pending",
    taskId: item.task_id || "",
    segments: item.segments || 0,
    findings: 0,
    l3: 0,
    l2: 0,
    error: item.error || "",
  };
}

async function run() {
  if (!files.value.length) return;
  running.value = true;
  rows.value = [];
  activeTask.value = "";
  findings.value = [];
  try {
    const body = await api.batchTasks(files.value, options);
    rows.value = body.items.map((item) => rowFor(item.filename, item));
    const accepted = body.items.filter((item) => item.task_id).length;
    ElMessage.success(`已提交 ${accepted}/${body.total} 个文件，正在后台检测…`);
    startPolling();
  } catch (err) {
    ElMessage.error(`批量提交失败：${err}`);
    running.value = false;
  }
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(refresh, 800);
  refresh();
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function refresh() {
  const pending = rows.value.filter((row) => row.taskId && ["pending", "running"].includes(row.status));
  if (!pending.length) {
    stopPolling();
    running.value = false;
    return;
  }
  await Promise.all(
    pending.map(async (row) => {
      try {
        const task = await api.task(row.taskId);
        row.status = task.status;
        row.segments = task.total_segments ?? row.segments;
        row.findings = task.total_findings ?? 0;
        if (task.status === "failed") row.error = task.error || "检测失败";
        if (task.status === "done") {
          const page = await api.taskFindings(row.taskId, 1, 100);
          row.l3 = page.items.filter((item) => item.level === "L3").length;
          row.l2 = page.items.filter((item) => item.level === "L2").length;
        }
      } catch (err) {
        row.error = String(err);
      }
    })
  );
}

async function openTask(row) {
  if (!row.taskId) return;
  activeTask.value = row.taskId;
  try {
    const page = await api.taskFindings(row.taskId, 1, 50);
    findings.value = page.items;
    findingsTotal.value = page.total;
  } catch (err) {
    ElMessage.error(`加载结果失败：${err}`);
  }
}

function statusType(status) {
  if (status === "done") return "success";
  if (status === "failed" || status === "解析失败") return "danger";
  if (status === "running") return "warning";
  return "info";
}

function levelStyle(level) {
  return { backgroundColor: LEVEL_COLORS[level] || "#64748b" };
}

function preview(text, limit = 60) {
  const value = String(text || "").replace(/\s+/g, " ");
  return value.length > limit ? `${value.slice(0, limit)}…` : value;
}

onBeforeUnmount(stopPolling);
</script>

<template>
  <div class="panel">
    <div class="panel-title">
      批量检测（一次最多 {{ MAX_FILES }} 个文件）
      <span class="hint">每个文件一个任务，坏文件只在结果里标错，不影响同批其它文件</span>
    </div>
    <el-upload
      drag
      multiple
      :auto-upload="false"
      :limit="MAX_FILES"
      :on-change="onChange"
      accept=".txt,.log,.md,.json,.csv,.tsv,.sql,.docx,.pdf"
    >
      <div class="upload-tip">
        <div class="upload-title">点击或拖拽多个文件到这里</div>
        <div class="upload-sub">支持 txt / json / csv / sql / docx / pdf，单文件上限由服务端配置（默认 20 MB）</div>
      </div>
    </el-upload>

    <div class="toolbar">
      <span class="tool-label">脱敏级别</span>
      <el-select v-model="options.maskLevel" size="small" style="width: 132px">
        <el-option label="按实际级别" value="auto" />
        <el-option v-for="level in ['L3', 'L2', 'L1', 'SAFE']" :key="level" :label="level" :value="level" />
      </el-select>
      <span class="tool-label">只显示 ≥</span>
      <el-select v-model="options.minLevel" size="small" style="width: 110px">
        <el-option v-for="level in ['SAFE', 'L1', 'L2', 'L3']" :key="level" :label="level" :value="level" />
      </el-select>
      <el-checkbox v-model="options.enableL2">L2 语义复核</el-checkbox>
      <el-checkbox v-model="options.enableL3">L3 大模型复核</el-checkbox>
      <el-checkbox v-model="options.enableNer">自由文本 NER</el-checkbox>
      <el-button type="primary" :disabled="!canRun" :loading="running" @click="run">开始批量检测</el-button>
      <el-button :disabled="running" @click="clearAll">清空</el-button>
    </div>

    <div v-if="rows.length" style="margin-top: 14px">
      <div class="panel-title" style="margin-bottom: 8px; font-size: 14px">
        批量结果
        <span class="hint">已处理 {{ doneCount }}/{{ rows.length }} 个文件</span>
      </div>
      <el-table :data="rows" size="small" style="width: 100%">
        <el-table-column prop="filename" label="文件" min-width="180" show-overflow-tooltip />
        <el-table-column label="状态" width="96">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="segments" label="片段" width="70" />
        <el-table-column prop="findings" label="命中" width="70" />
        <el-table-column label="高危/中危" width="100">
          <template #default="{ row }">{{ row.l3 }} / {{ row.l2 }}</template>
        </el-table-column>
        <el-table-column label="任务" min-width="130">
          <template #default="{ row }">
            <span class="mono">{{ row.taskId ? row.taskId.slice(0, 8) : "-" }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150">
          <template #default="{ row }">
            <el-button link type="primary" size="small" :disabled="!row.taskId" @click="openTask(row)">查看</el-button>
            <a
              v-if="row.taskId"
              class="export-link"
              :href="api.taskExportUrl(row.taskId, 'jsonl')"
              target="_blank"
              rel="noreferrer"
            >导出</a>
          </template>
        </el-table-column>
        <el-table-column prop="error" label="错误" min-width="160" show-overflow-tooltip />
      </el-table>
    </div>
  </div>

  <div v-if="activeTask" class="panel">
    <div class="panel-title">
      结果明细
      <span class="hint">共 {{ findingsTotal }} 条命中 · 任务 {{ activeTask.slice(0, 8) }}</span>
    </div>
    <el-table v-if="findings.length" :data="findings" size="small" style="width: 100%">
      <el-table-column prop="path" label="位置" width="150" show-overflow-tooltip />
      <el-table-column label="级别" width="76">
        <template #default="{ row }">
          <span class="chip" :style="levelStyle(row.level)">{{ row.level }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="score" label="分数" width="80" />
      <el-table-column label="类别" width="160" show-overflow-tooltip>
        <template #default="{ row }">{{ (row.types || []).join(", ") }}</template>
      </el-table-column>
      <el-table-column prop="hits" label="命中" width="60" />
      <el-table-column label="脱敏副本" min-width="220">
        <template #default="{ row }">
          <span class="mono">{{ preview(row.masked, 80) }}</span>
        </template>
      </el-table-column>
    </el-table>
    <div v-else class="empty">还没有检测结果</div>
  </div>
</template>

<style scoped>
.upload-tip {
  padding: 18px 0;
}
.upload-title {
  font-size: 15px;
  font-weight: 600;
}
.upload-sub {
  margin-top: 6px;
  font-size: 12px;
  color: var(--muted);
}
.tool-label {
  font-size: 12px;
  color: var(--muted);
}
.toolbar {
  margin-top: 12px;
}
.mono {
  font-family: Consolas, "Courier New", monospace;
  font-size: 12px;
  color: var(--muted);
}
.export-link {
  margin-left: 10px;
  font-size: 12px;
}
</style>
