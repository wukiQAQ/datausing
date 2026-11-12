<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api, LEVEL_LABELS } from "../api";
import { escapeHtml, renderHighlight } from "../highlight";
import { countHits, httpErrorStatus, safeError, snapshotClipboardText, snapshotOutputIssue, sourceSegments } from "../workspace";
import { operator } from "../theme";
import PageHeader from "../components/PageHeader.vue";
import AppIcon from "../components/AppIcon.vue";
import RiskIndicator from "../components/RiskIndicator.vue";
import EmptyState from "../components/EmptyState.vue";
import AdvancedOptions from "../components/AdvancedOptions.vue";
import EditorPanel from "../components/EditorPanel.vue";

const SAMPLE = `客户张三，手机 13800138000，身份证 11010519491231002X，收货地址：福建省福州市鼓楼区五四路 100 号。`;
// 自由文本样例：没有字段名/关键词，L1 规则抓不到人名和地名，只有 NER 能补
const NER_SAMPLE =
  "张三昨天从福州赶过来，和李四在厦门大学碰了面，商量下个季度的采购方案。";

const text = ref("");
const maxTextLength = 200000;
const pasteLimit = ref(false);
// 本次结果对应的原文快照：上传文件检测时没有原文，不能拿输入框内容去套文件的高亮区间
const detectSource = ref("");
const file = ref(null);
const fileLabel = computed(() => file.value?.name || "");
const maskLevel = ref("");
const minLevel = ref("SAFE");
const enableL2 = ref(false);
const enableL3 = ref(false);
const enableNer = ref(false);
const nerAvailable = ref(false);
const nerBackend = ref("");
const health = ref(null);
const loading = ref(false);
const result = ref(null);
const detectError = ref("");
const detectStatus = ref(0);
const stale = ref(false);
const showOriginal = ref(false);
const previewStrategy = ref("");
const previewLoading = ref(false);
const copyLoading = ref(false);
let revision = 0;
let disposed = false;
onBeforeUnmount(() => {
  disposed = true;
  revision++;
});
const originalEditor = ref(null);
const maskedEditor = ref(null);
const detail = ref(null);
const detailOpen = ref(false);
const resultPage = ref(1);
const levelFilter = ref("");
const fileAccept = computed(() => ".txt,.log,.md,.json,.csv,.tsv,.sql,.docx,.pdf,.xlsx" +
  (health.value?.ocr ? ",.png,.jpg,.jpeg,.bmp,.webp" : ""));
