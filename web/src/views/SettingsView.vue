<script setup>
import { safeError } from "../workspace";
import PageHeader from "../components/PageHeader.vue";
import { computed, onMounted, ref } from "vue";
import { api } from "../api";

// ---------------------------------------------------------------------------
// 模型与助手：进入页面就查一次，之后靠「刷新状态」手动刷新（模型可能后启动）
// ---------------------------------------------------------------------------
const section = ref("capabilities");
const health = ref(null);
const models = ref(null);
const healthLoading = ref(false);
const healthError = ref("");

const MODE_LABELS = {
  rules: "规则模式（本地规则，不需要模型）",
  ollama: "本地 Ollama",
};
const modeLabel = computed(() => {
  if (!health.value) return "";
  return MODE_LABELS[health.value.mode] || health.value.mode || "未知";
});
const modelList = computed(() => {
  const list = models.value ? models.value.models : null;
  return Array.isArray(list) ? list : [];
});
const modelHint = computed(() => {
  if (!health.value) return "";
  if (health.value.mode === "rules") {
    return "规则模式：方案推荐与风险研判都由本地规则生成，不需要下载模型。";
  }
  if (!modelList.value.length) {
    return "未发现本机模型：先在服务端执行 ollama pull <模型名>，或把 FLOWMASK_ASSISTANT_MODE 改回 rules。";
  }
  return `本机可用模型 ${modelList.value.length} 个：${modelList.value.join("、")}`;
});
const healthTagType = computed(() => {
  if (!health.value) return "info";
  return health.value.available ? "success" : "warning";
});

async function loadHealth() {
  healthLoading.value = true;
  healthError.value = "";
  try {
    // 两个接口各查各的：models 失败不应让健康状态一起空白
    const results = await Promise.allSettled([
      api.fetchAssistantHealth(),
      api.fetchAssistantModels(),
    ]);
    const [healthResult, modelsResult] = results;
    if (healthResult.status === "fulfilled") {
      health.value = healthResult.value;
    } else {
      throw healthResult.reason;
    }
    models.value =
      modelsResult.status === "fulfilled" ? modelsResult.value : null;
    if (modelsResult.status === "rejected") {
      healthError.value = `模型列表读取失败（不影响状态展示）：${modelsResult.reason}`;
    }
  } catch (err) {
    health.value = null;
    healthError.value = `助手状态读取失败：${safeError(err)}`;
  } finally {
    healthLoading.value = false;
  }
}

// ---------------------------------------------------------------------------
// 脱敏策略：下拉与说明由服务端统一给，这里只做只读展示
// ---------------------------------------------------------------------------
const strategies = ref([]);
const strategiesLoading = ref(false);
const strategiesError = ref("");
const defaultStrategy = ref("partial");

const strategyLabel = (key) => {
  const found = strategies.value.find((item) => item.key === key);
  return found ? found.label : key;
};

async function loadStrategies() {
  strategiesLoading.value = true;
  strategiesError.value = "";
  try {
    const body = await api.fetchMaskStrategies();
    strategies.value = body.strategies || [];
    defaultStrategy.value = body.default || "partial";
  } catch (err) {
    strategies.value = [];
    strategiesError.value = `脱敏策略加载失败：${safeError(err)}`;
  } finally {
    strategiesLoading.value = false;
  }
}

// ---------------------------------------------------------------------------
// 场景模板：选中一行就展开它的数据保留说明
// ---------------------------------------------------------------------------
const scenarios = ref([]);
const scenariosLoading = ref(false);
const scenariosError = ref("");
const selectedScenario = ref("");

const activeScenario = computed(
  () =>
    scenarios.value.find((item) => item.key === selectedScenario.value) || null,
);

function selectScenario(row) {
  selectedScenario.value = selectedScenario.value === row.key ? "" : row.key;
}

async function loadScenarios() {
  scenariosLoading.value = true;
  scenariosError.value = "";
  try {
    const body = await api.fetchScenarios();
    scenarios.value = body.scenarios || [];
  } catch (err) {
    scenarios.value = [];
    scenariosError.value = `场景模板加载失败：${safeError(err)}`;
  } finally {
    scenariosLoading.value = false;
  }
}

