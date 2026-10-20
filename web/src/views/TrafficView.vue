<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { api, LEVEL_COLORS, LEVEL_LABELS } from "../api";

const formats = ref(null);
const file = ref(null);
const uploading = ref(false);
const enableL2 = ref(false);
const enableL3 = ref(false);
const maskLevel = ref("");
const minLevel = ref("SAFE");
const summary = ref(null);
const task = ref(null);
const findings = ref([]);
const error = ref("");

const LEVEL_OPTIONS = ["SAFE", "L1", "L2", "L3"];

const uploadText = computed(() => (file.value ? file.value.name : "点击或拖拽 pcap 文件到这里"));

onMounted(async () => {
  try {
    formats.value = await api.trafficFormats();
  } catch (err) {
    error.value = String(err);
  }
  // ?demo=1 时自动导入仓库自带示例，方便演示与截图
  if (new URLSearchParams(location.search).get("demo")) {
    await importSample();
  }
});

async function importSample() {
  try {
    const res = await fetch(api.trafficSampleUrl);
    if (!res.ok) throw new Error(`示例下载失败：${res.status}`);
    const blob = await res.blob();
    file.value = new File([blob], "demo-traffic.pcap", { type: "application/vnd.tcpdump.pcap" });
    await importPcap();
  } catch (err) {
    error.value = String(err);
    ElMessage.error(error.value);
  }
}

function onFileChange(uploadFile) {
  file.value = uploadFile.raw || uploadFile;
}

async function importPcap() {
  if (!file.value) {
    ElMessage.warning("请先选择 pcap 文件");
    return;
  }
  uploading.value = true;
  error.value = "";
  summary.value = null;
  task.value = null;
  findings.value = [];
  try {
    const body = await api.importTraffic(file.value, {
      enableL2: enableL2.value,
      enableL3: enableL3.value,
      maskLevel: maskLevel.value,
      minLevel: minLevel.value,
    });
    summary.value = body;
    await waitTask(body.task_id);
  } catch (err) {
    error.value = String(err);
    ElMessage.error(error.value);
  } finally {
    uploading.value = false;
  }
}