const outputIssue = computed(() => result.value ? snapshotOutputIssue(result.value) : "");
const hasPreview = computed(() => result.value && !outputIssue.value && previewStrategy.value === strategy.value);
const busy = computed(() => loading.value || previewLoading.value || copyLoading.value || exportLoading.value);
const canOutput = computed(() => hasPreview.value && !busy.value);
const resultState = computed(() => {
  if (loading.value) return "正在检测";
  if ([401, 403].includes(detectStatus.value)) return "权限不足";
  if (detectError.value) return "检测失败";
  if (stale.value) return "结果已失效";
  if (!result.value) return text.value.trim() || file.value ? "等待提交" : "等待输入";
  if (outputIssue.value) return "检测不完整";
  if (previewLoading.value) return "正在核验策略";
  if (exportError.value && !hasPreview.value) return "策略未核验";
  if (hitCount.value) return "发现敏感命中";
  return result.value.effective_options?.min_level === "SAFE" ? "未发现命中" : "当前筛选无命中";
});
const hitCount = computed(() => countHits(findings.value));
const filteredFindings = computed(() =>
  findings.value.filter(
    (row) => !levelFilter.value || row.level === levelFilter.value,
  ),
);
const pagedFindings = computed(() =>
  filteredFindings.value.slice(
    (resultPage.value - 1) * 50,
    resultPage.value * 50,
  ),
);
watch(levelFilter, () => {
  resultPage.value = 1;
});
const segmentPage = ref(1);
const sourceIndex = computed(() =>
  sourceSegments(detectSource.value, result.value?.content_type),
);
const segmentList = computed(() => hasPreview.value ? result.value.masked_document.segments : []);
const isStructured = computed(
  () => result.value && result.value.content_type !== "text",
);
const editorSegments = computed(() =>
  segmentList.value.slice((segmentPage.value - 1) * 50, segmentPage.value * 50),
);
function rowHtml(segment) {
  const source = sourceIndex.value.get(segment.path);
  if (source === undefined) return escapeHtml("原文未提供");
  const index = findings.value.findIndex((row) => row.path === segment.path);
  const row = findings.value[index];
  return renderHighlight(
    source,
    (row?.spans || []).map((span) => ({
      ...span,
      level: row.level,
      finding: index,
    })),
  );
}
const originalDisplay = computed(() => {
  if (!result.value || !detectSource.value) return "";
  if (!isStructured.value) return detectSource.value;
  return editorSegments.value
    .map(
      (segment) =>
        `${segment.path}\n${sourceIndex.value.get(segment.path) ?? "原文未提供"}\n`,
    )
    .join("\n");
});
const originalDisplayHtml = computed(() => {
  if (!isStructured.value)
    return renderHighlight(
      detectSource.value,
      findings.value.flatMap((row, i) =>
        (row.spans || []).map((span) => ({
          ...span,
          level: row.level,
          finding: i,
        })),
      ),
    );
  return editorSegments.value
    .map((segment) => `${escapeHtml(segment.path)}\n${rowHtml(segment)}\n`)
    .join("\n");
});
const maskedDisplay = computed(() =>
  isStructured.value
    ? editorSegments.value
        .map((segment) => `${segment.path}\n${segment.masked}\n`)
        .join("\n")
    : maskedText.value,
);
const maskedDisplayHtml = computed(() => {
  const mark = (masked, row) => row && row.level !== "SAFE"
    ? renderHighlight(masked, [{ start: 0, end: Array.from(masked).length, level: row.level, label: "命中片段" }])
    : escapeHtml(masked);
  if (!isStructured.value) {
    const row = findings.value.find((item) => item.masked === maskedDisplay.value);
    return mark(maskedDisplay.value, row);
  }
  return editorSegments.value.map((segment) =>
    `${escapeHtml(segment.path)}\n${mark(segment.masked, segment)}\n`
  ).join("\n");
});
function showDetail(row) {
  detail.value = row;
  detailOpen.value = true;
}
function showHit(index) {
  if (findings.value[index]) showDetail(findings.value[index]);
}
async function copyMasked() {
  if (!canOutput.value || !maskedText.value) return;
  copyLoading.value = true;
  try {
    if (!await syncSnapshotPreview()) return;
    try {
      await navigator.clipboard.writeText(maskedText.value);
      ElMessage.success("脱敏正文已复制");
    } catch {
      ElMessage.warning("剪贴板不可用，可使用导出脱敏文件。");
    }
  } finally {
    copyLoading.value = false;
  }
}
function resetSnapshot(outdated = false) {
  revision++;
  stale.value = outdated;
  result.value = null;
  detectSource.value = "";
  detectError.value = "";
  detectStatus.value = 0;
  exportError.value = "";
  previewStrategy.value = "";
  showOriginal.value = false;
  detail.value = null;
  detailOpen.value = false;
  decryptOpen.value = false;
  decryptInput.value = "";
  decryptResult.value = null;
}
watch(text, () => {
  file.value = null;
  pasteLimit.value = false;
}, { flush: "sync" });
watch([text, maskLevel, minLevel, enableL2, enableL3, enableNer, operator], () => {
  resetSnapshot(stale.value || Boolean(result.value) || loading.value);
}, { flush: "sync" });
async function clearInput() {
  if (!text.value && !result.value && !fileLabel.value && !pasteLimit.value) return;
  try {
    await ElMessageBox.confirm(
      "清空输入和当前检测结果？此操作不会删除已保存的任务或审计记录。",
      "清空工作区",
      { confirmButtonText: "清空", cancelButtonText: "保留", type: "warning" },
    );
  } catch {
    return;
  }
  text.value = "";
  file.value = null;
  pasteLimit.value = false;
  resetSnapshot();
}
function sample() {
  switchToText();
  pasteLimit.value = false;
  text.value = SAMPLE;
}

const nerTitle = computed(() =>
  nerAvailable.value
    ? `NER 后端：${nerBackend.value}（可选识别，仅补类别不抬级别）`
    : "服务端未启用 NER：启动时设置 FLOWMASK_ENABLE_NER=1 并准备 NER 权重",
);

const findings = computed(() => (result.value ? result.value.findings : []));
const byLevel = computed(() => (result.value ? result.value.by_level : {}));
const detectedTypes = computed(() => {
  const labels = new Map(findings.value.flatMap((row) =>
    (row.spans || []).filter((span) => span.label).map((span) => [span.type, span.label]),
  ));
  return [...new Set(findings.value.flatMap((row) => row.types || []).map((type) => labels.get(type) || type))].join("、");
});
const engineSummary = computed(() => {
  const labels = {
    disabled: "未请求", ineligible: "未达复核条件", skipped: "未达复核条件",
    executed: "已执行", unavailable: "不可用", timeout: "超时", error: "失败",
  };
  return ["l2", "l3"].map((engine) => {
    const runs = Object.entries(result.value?.engine_runs?.[engine] || {});
    const summary = runs.map(([status, count]) => `${labels[status] || "未标明"} ${count}`).join("、");
    return `${engine.toUpperCase()} ${summary || "无执行记录"}`;
  }).join(" · ");
});
const maskedText = computed(() => hasPreview.value ? snapshotClipboardText(result.value) : "");

