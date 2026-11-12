<script setup>
import { safeError } from "../workspace";
import PageHeader from "../components/PageHeader.vue";
import AppIcon from "../components/AppIcon.vue";
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { api } from "../api";

const rules = ref([]);
const query = ref("");
const filteredRules = computed(() =>
  rules.value.filter((row) =>
    `${row.name} ${row.type} ${row.pattern}`
      .toLowerCase()
      .includes(query.value.toLowerCase()),
  ),
);
const loading = ref(false);
const dialogVisible = ref(false);
const form = ref({
  name: "",
  type: "phone",
  pattern: "",
  weight: 0.5,
  confidence: 0.8,
});
const TYPES = [
  "phone",
  "id_card",
  "bank_card",
  "email",
  "ip",
  "secret",
  "password",
  "name",
  "address",
  "medical",
];

async function load() {
  loading.value = true;
  try {
    rules.value = await api.rules();
  } catch (err) {
    ElMessage.error(`加载规则失败：${safeError(err)}`);
  } finally {
    loading.value = false;
  }
}

async function toggle(row) {
  try {
    await api.toggleRule(row.id, row.enabled);
    ElMessage.success(`${row.name} 已${row.enabled ? "启用" : "停用"}`);
  } catch (err) {
    row.enabled = !row.enabled;
    ElMessage.error(`更新失败：${safeError(err)}`);
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(
      `删除自定义规则「${row.name}」？内置规则不受影响。`,
      "确认",
      {
        type: "warning",
        confirmButtonText: "删除规则",
        cancelButtonText: "取消",
      },
    );
    await api.deleteRule(row.id);
    ElMessage.success("已删除");
    load();
  } catch (err) {
    if (err !== "cancel") ElMessage.error(`删除失败：${safeError(err)}`);
  }
}

async function submit() {
  try {
    await api.addRule(form.value);
    ElMessage.success("规则已添加并即时生效");
    dialogVisible.value = false;
    form.value = {
      name: "",
      type: "phone",
      pattern: "",
      weight: 0.5,
      confidence: 0.8,
    };
    load();
  } catch (err) {
    ElMessage.error(`添加失败：${safeError(err)}`);
  }
}

onMounted(load);
</script>

<template>
  <PageHeader
    title="识别规则"
    description="管理识别边界与置信度，自定义规则保存后即时生效。"
    index="DETECTION RULES"
  />
  <div class="metric-strip">
    <div class="metric">
      <span>全部规则</span><strong>{{ rules.length }}</strong>
    </div>
    <div class="metric">
      <span>已启用</span
      ><strong>{{ rules.filter((r) => r.enabled).length }}</strong>
    </div>
    <div class="metric">
      <span>自定义规则</span
      ><strong>{{ rules.filter((r) => !r.builtin).length }}</strong>
    </div>
  </div>
  <div class="panel">
    <div class="panel-title">
      规则库
      <span class="hint"
        >内置 {{ rules.filter((r) => r.builtin).length }} 条 · 自定义
        {{ rules.filter((r) => !r.builtin).length }} 条</span
      >
    </div>
    <div class="toolbar">
      <el-input
        v-model="query"
        clearable
        aria-label="搜索识别规则"
        placeholder="搜索规则名称、类别或正则…"
        style="width: 290px"
        ><template #prefix><AppIcon name="search" :size="16" /></template
      ></el-input>
      <el-button type="primary" @click="dialogVisible = true"
        >新增规则</el-button
      >
      <el-button :loading="loading" @click="load">刷新</el-button>
    </div>
    <el-table
      :data="filteredRules"
      stripe
      style="margin-top: 12px"
      v-loading="loading"
    >
      <el-table-column prop="name" label="规则名" width="150" />
      <el-table-column prop="type" label="类别" width="110" />
      <el-table-column prop="pattern" label="正则" show-overflow-tooltip />
      <el-table-column prop="weight" label="权重" width="80" />
      <el-table-column prop="confidence" label="置信度" width="90" />
      <el-table-column label="来源" width="100">
        <template #default="scope">
          <el-tag :type="scope.row.builtin ? 'info' : 'success'" size="small">
            {{ scope.row.builtin ? "内置" : "自定义" }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="启用" width="90">
        <template #default="scope">
          <el-switch v-model="scope.row.enabled" @change="toggle(scope.row)" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="90">
        <template #default="scope">
          <el-button
            size="small"
            type="danger"
            :disabled="scope.row.builtin"
            @click="remove(scope.row)"
          >
            删除
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>

  <el-dialog v-model="dialogVisible" title="新增识别规则" width="560px">
    <el-form :model="form" label-width="88px">
      <el-form-item label="规则名">
        <el-input v-model="form.name" placeholder="例如 employee_no" />
      </el-form-item>
      <el-form-item label="类别">
        <el-select v-model="form.type" style="width: 100%">
          <el-option
            v-for="type in TYPES"
            :key="type"
            :label="type"
            :value="type"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="正则">
        <el-input v-model="form.pattern" placeholder="例如 (E[0-9]{6})" />
        <div class="hint">
          使用至少一个 (...) 捕获组标记需要脱敏的片段；正则由服务端 Python
          引擎校验。
        </div>
      </el-form-item>
      <el-form-item label="权重">
        <el-input-number v-model="form.weight" :min="0" :max="1" :step="0.05" />
      </el-form-item>
      <el-form-item label="置信度">
        <el-input-number
          v-model="form.confidence"
          :min="0"
          :max="1"
          :step="0.01"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" @click="submit">保存并生效</el-button>
    </template>
  </el-dialog>
</template>