async function waitTask(taskId) {
  for (let i = 0; i < 60; i += 1) {
    const info = await api.task(taskId);
    task.value = info;
    if (info.status === "done" || info.status === "failed") {
      const page = await api.taskFindings(taskId, 1, 100);
      findings.value = page.items || [];
      if (info.status === "failed") ElMessage.error(info.error || "任务执行失败");
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  ElMessage.warning("任务仍在执行，请稍后到「任务与审计链」查看");
}

function exportFindings(fmt) {
  if (!task.value) return;
  window.open(api.taskExportUrl(task.value.id, fmt), "_blank");
}

function levelColor(level) {
  return LEVEL_COLORS[level] || "#64748b";
}

function levelLabel(level) {
  return LEVEL_LABELS[level] || level;
}

// ---------------- 实时旁路 ----------------

const liveSessions = ref([]);
const liveBusy = ref(false);
const liveForm = ref({ pcap_path: "data/live.pcap", interval_ms: 500, window_s: 2.0 });
let liveTimer = null;

async function loadLive() {
  try {
    const body = await api.liveList();
    liveSessions.value = body.sessions || [];
  } catch (err) {
    // 实时会话列表失败不打断导入主流程，静默即可
  }
}

async function startLive() {
  liveBusy.value = true;
  try {
    const res = await api.liveStart({ ...liveForm.value });
    ElMessage.success(`已开始跟随 ${res.pcap_path}`);
    await loadLive();
  } catch (err) {
    ElMessage.error(String(err));
  } finally {
    liveBusy.value = false;
  }
}

async function stopLive(row) {
  liveBusy.value = true;
  try {
    await api.liveStop(row.live_id);
    ElMessage.success("已停止跟随（未写完的窗口会先落库）");
    await loadLive();
  } catch (err) {
    ElMessage.error(String(err));
  } finally {
    liveBusy.value = false;
  }
}

async function clearLive() {
  try {
    const res = await api.liveClear();
    ElMessage.success(`已清理 ${res.cleared} 个已停止的会话`);
    await loadLive();
  } catch (err) {
    ElMessage.error(String(err));
  }
}

onMounted(() => {
  loadLive();
  liveTimer = setInterval(loadLive, 2000);
  if (new URLSearchParams(location.search).get("live")) startLive();
});
onBeforeUnmount(() => clearInterval(liveTimer));
</script>

<template>
  <el-card shadow="never">
    <template #header>
      <div class="card-head">
        <span>流量导入（pcap → 正文 → 同一套识别分级与审计）</span>
        <span class="hint" v-if="formats">
          支持 {{ formats.formats.join(" / ") }} · 上限 {{ formats.max_upload_mb }} MB · {{ formats.note }}
        </span>
      </div>
    </template>

    <el-row :gutter="16">
      <el-col :span="12">
        <el-upload drag :auto-upload="false" :limit="1" :on-change="onFileChange" :show-file-list="false">
          <div class="upload-box">
            <div class="upload-title">{{ uploadText }}</div>
            <div class="upload-hint">仓库自带示例：<code>server/samples/demo-traffic.pcap</code>（含 L3/L2/SAFE 三类会话）</div>
          </div>
        </el-upload>
      </el-col>
      <el-col :span="12">
        <el-form label-width="90px" label-position="left">
          <el-form-item label="脱敏级别">
            <el-select v-model="maskLevel" placeholder="按实际级别" clearable style="width: 100%">
              <el-option v-for="lvl in LEVEL_OPTIONS" :key="lvl" :label="lvl" :value="lvl" />
            </el-select>
          </el-form-item>
          <el-form-item label="只显示 ≥">
            <el-select v-model="minLevel" style="width: 100%">
              <el-option v-for="lvl in LEVEL_OPTIONS" :key="lvl" :label="lvl" :value="lvl" />
            </el-select>
          </el-form-item>
          <el-form-item label="模型">
            <el-checkbox v-model="enableL2">L2 语义复核</el-checkbox>
            <el-checkbox v-model="enableL3">L3 大模型复核</el-checkbox>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="uploading" @click="importPcap">导入并检测</el-button>
            <el-button :loading="uploading" @click="importSample">导入自带示例</el-button>
          </el-form-item>
        </el-form>
      </el-col>
    </el-row>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" style="margin-top: 12px" />

    <el-descriptions v-if="summary" :column="6" border style="margin-top: 16px">
      <el-descriptions-item label="报文">{{ summary.packets }}</el-descriptions-item>
      <el-descriptions-item label="可解码">{{ summary.decoded_frames }}</el-descriptions-item>
      <el-descriptions-item label="会话">{{ summary.sessions }}</el-descriptions-item>
      <el-descriptions-item label="报文段">{{ summary.messages }}</el-descriptions-item>
      <el-descriptions-item label="待检测片段">{{ summary.segments }}</el-descriptions-item>
      <el-descriptions-item label="任务状态">
        <el-tag v-if="task" :type="task.status === 'done' ? 'success' : task.status === 'failed' ? 'danger' : 'info'">
          {{ task.status }}
        </el-tag>
        <span v-else>运行中…</span>
      </el-descriptions-item>
    </el-descriptions>

    <div v-if="task && task.status === 'done'" class="result-head">
      <span>命中 <b>{{ findings.length }}</b> 条 · 任务 <code>{{ task.id }}</code></span>
      <el-button-group>
        <el-button size="small" @click="exportFindings('jsonl')">导出 JSONL</el-button>
        <el-button size="small" @click="exportFindings('csv')">导出 CSV</el-button>
      </el-button-group>
    </div>

    <el-table v-if="findings.length" :data="findings" size="small" style="margin-top: 8px">
      <el-table-column prop="path" label="流量 / 请求" min-width="300" show-overflow-tooltip />
      <el-table-column label="级别" width="88">
        <template #default="{ row }">
          <el-tag :style="{ backgroundColor: levelColor(row.level), color: '#fff', border: 'none' }">
            {{ row.level }} {{ levelLabel(row.level) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="score" label="分数" width="80" />
      <el-table-column prop="types" label="类别" min-width="140">
        <template #default="{ row }">{{ (row.types || []).join(", ") }}</template>
      </el-table-column>
      <el-table-column prop="hits" label="命中" width="70" />
      <el-table-column prop="masked" label="脱敏副本" min-width="280" show-overflow-tooltip />
    </el-table>
  </el-card>

  <el-card shadow="never" style="margin-top: 16px">
    <template #header>
      <div class="card-head">
        <span>实时旁路（跟随持续增长的 pcap 文件）</span>
        <span class="hint">服务端不直接抓网卡：由 dumpcap / Wireshark / 自研采集器持续写文件，这里按窗口增量检测</span>
      </div>
    </template>

    <el-form inline>
      <el-form-item label="抓包文件">
        <el-input v-model="liveForm.pcap_path" style="width: 240px" placeholder="data/live.pcap" />
      </el-form-item>
      <el-form-item label="轮询间隔">
        <el-input-number v-model="liveForm.interval_ms" :min="100" :max="10000" :step="100" />
      </el-form-item>
      <el-form-item label="落库窗口(s)">
        <el-input-number v-model="liveForm.window_s" :min="0.2" :max="60" :step="0.5" />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="liveBusy" @click="startLive">开始跟随</el-button>
        <el-button @click="loadLive">刷新</el-button>
        <el-button @click="clearLive">清理已停止</el-button>
      </el-form-item>
    </el-form>

    <el-table :data="liveSessions" size="small" empty-text="还没有实时会话：点「开始跟随」后往里追加报文即可">
      <el-table-column prop="pcap_path" label="抓包文件" min-width="220" show-overflow-tooltip />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.running ? 'success' : 'info'">{{ row.running ? "跟随中" : "已停止" }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="packets" label="报文" width="80" />
      <el-table-column prop="segments" label="待检测片段" width="110" />
      <el-table-column prop="tasks" label="窗口任务" width="90" />
      <el-table-column label="最近任务" min-width="150">
        <template #default="{ row }"><code v-if="row.last_task_id">{{ row.last_task_id }}</code></template>
      </el-table-column>
      <el-table-column prop="last_error" label="最近错误" min-width="180" show-overflow-tooltip />
      <el-table-column label="操作" width="110">
        <template #default="{ row }">
          <el-button v-if="row.running" size="small" text type="danger" @click="stopLive(row)">停止</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>
</template>

<style scoped>
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
}
.hint {
  color: #64748b;
  font-size: 12px;
}
.upload-box {
  padding: 18px 12px;
}
.upload-title {
  font-weight: 600;
  color: #0f172a;
}
.upload-hint {
  margin-top: 6px;
  font-size: 12px;
  color: #64748b;
}
.result-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 16px;
}
</style>