async function runDetect() {
  if (disposed || busy.value || (!file.value && !text.value.trim())) return;
  resetSnapshot();
  if (file.value && /\.(png|jpe?g|bmp|webp)$/i.test(file.value.name) && !health.value?.ocr) {
    detectError.value = "图片识别未就绪。请启用服务端 OCR 或选择文本、文档。";
    return;
  }
  const submittedRevision = revision;
  const source = text.value;
  const selectedFile = file.value;
  loading.value = true;
  try {
    const response = selectedFile
      ? await api.detectFile(selectedFile, {
          enableL2: enableL2.value, enableL3: enableL3.value, enableNer: enableNer.value,
          maskLevel: maskLevel.value, minLevel: minLevel.value,
        })
      : await api.detectText({
          text: source, enable_l2: enableL2.value, enable_l3: enableL3.value,
          enable_ner: enableNer.value, mask_level: maskLevel.value || null,
          min_level: minLevel.value,
        });
    if (revision !== submittedRevision) return;
    result.value = response;
    detectSource.value = selectedFile ? "" : source;
    resultPage.value = 1;
    segmentPage.value = 1;
    if (!outputIssue.value) await syncSnapshotPreview();
  } catch (error) {
    if (revision !== submittedRevision) return;
    detectStatus.value = httpErrorStatus(error);
    detectError.value = safeError(error, "检测失败，请检查服务连接后重试。");
  } finally {
    loading.value = false;
  }
}

function onFileChange(uploadFile) {
  if (busy.value || !uploadFile.raw) return;
  file.value = uploadFile.raw;
  runDetect();
}

function switchToText() {
  if (!file.value) return;
  file.value = null;
  resetSnapshot(stale.value || Boolean(result.value));
}

function detectShortcut(event) {
  if (event.key !== "Enter" || !(event.ctrlKey || event.metaKey) ||
      event.isComposing || event.keyCode === 229) return;
  event.preventDefault();
  runDetect();
}

function checkPaste(event) {
  const inserted = event.clipboardData?.getData("text") || "";
  const { selectionStart, selectionEnd } = event.target;
  if (text.value.length - (selectionEnd - selectionStart) + inserted.length <= maxTextLength) return;
  event.preventDefault();
  pasteLimit.value = true;
}

// ---------------------------------------------------------------------------
// 场景模板：选中即套用推荐策略 + 级别 + 可选识别开关，并展示数据保留提示
// ---------------------------------------------------------------------------
const scenarios = ref([]);
const scenarioKey = ref("");

const activeScenario = computed(
  () => scenarios.value.find((item) => item.key === scenarioKey.value) || null,
);

function applyScenario(key) {
  const scenario = scenarios.value.find((item) => item.key === key);
  if (!scenario) return;
  strategy.value = scenario.strategy || strategy.value;
  maskLevel.value = scenario.mask_level || "";
  minLevel.value = scenario.min_level || "SAFE";
  enableL2.value = Boolean(scenario.enable_l2);
  enableL3.value = Boolean(scenario.enable_l3);
  // NER 只有服务端启用时才真正生效，这里不为不可用的能力伪造开启
  enableNer.value = Boolean(scenario.enable_ner) && nerAvailable.value;
}

function clearScenario() {
  scenarioKey.value = "";
}

// ---------------------------------------------------------------------------
// 脱敏策略：导出与「还原加密内容」共用，服务端不可用时退化为内置常用策略
// ---------------------------------------------------------------------------
const STRATEGY_FALLBACK = [
  {
    key: "block",
    label: "整段屏蔽",
    description: "敏感片段整体替换为占位符，信息全丢，不可逆。",
  },
  {
    key: "partial",
    label: "部分遮蔽",
    description: "只保留首尾少量字符，中间打码，不可逆。",
  },
  {
    key: "hash",
    label: "哈希",
    description: "替换为加盐摘要，可关联比对，不可逆。",
  },
  {
    key: "generalize",
    label: "泛化",
    description: "按类别泛化（如精确值变区间），不可逆。",
  },
  {
    key: "pseudonym",
    label: "假名化",
    description: "映射为稳定的假名/假值，不可逆。",
  },
  {
    key: "encrypt",
    reversible: true,
    label: "加密",
    description: "替换为本机密钥加密的 enc: 密文，可在本机还原。",
  },
];
const strategies = ref([]);
const strategy = ref("partial");
function handleSnapshotError(error, message) {
  const status = httpErrorStatus(error);
  previewStrategy.value = "";
  showOriginal.value = false;
  detail.value = null;
  detailOpen.value = false;
  if ([401, 403, 409, 410].includes(status)) {
    result.value = null;
    detectSource.value = "";
    stale.value = status === 409 || status === 410;
    detectStatus.value = status;
  }
  exportError.value = safeError(error, message);
}

async function syncSnapshotPreview() {
  const snapshot = result.value;
  if (!snapshot || outputIssue.value) return false;
  const selected = strategy.value;
  const requestedRevision = revision;
  const isCurrent = () => revision === requestedRevision &&
    result.value?.result_id === snapshot.result_id && strategy.value === selected;
  previewLoading.value = true;
  exportError.value = "";
  try {
    const preview = await api.previewSnapshot(snapshot, selected);
    if (!isCurrent()) return false;
    if (["result_id", "policy_version", "source_fingerprint", "ruleset_revision"].some(
      (key) => preview?.[key] !== snapshot[key],
    )) {
      handleSnapshotError({ status: 409 }, "");
      return false;
    }
    result.value = preview;
    previewStrategy.value = selected;
    return !outputIssue.value;
  } catch (error) {
    if (isCurrent()) handleSnapshotError(error, "策略预览失败，请检查服务后重新检测。");
    return false;
  } finally {
    previewLoading.value = false;
  }
}
watch(strategy, () => {
  showOriginal.value = false;
  detail.value = null;
  detailOpen.value = false;
  if (result.value && !busy.value && !outputIssue.value) syncSnapshotPreview();
});