// ---------------------------------------------------------------------------
// 常见问题：静态文案，写清楚能力边界与配置方式，不做夸张承诺
// ---------------------------------------------------------------------------
const FAQ = [
  {
    q: "数据会上传到公网吗？",
    a: "不会。识别、分级、脱敏、审计全部在本机服务进程内完成，默认不依赖任何外部网络服务。只有主动开启 L3 大模型或把助手切到 ollama 模式时，才会连接本机地址（默认 127.0.0.1:11434）上的 Ollama；把 FLOWMASK_ASSISTANT_MODE 保持 rules、L2/L3 保持关闭时，整条链路完全不联网。浏览器访问的也是本机服务。",
  },
  {
    q: "支持哪些输入格式？",
    a: "文本 / Markdown / 日志（txt、md、log）、JSON、CSV / TSV、SQL、Word（docx）、Excel（xlsx）。docx 与 xlsx 都由服务端零依赖自研解析（zip + XML），不额外需要 python-docx / openpyxl；xlsx 每张工作表只检测表头以下的数据行，表头只作为列名提示。PDF 依赖可选库 pdfplumber / pypdf，缺依赖时会返回明确的安装提示而不是假装解析成功；扫描件 PDF 没有文本层，需要先 OCR。图片 OCR 见下一条。",
  },
  {
    q: "加密内容如何还原？",
    a: "导出时选择「加密」策略，被替换的敏感值会写成 enc: 开头的密文片段。把这份脱敏文本重新贴回检测页，点结果区的「还原加密内容」，服务端会用本机密钥把它还原成原文。密钥来自数据目录下的 export_secret.key（可用 FLOWMASK_EXPORT_SECRET 指定）；换机器或换密钥后旧密文无法还原。还原只在本机进行，请不要把密文或还原结果外发。",
  },
  {
    q: "图片 OCR 怎么开？",
    a: "OCR 是可选能力，默认关闭：先在服务端安装可选 OCR 依赖（requirements-ocr.txt），再设置 FLOWMASK_OCR_ENABLED=1 并重启服务。未启用时上传图片会返回「需要 OCR 依赖」的提示，不会静默失败。启用后图片先被识别成文字，再走同一套 L1/L2/L3 分级与脱敏流程。",
  },
  {
    q: "审计链如何验证？",
    a: "在「任务与审计链」页签点「刷新并校验」（对应 GET /api/v1/audit/verify），服务端会按 HMAC-SHA256 逐条重算 prev_hash、signature 与 entry_hash，返回是否通过、共多少条、校验了多少条、断点位置。也可以直接调接口或写脚本复核。签名密钥默认首次启动时自动生成并落在数据目录的 audit_secret.key（可用 FLOWMASK_AUDIT_SECRET 指定），换密钥后旧记录将无法验签。",
  },
  {
    q: "是否会保留原文？",
    a: "不会落原文。任务与检测结果只保存脱敏副本、原文的 SHA-256 哈希和审计链条目；即时检测接口不落库。流量导入（pcap）同样只存脱敏后的正文。导出的脱敏文件是本次请求在内存里重建后直接返回的，不写入数据库。",
  },
  {
    q: "性能与并发怎么样？",
    a: "单机 SQLite + 进程内识别，适合演示、单用户或小团队使用。L1 规则识别是毫秒级；L2 / L3 受模型与超时限制（默认 L2 0.5s、L3 5s），超时会自动降级并在结果里标记 degraded。并发能力取决于本机 CPU 与内存，不建议把 SQLite 单实例当多用户高并发服务使用。上传大小受 FLOWMASK_MAX_UPLOAD_MB 限制（默认 20MB）。",
  },
  {
    q: "依赖哪些环境变量？",
    a: "全部可选，不设置就按本机演示的默认值运行。常用：FLOWMASK_API_TOKEN（接口鉴权，空表示不鉴权）、FLOWMASK_DATA_DIR / FLOWMASK_DB_PATH、FLOWMASK_ENABLE_L2 / FLOWMASK_ENABLE_L3、FLOWMASK_ENABLE_NER、FLOWMASK_OCR_ENABLED、FLOWMASK_ASSISTANT_MODE / FLOWMASK_ASSISTANT_ENDPOINT / FLOWMASK_ASSISTANT_MODEL、FLOWMASK_AUDIT_SECRET、FLOWMASK_EXPORT_SECRET、FLOWMASK_MAX_UPLOAD_MB。",
  },
  {
    q: "「模型与助手」里的助手模式和 L3 大模型是一回事吗？",
    a: "不是。L3 大模型用于检测阶段的复核（对可疑片段再判一次），AI 助手用于推荐脱敏方案和统计看板的风险研判。两者都可以指向本机 Ollama，但配置项、开关和超时相互独立；助手默认 rules 模式，不下载模型也能用。",
  },
];

