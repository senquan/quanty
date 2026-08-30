<script lang="ts" setup>
import { onMounted, reactive, ref, watch } from 'vue';
import { useClipboard } from '@vueuse/core';
import {
  Copy,
  Edit,
  Plus,
  Search,
  Trash2,
  Upload,
} from '@lucide/vue';
import {
  ElButton,
  ElCard,
  ElCol,
  ElDialog,
  ElForm,
  ElFormItem,
  ElInput,
  ElMessage,
  ElMessageBox,
  ElRow,
  ElTable,
  ElTableColumn,
} from 'element-plus';
import type { FormInstance, FormRules } from 'element-plus';

import {
  bulkCreateWatchlistApi,
  createWatchlistApi,
  deleteWatchlistApi,
  getWatchlistApi,
  updateWatchlistApi,
  type WatchlistItem,
} from '#/api/watchlist';

const items = ref<WatchlistItem[]>([]);
const loading = ref(false);
const searchKeyword = ref('');

function formatDateTime(value?: null | string) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

async function fetchItems() {
  loading.value = true;
  try {
    const search = searchKeyword.value.trim();
    items.value = await getWatchlistApi({ search: search || undefined });
  } catch {
    ElMessage.error('加载自选股失败');
  } finally {
    loading.value = false;
  }
}

// ============ 单个添加 / 编辑 ============
const dialogVisible = ref(false);
const dialogMode = ref<'create' | 'edit'>('create');
const submitting = ref(false);
const formRef = ref<FormInstance>();
const form = reactive<{ id?: number; code: string; name: string; note: string }>({
  id: undefined,
  code: '',
  name: '',
  note: '',
});

const rules: FormRules = {
  code: [{ required: true, message: '请输入股票代码', trigger: 'blur' }],
};

function openCreate() {
  dialogMode.value = 'create';
  form.id = undefined;
  form.code = '';
  form.name = '';
  form.note = '';
  dialogVisible.value = true;
}

function openEdit(row: WatchlistItem) {
  dialogMode.value = 'edit';
  form.id = row.id;
  form.code = row.code;
  form.name = row.name || '';
  form.note = row.note || '';
  dialogVisible.value = true;
}

async function submitForm() {
  if (!formRef.value) return;
  await formRef.value.validate(async (valid) => {
    if (!valid) return;
    submitting.value = true;
    try {
      if (dialogMode.value === 'create') {
        await createWatchlistApi({
          code: form.code,
          name: form.name || undefined,
          note: form.note || undefined,
        });
        ElMessage.success('已添加');
      } else {
        await updateWatchlistApi(form.id!, {
          name: form.name || undefined,
          note: form.note || undefined,
        });
        ElMessage.success('已保存');
      }
      dialogVisible.value = false;
      await fetchItems();
    } catch {
      // 错误提示由拦截器统一处理
    } finally {
      submitting.value = false;
    }
  });
}

// ============ 批量导入 ============
const bulkVisible = ref(false);
const bulkText = ref('');
const bulkSubmitting = ref(false);

function openBulk() {
  bulkText.value = '';
  bulkVisible.value = true;
}

function parseBulk(text: string) {
  const out: { code: string; name?: string }[] = [];
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const parts = trimmed.split(/[\s,，、]+/).filter(Boolean);
    if (!parts.length) continue;
    const code = parts[0]!;
    const rest = parts.slice(1);
    out.push({ code, name: rest.length ? rest.join(' ') : undefined });
  }
  return out;
}

async function submitBulk() {
  const parsed = parseBulk(bulkText.value);
  if (!parsed.length) {
    ElMessage.warning('请先输入至少一个股票代码');
    return;
  }
  bulkSubmitting.value = true;
  try {
    const created = await bulkCreateWatchlistApi(parsed);
    ElMessage.success(`成功导入 ${created.length} 只（重复已跳过）`);
    bulkVisible.value = false;
    await fetchItems();
  } catch {
    // 错误提示由拦截器统一处理
  } finally {
    bulkSubmitting.value = false;
  }
}

// ============ 删除 / 复制 ============
const { copy } = useClipboard();

function handleDelete(row: WatchlistItem) {
  ElMessageBox.confirm(`确定将「${row.code}${row.name ? ` ${row.name}` : ''}」移出自选股吗？`, '提示', {
    type: 'warning',
    confirmButtonText: '删除',
    cancelButtonText: '取消',
  })
    .then(async () => {
      try {
        await deleteWatchlistApi(row.id);
        ElMessage.success('已删除');
        await fetchItems();
      } catch {
        ElMessage.error('删除失败');
      }
    })
    .catch(() => {});
}