const strategyOptions = computed(() =>
  strategies.value.length ? strategies.value : STRATEGY_FALLBACK,
);
const strategyHint = computed(() => {
  const found = strategyOptions.value.find(
    (item) => item.key === strategy.value,
  );
  if (!found) return "";
  const reversible = found.reversible ? "（可还原）" : "（不可逆）";
  return `${found.description || ""}${reversible}`;
});

const FMT_LABELS = {
  auto: "跟随原格式",
  txt: "纯文本 txt",
  json: "JSON",
  csv: "CSV",
  sql: "SQL",
  docx: "Word docx",
  xlsx: "Excel xlsx",
};
const TEXT_FMTS = ["auto", "txt", "json", "csv", "sql"];
const FILE_FMTS = ["auto", "txt", "docx", "xlsx", "json", "csv", "sql"];
const exportFmt = ref("auto");
const exportLoading = ref(false);
const exportError = ref("");

const fmtOptions = computed(() => (fileLabel.value ? FILE_FMTS : TEXT_FMTS));
const exportFallbackName = computed(() => {
  const fmt =
    exportFmt.value === "auto"
      ? fileLabel.value
        ? "bin"
        : "txt"
      : exportFmt.value;
  return `flowmask-masked.${fmt}`;
});

async function runExport() {
  if (!canOutput.value) return;
  if (!fmtOptions.value.includes(exportFmt.value)) exportFmt.value = "auto";
  const requestedRevision = revision;
  const selected = strategy.value;
  const id = result.value.result_id;
  const isCurrent = () => revision === requestedRevision && strategy.value === selected &&
    result.value?.result_id === id && Boolean(hasPreview.value);
  exportLoading.value = true;
  try {
    if (!await syncSnapshotPreview() || !isCurrent()) return;
    const name = await api.exportSnapshot(
      result.value, selected, exportFmt.value, exportFallbackName.value, isCurrent,
    );
    ElMessage.success(`已导出：${name}`);
  } catch (error) {
    if (isCurrent()) handleSnapshotError(error, "导出失败，请检查格式和服务状态。");
  } finally {
    exportLoading.value = false;
  }
}

// ---------------------------------------------------------------------------
// 还原加密内容：把当前掩码副本当 ciphertext 交给本机服务端还原
// ---------------------------------------------------------------------------
const decryptOpen = ref(false);
const decryptInput = ref("");
const decryptLoading = ref(false);
const decryptError = ref("");
const decryptResult = ref(null);

function openDecrypt() {
  decryptInput.value = maskedText.value;
  decryptResult.value = null;
  decryptError.value = "";
  decryptOpen.value = true;
}

async function runDecrypt() {
  if (!decryptInput.value.trim()) {
    decryptError.value = "先粘贴含 enc: 密文的脱敏文本。";
    return;
  }
  decryptLoading.value = true;
  decryptError.value = "";
  const requestedRevision = revision;
  try {
    const decrypted = await api.decryptText(decryptInput.value);
    if (revision === requestedRevision && decryptOpen.value) decryptResult.value = decrypted;
  } catch (err) {
    if (revision !== requestedRevision || !decryptOpen.value) return;
    decryptResult.value = null;
    decryptError.value = safeError(err, "还原失败，请检查密文及本机密钥。");
  } finally {
    decryptLoading.value = false;
  }
}

// ---------------------------------------------------------------------------
// AI 推荐方案：只发用途描述与统计信息，不发原文
// ---------------------------------------------------------------------------
const SOURCE_LABELS = {
  rules: "本地规则",
  local_llm: "本机模型",
  degraded: "模型不可用已回退",
};

const suggestOpen = ref(false);
const suggestLoading = ref(false);
const suggestError = ref("");
const suggestResult = ref(null);
const suggestForm = ref({ prompt: "", scenario: "" });

const sourceLabel = (key) => SOURCE_LABELS[key] || key || "未知来源";
const strategyLabelFor = (key) => {
  const found = strategyOptions.value.find((item) => item.key === key);
  return found ? `${found.label}（${found.key}）` : key || "—";
};

function openSuggest() {
  suggestForm.value = { prompt: "", scenario: scenarioKey.value };
  suggestResult.value = null;
  suggestError.value = "";
  suggestOpen.value = true;
}

async function runSuggest() {
  const prompt = suggestForm.value.prompt.trim();
  if (prompt.length > 200) {
    suggestError.value = "用途描述最多 200 字。";
    return;
  }
  suggestLoading.value = true;
  suggestError.value = "";
  try {
    // fields 只传类别名，不传真实值；原文一律不上送
    const fields = [
      ...new Set(findings.value.flatMap((item) => item.types || [])),
    ].slice(0, 32);
    suggestResult.value = await api.suggestMasking({
      scenario: suggestForm.value.scenario || null,
      content_type: (result.value && result.value.content_type) || "text",
      fields,
      level_counts: byLevel.value,
      text_length: (detectSource.value || maskedText.value || "").length,
      prompt,
    });
  } catch (err) {
    suggestResult.value = null;
    suggestError.value = safeError(err, "推荐暂时不可用，请稍后重试。");
  } finally {
    suggestLoading.value = false;
  }
}

