<script setup>
import { safeError } from "../workspace";
import PageHeader from "../components/PageHeader.vue";
import AppIcon from "../components/AppIcon.vue";
import RiskIndicator from "../components/RiskIndicator.vue";
import { onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { api } from "../api";
import { renderHighlight } from "../highlight";

const SAMPLE = `客户张三，手机 13800138000，身份证 11010519491231002X，收货地址：福建省福州市鼓楼区五四路 100 号。`;

const text = ref(SAMPLE);
const authorized = ref(false);
const maskLevel = ref("");
const operator = ref("auditor-01");
const result = ref(null);
const policy = ref([]);
const loading = ref(false);
const lastAuthorizedRequest = ref(null);

async function render() {
  if (!text.value.trim()) {
    ElMessage.warning("先输入文本");
    return;
  }
  loading.value = true;
  try {
    const requested = authorized.value;
    result.value = await api.dynamicRender(
      {
        text: text.value,
        authorized: authorized.value,
        mask_level: maskLevel.value || null,
      },
      operator.value,
    );
    lastAuthorizedRequest.value = requested;
  } catch (err) {
    ElMessage.error(`渲染失败：${safeError(err)}`);
  } finally {
    loading.value = false;
  }
}

async function loadPolicy() {
  try {
    const body = await api.dynamicPolicy();
    policy.value = body.levels || [];
  } catch (err) {
    ElMessage.error(`加载策略失败：${safeError(err)}`);
  }
}

onMounted(async () => {
  await loadPolicy();
  await render();
});
</script>

<template>
  <PageHeader
    title="动态脱敏与授权"
    description="按授权返回不同数据视图，每次访问均记录到审计链。"
    index="ACCESS CONTROL"
  />
  <div class="panel">
    <div class="panel-title">
      动态脱敏视图（原文不落库，按授权切换）
      <span class="hint">授权查看含敏感原文，每次渲染都会留存审计记录</span>
    </div>
    <el-input
      v-model="text"
      type="textarea"
      :rows="4"
      placeholder="输入待渲染的文本"
    />
    <div class="toolbar">
      <el-switch
        v-model="authorized"
        active-text="授权查看原文（留审计痕迹）"
      />
      <el-select v-model="maskLevel" style="width: 170px">
        <el-option label="按实际分级" value="" />
        <el-option label="强制 L3" value="L3" />
        <el-option label="强制 L2" value="L2" />
        <el-option label="强制 L1" value="L1" />
      </el-select>
      <el-input v-model="operator" placeholder="操作人" style="width: 160px" />
      <el-button type="primary" :loading="loading" @click="render"
        >渲染</el-button
      >
      <el-button @click="text = SAMPLE">恢复示例</el-button>
    </div>
  </div>

  <div
    v-if="result && authorized !== lastAuthorizedRequest"
    class="inline-notice warning"
  >
    <AppIcon
      name="info"
      :size="16"
    />授权选项已修改，请点击「渲染」提交。下方仍是上次响应的访问权限。
  </div>
  <div
    v-if="result && lastAuthorizedRequest && !result.authorized"
    class="inline-notice warning"
    role="status"
  >
    <AppIcon
      name="dynamic"
      :size="16"
    />服务端未授予原文访问权限，当前仅展示脱敏副本。授权以服务端策略为准。
  </div>
  <div class="panel" v-if="result">
    <div class="panel-title">
      渲染结果
      <span class="hint">
        策略
        {{ result.policy === "full_access" ? "授权访问原文" : "仅脱敏副本" }} ·
        命中审计链（操作人 {{ result.operator_id }}）
      </span>
    </div>
    <div class="compare">
      <div class="pane">
        <div class="pane-head">脱敏副本（未授权可见）</div>
        <div class="pane-body">{{ result.masked }}</div>
      </div>
      <div class="pane">
        <div class="pane-head">原文（授权后可见，命中处高亮）</div>
        <div class="pane-body">
          <template v-if="result.authorized && authorized">
            <span v-html="renderHighlight(result.text, result.spans)"></span>
          </template>
          <span v-else class="empty" style="padding: 0">
            未授权：开启上方「授权查看原文」并点击「渲染」后显示，访问会写入审计链。
          </span>
        </div>
      </div>
    </div>
    <div class="legend">
      <RiskIndicator :level="result.level" />
      <span class="chip pale">分数 {{ result.score }}</span>
      <span class="chip pale"
        >类别 {{ (result.type_labels || []).join("、") || "无" }}</span
      >
      <span class="chip pale">模型链 {{ result.model_chain }}</span>
      <span class="chip pale">策略 {{ result.policy }}</span>
    </div>
    <div class="hint" style="margin-top: 10px; color: var(--muted)">
      {{ result.note }}
    </div>
  </div>

  <div class="panel">
    <div class="panel-title">脱敏策略表（可解释、可审计）</div>
    <el-table :data="policy" stripe>
      <el-table-column label="级别" width="120">
        <template #default="scope">
          <RiskIndicator :level="scope.row.level" />
        </template>
      </el-table-column>
      <el-table-column prop="without_auth" label="未授权" />
      <el-table-column prop="with_auth" label="授权后" />
    </el-table>
  </div>
</template>