onMounted(async () => {
  await Promise.all([loadHealth(), loadStrategies(), loadScenarios()]);
});
</script>

<template>
  <PageHeader
    title="设置与策略"
    description="了解本机能力、脱敏策略和场景模板，明确数据保留边界。"
    index="PREFERENCES"
  />
  <el-tabs v-model="section"
    ><el-tab-pane name="capabilities" label="本机能力" /><el-tab-pane
      name="policies"
      label="策略与场景库" /><el-tab-pane name="help" label="使用与安全说明"
  /></el-tabs>
  <div v-show="section === 'capabilities'" class="panel">
    <div class="panel-title">
      模型与助手
      <span class="hint">只读状态：模型与助手都由服务端环境变量配置</span>
    </div>
    <div class="toolbar">
      <el-button size="small" :loading="healthLoading" @click="loadHealth"
        >刷新状态</el-button
      >
      <el-tag v-if="health" size="small" :type="healthTagType">
        {{ health.available ? "可用" : "当前不可用" }}
      </el-tag>
    </div>
    <el-alert
      v-if="healthError"
      type="error"
      :closable="false"
      show-icon
      style="margin-top: 12px"
    >
      {{ healthError }}
    </el-alert>
    <div v-loading="healthLoading" style="margin-top: 12px">
      <template v-if="health">
        <dl class="kv">
          <dt>助手开关</dt>
          <dd>
            {{
              health.enabled
                ? "已启用"
                : "已停用（FLOWMASK_ASSISTANT_ENABLED=0）"
            }}
          </dd>
          <dt>模式</dt>
          <dd>{{ modeLabel }}</dd>
          <dt>状态说明</dt>
          <dd>{{ health.note || "—" }}</dd>
          <dt>当前模型</dt>
          <dd>{{ health.model || "未配置" }}</dd>
          <dt>模型服务地址</dt>
          <dd>{{ (models && models.endpoint) || "—" }}</dd>
          <dt>本机模型</dt>
          <dd>{{ modelList.length ? modelList.join("、") : "未发现" }}</dd>
          <dt>模型接口说明</dt>
          <dd>{{ (models && models.note) || "—" }}</dd>
        </dl>
        <div class="hint-line">{{ modelHint }}</div>
      </template>
      <div v-else class="empty">还没有取到助手状态</div>
    </div>
    <div class="hint-line">
      配置方式（服务端环境变量，前端只读）：<code>FLOWMASK_ASSISTANT_MODE</code>（rules
      / ollama）、
      <code>FLOWMASK_ASSISTANT_ENDPOINT</code>（Ollama 地址，默认
      http://127.0.0.1:11434/api/generate）、
      <code>FLOWMASK_ASSISTANT_MODEL</code>（模型名，如
      qwen2.5:1.5b）。改完需要重启服务。
    </div>
  </div>

  <div v-show="section === 'policies'" class="panel">
    <div class="panel-title">
      脱敏策略与场景
      <span class="hint"
        >策略说明与场景模板都来自服务端，选场景可按推荐配置一键套用</span
      >
    </div>
    <div class="toolbar">
      <el-button
        size="small"
        :loading="strategiesLoading"
        @click="loadStrategies"
        >刷新策略</el-button
      >
      <el-button size="small" :loading="scenariosLoading" @click="loadScenarios"
        >刷新场景</el-button
      >
      <span class="hint">默认策略：{{ strategyLabel(defaultStrategy) }}</span>
    </div>

    <el-alert
      v-if="strategiesError"
      type="error"
      :closable="false"
      show-icon
      style="margin-top: 12px"
    >
      {{ strategiesError }}
    </el-alert>
    <el-table
      v-if="strategies.length"
      :data="strategies"
      stripe
      style="margin-top: 12px"
      :row-style="
        ({ row }) => (row.key === defaultStrategy ? { fontWeight: 600 } : {})
      "
    >
      <el-table-column label="策略" width="150">
        <template #default="scope">
          {{ scope.row.label }}
          <div class="hint">{{ scope.row.key }}</div>
        </template>
      </el-table-column>
      <el-table-column prop="description" label="说明" show-overflow-tooltip />
      <el-table-column label="可逆性" width="100">
        <template #default="scope">
          <el-tag
            :type="scope.row.reversible ? 'warning' : 'info'"
            size="small"
          >
            {{ scope.row.reversible ? "可还原" : "不可逆" }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="示例（虚构）" width="300">
        <template #default="scope">
          <div
            v-for="(example, index) in (scope.row.examples || []).slice(0, 3)"
            :key="index"
            class="hint"
          >
            {{ example.raw }} → {{ example.masked }}
          </div>
          <span v-if="!(scope.row.examples || []).length" class="hint">—</span>
        </template>
      </el-table-column>
    </el-table>
    <div v-else-if="!strategiesLoading && !strategiesError" class="empty">
      没有可展示的策略
    </div>

    <el-alert
      v-if="scenariosError"
      type="error"
      :closable="false"
      show-icon
      style="margin-top: 12px"
    >
      {{ scenariosError }}
    </el-alert>
    <el-table
      v-if="scenarios.length"
      :data="scenarios"
      stripe
      highlight-current-row
      style="margin-top: 12px"
      @row-click="selectScenario"
    >
      <el-table-column label="场景" width="170">
        <template #default="scope">
          {{ scope.row.label }}
          <div class="hint">{{ scope.row.key }}</div>
        </template>
      </el-table-column>
      <el-table-column prop="description" label="用途" show-overflow-tooltip />
      <el-table-column label="推荐策略" width="130">
        <template #default="scope">{{
          strategyLabel(scope.row.strategy)
        }}</template>
      </el-table-column>
      <el-table-column label="级别" width="150">
        <template #default="scope">
          脱敏 {{ scope.row.mask_level }} · 最低 {{ scope.row.min_level }}
        </template>
      </el-table-column>
      <el-table-column label="数据保留说明" show-overflow-tooltip>
        <template #default="scope">{{ scope.row.retention || "—" }}</template>
      </el-table-column>
    </el-table>
    <div v-else-if="!scenariosLoading && !scenariosError" class="empty">
      没有可展示的场景模板
    </div>
    <el-alert
      v-if="activeScenario"
      type="info"
      :closable="false"
      show-icon
      style="margin-top: 12px"
    >
      <b>{{ activeScenario.label }}</b> · 脱敏级别
      {{ activeScenario.mask_level }} · 最低 {{ activeScenario.min_level }} ·
      策略 {{ strategyLabel(activeScenario.strategy) }}<br />
      {{ activeScenario.description }}<br />
      数据保留：{{ activeScenario.retention || "无额外说明" }}
    </el-alert>
    <div v-if="scenarios.length" class="hint-line">
      点表格行可展开/收起该场景的数据保留说明。
    </div>
  </div>

  <div v-show="section === 'help'" class="panel">
    <div class="panel-title">
      常见问题
      <span class="hint">能力边界与配置方式，按实际情况说明</span>
    </div>
    <details v-for="item in FAQ" :key="item.q" class="faq-item">
      <summary>{{ item.q }}</summary>
      <p>{{ item.a }}</p>
    </details>
  </div>
</template>