function applySuggestion() {
  const plan = suggestResult.value;
  if (!plan) return;
  if (plan.strategy) strategy.value = plan.strategy;
  if (plan.mask_level) maskLevel.value = plan.mask_level;
  if (plan.min_level) minLevel.value = plan.min_level;
  enableL2.value = Boolean(plan.enable_l2);
  enableL3.value = Boolean(plan.enable_l3);
  enableNer.value = Boolean(plan.enable_ner) && nerAvailable.value;
  suggestOpen.value = false;
  ElMessage.success("已更新检测配置与导出策略；重新检测可更新预览。");
}

onMounted(async () => {
  // NER 需要本地模型：服务端没启用时把开关禁用，避免用户开了却没效果
  try {
    health.value = await api.health();
    nerAvailable.value = Boolean(health.value.ner);
    nerBackend.value = health.value.ner_backend || "";
    // 服务端默认开 NER 时开关跟着打开；用户仍可手动关掉本次请求的可选识别
    if (!text.value && !file.value) enableNer.value = nerAvailable.value;
  } catch {
    nerAvailable.value = false;
  }
  // 策略/场景加载失败不影响检测主流程，只是下拉退化为内置常用选项
  const [strategyBody, scenarioBody] = await Promise.allSettled([
    api.fetchMaskStrategies(),
    api.fetchScenarios(),
  ]);
  if (strategyBody.status === "fulfilled") {
    strategies.value = strategyBody.value.strategies || [];
    if (strategyBody.value.default) strategy.value = strategyBody.value.default;
  }
  if (scenarioBody.status === "fulfilled") {
    scenarios.value = scenarioBody.value.scenarios || [];
  }
  // 打开即跑一遍示例：答辩/演示时页面一落地就有内容
  // ?ner=1：直接演示自由文本模式（NER 补出人名/地名）
  const wantNer =
    new URLSearchParams(window.location.search).get("ner") === "1";
  if (wantNer && nerAvailable.value) {
    enableNer.value = true;
    text.value = NER_SAMPLE;
  }
  if (
    new URLSearchParams(window.location.search).get("demo") === "1" ||
    wantNer
  ) {
    if (!text.value) text.value = SAMPLE;
    runDetect();
  }
});
</script>