async function copyAllCodes() {
  if (!items.value.length) {
    ElMessage.warning('自选股为空');
    return;
  }
  const codes = items.value.map((i) => i.code).join(', ');
  await copy(codes);
  ElMessage.success('已复制全部代码到剪贴板');
}

onMounted(() => {
  fetchItems();
});

watch(searchKeyword, () => {
  fetchItems();
});
</script>

<template>
  <div class="watchlist-page p-4">
    <ElCard shadow="never">
      <template #header>
        <ElRow justify="space-between" align="middle">
          <ElCol>
            <div class="flex items-center gap-2">
              <ElInput
                v-model="searchKeyword"
                placeholder="搜索代码或名称..."
                clearable
                style="width: 260px"
              >
                <template #prefix>
                  <Search class="w-4 h-4" />
                </template>
              </ElInput>
              <ElButton type="primary" @click="openCreate">
                <Plus class="w-4 h-4 mr-1" />
                添加自选股
              </ElButton>
              <ElButton @click="openBulk">
                <Upload class="w-4 h-4 mr-1" />
                批量导入
              </ElButton>
              <ElButton @click="copyAllCodes">
                <Copy class="w-4 h-4 mr-1" />
                复制全部代码
              </ElButton>
            </div>
          </ElCol>
          <ElCol>
            <span class="text-xs text-gray-400">共 {{ items.length }} 只</span>
          </ElCol>
        </ElRow>
      </template>

      <ElTable
        v-loading="loading"
        :data="items"
        stripe
        empty-text="暂无自选股，点击「添加自选股」或「批量导入」"
      >
        <ElTableColumn prop="code" label="代码" width="140">
          <template #default="{ row }">
            <span class="font-mono font-medium">{{ row.code }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="name" label="名称" min-width="160">
          <template #default="{ row }">
            <span v-if="row.name">{{ row.name }}</span>
            <span v-else class="text-gray-300">—</span>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="note" label="备注" min-width="200">
          <template #default="{ row }">
            <span v-if="row.note" class="text-gray-600">{{ row.note }}</span>
            <span v-else class="text-gray-300">—</span>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="created_at" label="添加时间" width="170">
          <template #default="{ row }">
            {{ formatDateTime(row.created_at) }}
          </template>
        </ElTableColumn>
        <ElTableColumn label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <ElButton link type="primary" size="small" @click="openEdit(row)">
              <Edit class="w-4 h-4 mr-1" />编辑
            </ElButton>
            <ElButton link type="danger" size="small" @click="handleDelete(row)">
              <Trash2 class="w-4 h-4" />
            </ElButton>
          </template>
        </ElTableColumn>
      </ElTable>
    </ElCard>

    <!-- 单个添加 / 编辑 -->
    <ElDialog
      v-model="dialogVisible"
      :title="dialogMode === 'create' ? '添加自选股' : '编辑自选股'"
      width="460px"
    >
      <ElForm ref="formRef" :model="form" :rules="rules" label-width="72px">
        <ElFormItem label="代码" prop="code">
          <ElInput
            v-model="form.code"
            :disabled="dialogMode === 'edit'"
            placeholder="如 600519.SH / 000001.SZ"
          />
        </ElFormItem>
        <ElFormItem label="名称">
          <ElInput v-model="form.name" placeholder="可选，如 贵州茅台" />
        </ElFormItem>
        <ElFormItem label="备注">
          <ElInput
            v-model="form.note"
            type="textarea"
            :rows="3"
            placeholder="可选备注"
          />
        </ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton @click="dialogVisible = false">取消</ElButton>
        <ElButton type="primary" :loading="submitting" @click="submitForm">
          保存
        </ElButton>
      </template>
    </ElDialog>

    <!-- 批量导入 -->
    <ElDialog v-model="bulkVisible" title="批量导入自选股" width="520px">
      <p class="text-xs text-gray-400 mb-2">
        每行一个，代码与名称用空格分隔，如：<br />
        <span class="font-mono">600519.SH 贵州茅台</span><br />
        <span class="font-mono">000001.SZ 平安银行</span>
      </p>
      <ElInput
        v-model="bulkText"
        type="textarea"
        :rows="10"
        placeholder="粘贴代码列表，支持空格/逗号/换行分隔"
      />
      <template #footer>
        <ElButton @click="bulkVisible = false">取消</ElButton>
        <ElButton type="primary" :loading="bulkSubmitting" @click="submitBulk">
          导入
        </ElButton>
      </template>
    </ElDialog>
  </div>
</template>

<style scoped>
.watchlist-page {
  min-height: 100%;
}
</style>