<template>
  <div class="detect-workspace">
    <PageHeader
      title="实时检测"
      description="识别敏感信息，审查脱敏结果，让每一次数据流转都更可控。"
      index="DETECTION"
    />
    <div class="workflow" aria-label="检测工作流">
      <span :class="{ current: !result && !loading }"><b>01</b>数据输入</span>
      <AppIcon name="right" :size="12" />
      <span :class="{ current: loading }"><b>02</b>敏感识别</span>
      <AppIcon name="right" :size="12" />
      <span :class="{ current: result && !loading }"><b>03</b>脱敏与核验</span>
    </div>
    <div class="detection-grid">
      <section class="panel input-workspace" aria-labelledby="source-heading" :aria-busy="busy">
        <header class="detection-panel-head">
          <AppIcon name="terminal" :size="19" />
          <div>
            <h2 id="source-heading">数据输入与策略</h2>
            <p>选择场景、粘贴文本或上传文件。</p>
          </div>
        </header>
        <div class="source-controls">
          <label class="field">场景模板
            <el-select v-model="scenarioKey" aria-label="场景模板" placeholder="自定义配置"
              clearable :disabled="busy" @change="applyScenario" @clear="clearScenario">
              <el-option v-for="item in scenarios" :key="item.key" :label="item.label" :value="item.key" />
            </el-select>
          </label>
          <el-button :disabled="busy" @click="sample"><AppIcon name="file" :size="16" />载入示例</el-button>
        </div>
        <p v-if="activeScenario" class="source-hint">
          {{ activeScenario.description }} 数据保留以{{ activeScenario.retention || "服务端策略" }}为准。
        </p>
        <div class="source-editor">
          <div class="source-editor-head"><label for="detect-input">待检测文本</label><span>自动识别文本 / JSON / CSV / SQL</span></div>
          <el-input id="detect-input" v-model="text" type="textarea" :rows="7" :disabled="busy"
            :maxlength="maxTextLength" aria-label="待检测文本"
            placeholder="在这里粘贴需要检查的内容，或上传一份文档。"
            @keydown="detectShortcut" @paste="checkPaste" />
          <div class="input-state" aria-live="polite">
            <span>{{ text.length.toLocaleString() }} / 200,000 字符</span>
            <span v-if="fileLabel"><AppIcon name="file" :size="12" /> 当前文件 {{ fileLabel }}
              <button class="file-source-switch" type="button" :disabled="busy" @click="switchToText">改用文本</button>
            </span>
            <span v-if="pasteLimit" class="error-text">粘贴内容超出上限，未写入。请缩小内容或上传文件。</span>
            <span v-else-if="text.length >= maxTextLength" class="error-text">已达输入上限，请确认原文是否完整。</span>
          </div>
        </div>
        <div class="source-upload">
          <div class="input-actions">
            <el-upload :auto-upload="false" :show-file-list="false" :on-change="onFileChange"
              :disabled="busy" :accept="fileAccept">
              <el-button :disabled="busy"><AppIcon name="upload" :size="15" />上传文件</el-button>
            </el-upload>
            <el-button :disabled="busy" aria-label="清空工作区" @click="clearInput">
              <AppIcon name="trash" :size="15" />清空
            </el-button>
          </div>
          <p class="hint">支持文本、结构化数据及 Word、Excel、PDF。{{ health?.ocr ? "可上传图片进行 OCR。" : "图片 OCR 未就绪。" }}</p>
        </div>
        <AdvancedOptions>
          <label class="field">脱敏级别
            <el-select v-model="maskLevel" aria-label="脱敏级别" :disabled="busy">
              <el-option label="按实际级别" value="" />
              <el-option v-for="level in ['L3', 'L2', 'L1']" :key="level" :label="`强制 ${level}`" :value="level" />
            </el-select>
          </label>
          <label class="field">结果最低级别
            <el-select v-model="minLevel" aria-label="结果最低级别" :disabled="busy">
              <el-option v-for="level in ['SAFE', 'L1', 'L2', 'L3']" :key="level" :label="`显示 ≥ ${level}`" :value="level" />
            </el-select>
          </label>
          <el-checkbox v-model="enableL2" :disabled="busy">请求 L2 语义</el-checkbox>
          <el-checkbox v-model="enableL3" :disabled="busy">请求 L3 复核</el-checkbox>
          <el-checkbox v-model="enableNer" :disabled="!nerAvailable || busy" :title="nerTitle">
            自由文本 NER{{ nerAvailable ? "" : "（未就绪）" }}
          </el-checkbox>
          <p class="capability-note hint">
            L2 / L3 的默认配置不代表模型在线，实际覆盖以结果为准。NER：{{ nerAvailable ? nerBackend || "已就绪" : "未就绪" }}。OCR：{{ health?.ocr ? health.ocr_backend || "已就绪" : "未就绪" }}。
          </p>
        </AdvancedOptions>
        <footer class="detection-submit">
          <span class="hint">Ctrl / ⌘ + Enter</span>
          <el-button type="primary" :loading="loading" :disabled="busy || (!file && !text.trim())" @click="runDetect">
            <AppIcon v-if="!loading" name="play" :size="15" />{{ loading ? "正在检测" : "开始检测" }}
          </el-button>
        </footer>
      </section>
      <div class="detection-results">
        <section class="panel result-workspace" aria-labelledby="result-heading" :aria-busy="loading || previewLoading">
          <header class="detection-panel-head">
            <AppIcon name="shield" :size="19" />
            <div>
              <h2 id="result-heading">检测与脱敏结果</h2>
              <p>核验服务端返回的脱敏副本。</p>
            </div>
            <span class="result-state" :class="{ attention: stale || detectError || outputIssue || exportError }" role="status">{{ resultState }}</span>
          </header>
          <div class="result-workspace-body">
            <el-alert v-if="detectError" :title="detectError" type="error" show-icon :closable="false" class="detect-error" />
            <div v-if="stale && !exportError" class="inline-notice warning" role="status">
              <AppIcon name="info" :size="16" />输入、检测选项或操作人已变化。请重新检测以生成新结果。
            </div>
            <div v-if="outputIssue" class="inline-notice warning" role="alert">
              <AppIcon name="warning" :size="16" />{{ outputIssue }} 复制与导出已锁定。
            </div>
            <div v-if="exportError" class="inline-notice warning" role="alert">{{ exportError }}</div>
            <template v-if="result">
              <div class="risk-summary" aria-label="风险摘要">
                <span><strong>{{ hitCount }}</strong>{{ result.effective_options?.min_level === "SAFE" ? "敏感命中" : "当前筛选命中" }}</span>
                <RiskIndicator v-for="level in ['L3', 'L2', 'L1', 'SAFE']" :key="level" :level="level" :count="byLevel[level] || 0" />
              </div>
              <dl class="detection-meta">
                <div><dt>检测范围</dt><dd>{{ result.content_type.toUpperCase() }} · {{ result.segments }} 个片段 · {{ result.duration_ms }} ms</dd></div>
                <div><dt>敏感类别</dt><dd>{{ detectedTypes || "当前筛选无类别" }}</dd></div>
                <div v-if="result.result_id"><dt>快照</dt><dd><code>{{ result.result_id.slice(0, 8) }}</code> · {{ hasPreview ? strategyLabelFor(previewStrategy) : "策略待核验" }}</dd></div>
                <div><dt>模型执行</dt><dd>{{ engineSummary }}</dd></div>
              </dl>
            </template>
            <template v-if="hasPreview">
              <div class="preview-controls">
                <span class="hint">{{ fileLabel ? "文件检测不回传原文" : "默认展示脱敏副本" }}</span>
                <el-button v-if="detectSource" size="small" :aria-expanded="showOriginal" @click="showOriginal = !showOriginal">
                  {{ showOriginal ? "隐藏原文" : "核验输入原文" }}
                </el-button>
              </div>
              <div class="compare" :class="{ 'with-original': showOriginal }">
                <EditorPanel v-if="showOriginal" ref="originalEditor" title="输入原文"
                  :text="originalDisplay" :html="originalDisplayHtml"
                  @scroll="(ratio, x) => maskedEditor?.setScroll(ratio, x)" @hit="showHit" />
                <EditorPanel ref="maskedEditor" title="脱敏副本" :text="maskedDisplay" :html="maskedDisplayHtml" masked
                  @scroll="(ratio, x) => originalEditor?.setScroll(ratio, x)">
                  <template #actions>
                    <button class="icon-button" aria-label="复制脱敏副本" title="复制脱敏正文，保留文件结构请导出"
                      :disabled="!canOutput || !maskedText" @click="copyMasked"><AppIcon name="copy" :size="15" /></button>
                  </template>
                  <template #default>未返回命中片段。完整文档可通过导出获取。</template>
                </EditorPanel>
              </div>
              <div v-if="isStructured && segmentList.length > 50" class="table-footer">
                <span>每页 50 个片段。保留结构请导出文件。</span>
                <el-pagination v-model:current-page="segmentPage" :page-size="50" :total="segmentList.length"
                  :pager-count="5" layout="prev, pager, next" size="small" />
              </div>
            </template>
            <EmptyState v-else-if="!detectError && !stale && !outputIssue && !exportError"
              :title="loading ? '服务端正在检测' : previewLoading ? '正在核验脱敏策略' : '还没有检测结果'"
              :description="loading || previewLoading ? '请等待当前请求完成。' : '完成检测后，这里会显示风险摘要、脱敏副本和可核验的快照。'" />
            <div v-if="result && !outputIssue" class="export-bar">
              <label class="field">脱敏策略
                <el-select v-model="strategy" aria-label="导出脱敏策略" :disabled="busy">
                  <el-option v-for="item in strategyOptions" :key="item.key" :label="item.label" :value="item.key" />
                </el-select>
              </label>
              <label class="field">文件格式
                <el-select v-model="exportFmt" aria-label="导出文件格式" :disabled="busy">
                  <el-option v-for="fmt in fmtOptions" :key="fmt" :label="FMT_LABELS[fmt] || fmt" :value="fmt" />
                </el-select>
              </label>
              <p class="capability-note hint">{{ strategyHint }} 预览与导出使用同一检测快照。</p>
              <div class="export-actions">
                <el-button :disabled="busy" @click="openSuggest">AI 推荐方案</el-button>
                <el-button v-if="strategy === 'encrypt'" :disabled="!canOutput" @click="openDecrypt">还原加密内容</el-button>
                <el-button class="export-primary" type="primary" :loading="exportLoading" :disabled="!canOutput"
                  @click="runExport"><AppIcon name="download" :size="15" />导出脱敏文件</el-button>
              </div>
            </div>
          </div>
        </section>
        <section v-if="result" class="panel details-panel">
          <div class="panel-title">
            <span
              >检测结果明细
              <span class="hint" style="margin-left: 8px"
                >{{ findings.length }} 个片段</span
              ></span
            ><el-select
              v-model="levelFilter"
              aria-label="明细风险筛选"
              placeholder="所有级别"
              clearable
              size="small"
              style="width: 132px"
              ><el-option
                v-for="level in ['L3', 'L2', 'L1', 'SAFE']"
                :key="level"
                :label="`${level} ${LEVEL_LABELS[level]}`"
                :value="level"
            /></el-select>
          </div>
          <el-table
            v-if="pagedFindings.length"
            :data="pagedFindings"
            :max-height="380"
            @row-dblclick="showDetail"
            ><el-table-column label="位置与风险" min-width="112">
              <template #default="{ row }">
                <div class="finding-position"><span :title="row.path">{{ row.path }}</span><RiskIndicator :level="row.level" /></div>
              </template>
            </el-table-column><el-table-column
              label="敏感类别"
              min-width="110"
              show-overflow-tooltip
              ><template #default="{ row }">{{
                (row.types || []).join(" / ") || "—"
              }}</template></el-table-column
            ><el-table-column
              v-if="hasPreview" prop="masked"
              label="脱敏副本"
              min-width="110"
              show-overflow-tooltip
            /><el-table-column label="操作" width="62" fixed="right"
              ><template #default="{ row }"
                ><el-button link type="primary" @click="showDetail(row)"
                  >详情</el-button
                ></template
              ></el-table-column
            ></el-table
          >
          <EmptyState
            v-else
            :title="result ? '没有符合筛选条件的结果' : '等待第一次检测'"
            :description="
              result
                ? '可调整风险筛选或最低返回级别后重试。'
                : '粘贴文本或上传文件，然后开始检测。'
            "
          />
          <div v-if="findings.length" class="table-footer">
            <span>点击高亮或「详情」查看规则、区间与判断依据</span
            ><el-pagination
              v-model:current-page="resultPage"
              :page-size="50"
              :total="filteredFindings.length"
              layout="prev, pager, next" :pager-count="5"
              size="small"
            />
          </div>
        </section>
      </div>
    </div>
    <el-dialog v-model="detailOpen" title="命中详情" width="640px"
      ><template v-if="detail"
        ><dl class="kv">
          <dt>片段位置</dt>
          <dd>{{ detail.path }}</dd>
          <dt>风险等级</dt>
          <dd><RiskIndicator :level="detail.level" /></dd>
          <dt>评分 / 命中数</dt>
          <dd>{{ detail.score }} / {{ detail.hits }}</dd>
          <dt>类别</dt>
          <dd>{{ (detail.types || []).join(" / ") || "无" }}</dd>
          <dt>模型链</dt>
          <dd>{{ detail.model_chain }}</dd>
          <dt>降级 / 直接判定</dt>
          <dd>
            {{ detail.degraded ? "是" : "否" }} /
            {{ detail.direct ? "是" : "否" }}
          </dd>
          <dt>判断依据</dt>
          <dd>{{ detail.reason || "服务端未提供额外说明" }}</dd>
        </dl>
        <h4>脱敏副本</h4>
        <div class="detail-content">{{ hasPreview ? detail.masked : "副本未核验，已隐藏。" }}</div>
        <h4>命中规则与区间</h4>
        <el-table :data="detail.spans || []" max-height="220"
          ><el-table-column prop="label" label="类别" /><el-table-column
            prop="rule"
            label="规则"
            show-overflow-tooltip /><el-table-column
            prop="start"
            label="起点"
            width="65" /><el-table-column
            prop="end"
            label="终点"
            width="65" /></el-table></template
    ></el-dialog>
  </div>

  <!-- AI 推荐方案弹窗 -->
  <el-dialog v-model="suggestOpen" title="AI 推荐脱敏方案" width="620px">
    <el-form label-width="88px">
      <el-form-item label="用途描述">
        <el-input
          v-model="suggestForm.prompt"
          type="textarea"
          :rows="3"
          maxlength="200"
          show-word-limit
          placeholder="例如：给外部审计导出近一个月的工单明细。请勿输入真实数据，这里只用来推断用途。"
        />
      </el-form-item>
      <el-form-item label="场景模板">
        <el-select
          v-model="suggestForm.scenario"
          clearable
          placeholder="可选"
          style="width: 240px"
        >
          <el-option
            v-for="item in scenarios"
            :key="item.key"
            :label="item.label"
            :value="item.key"
          />
        </el-select>
      </el-form-item>
    </el-form>
    <div v-if="suggestError" class="hint-line" style="color: #dc2626">
      {{ suggestError }}
    </div>
    <div v-if="suggestResult" style="margin-top: 12px">
      <dl class="kv">
        <dt>推荐策略</dt>
        <dd>{{ strategyLabelFor(suggestResult.strategy) }}</dd>
        <dt>脱敏级别</dt>
        <dd>
          {{ suggestResult.mask_level }}（最低 {{ suggestResult.min_level }}）
        </dd>
        <dt>可选识别</dt>
        <dd>
          L2 {{ suggestResult.enable_l2 ? "开" : "关" }} · L3
          {{ suggestResult.enable_l3 ? "开" : "关" }} · NER
          {{ suggestResult.enable_ner ? "开" : "关" }}
        </dd>
        <dt>数据保留</dt>
        <dd>{{ suggestResult.retention || "—" }}</dd>
        <dt>推荐理由</dt>
        <dd>{{ suggestResult.rationale }}</dd>
        <dt>来源</dt>
        <dd>{{ sourceLabel(suggestResult.source) }}</dd>
      </dl>
    </div>
    <div v-else class="hint-line">
      只上报用途描述、字符数、各级别命中数与类别名，不上报原文和真实值。
    </div>
    <template #footer>
      <el-button @click="suggestOpen = false">关闭</el-button>
      <el-button :loading="suggestLoading" @click="runSuggest"
        >获取推荐</el-button
      >
      <el-button
        type="primary"
        :disabled="!suggestResult"
        @click="applySuggestion"
      >
        应用到当前设置
      </el-button>
    </template>
  </el-dialog>

  <!-- 还原加密内容弹窗 -->
  <el-dialog v-model="decryptOpen" title="还原加密内容" width="640px">
    <div class="hint-line" style="margin-top: 0">
      把含
      <code>enc:</code> 密文的脱敏文本交给本机服务端还原。仅本机还原，请勿外发。
    </div>
    <el-input
      v-model="decryptInput"
      type="textarea"
      :rows="5"
      placeholder="粘贴含 enc: 密文的脱敏文本"
      style="margin-top: 10px"
    />
    <div v-if="decryptError" class="hint-line" style="color: #dc2626">
      {{ decryptError }}
    </div>
    <div v-if="decryptResult" style="margin-top: 12px">
      <div class="pane">
        <div class="pane-head">
          还原结果（{{ decryptResult.replaced }} 个密文片段 · 策略
          {{ decryptResult.strategy }}）
        </div>
        <div class="pane-body">{{ decryptResult.text }}</div>
      </div>
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        style="margin-top: 10px"
      >
        还原结果含明文敏感信息，只在本机查看，请勿复制外发或写入工单。
      </el-alert>
    </div>
    <template #footer>
      <el-button @click="decryptOpen = false">关闭</el-button>
      <el-button type="primary" :loading="decryptLoading" @click="runDecrypt"
        >还原</el-button
      >
    </template>
  </el-dialog>
</template>
