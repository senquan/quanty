<script lang="ts" setup>
import { nextTick, onMounted, reactive, ref } from 'vue';

import { formatDate } from '@vben/utils';

import {
  ElButton,
  ElCard,
  ElDialog,
  ElForm,
  ElFormItem,
  ElInput,
  ElMessage,
  ElMessageBox,
  ElOption,
  ElPagination,
  ElSelect,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import {
  type CleanerServiceCreatePayload,
  type CleanerServiceItem,
  deleteCleanerServiceApi,
  type FactorRegistryItem,
  importCleanerFactorsApi,
  listCleanerServiceFactorsApi,
  listCleanerServicesApi,
  listFactorRegistryApi,
  pollCleanerQosApi,
  registerCleanerServiceApi,
  type RemoteFactorItem,
  syncCleanerFactorsApi,
  testCleanerServiceApi,
} from '#/api/cleaner-gateway';

// ============ 服务列表 ============
const loading = ref(false);
const services = ref<CleanerServiceItem[]>([]);
/** 行内按钮 loading 标记：key = `${code}:${action}` */
const busy = reactive<Record<string, boolean>>({});

function busyKey(code: string, action: string) {
  return `${code}:${action}`;
}

async function loadServices() {
  loading.value = true;
  try {
    services.value = await listCleanerServicesApi();
  } catch (error: any) {
    ElMessage.error(`加载服务列表失败: ${error?.msg ?? error?.message ?? error}`);
  } finally {
    loading.value = false;
  }
}

function statusTagType(s: CleanerServiceItem): 'danger' | 'info' | 'success' | 'warning' {
  if (!s.is_active) return 'info';
  if (s.status === 'online') return 'success';
  if (s.status === 'degraded') return 'warning';
  return 'danger';
}

// ============ 新建服务 ============
const dialogVisible = ref(false);
const saving = ref(false);
const createForm = reactive<CleanerServiceCreatePayload>({
  service_code: '',
  name: '',
  base_url: '',
  api_key: '',
});

function openCreate() {
  createForm.service_code = '';
  createForm.name = '';
  createForm.base_url = '';
  createForm.api_key = '';
  dialogVisible.value = true;
}

async function saveService() {
  if (!createForm.service_code || !createForm.name || !createForm.base_url) {
    ElMessage.warning('请填写服务编码、名称与地址');
    return;
  }
  saving.value = true;
  try {
    await registerCleanerServiceApi({ ...createForm });
    ElMessage.success(`服务「${createForm.name}」注册成功`);
    dialogVisible.value = false;
    await loadServices();
    await loadRegistry();
  } catch (error: any) {
    ElMessage.error(`注册失败: ${error?.msg ?? error?.message ?? error}`);
  } finally {
    saving.value = false;
  }
}

// ============ 服务运维操作 ============
async function handleTest(s: CleanerServiceItem) {
  const k = busyKey(s.service_code, 'test');
  busy[k] = true;
  try {
    const r = await testCleanerServiceApi(s.service_code);
    if (r.ok) {
      ElMessage.success(`连接正常（状态：${r.status ?? '—'}，因子数：${r.factor_count ?? '—'}）`);
    } else {
      ElMessage.warning(`连接异常：${r.message ?? '未知原因'}`);
    }
  } catch (error: any) {
    ElMessage.error(`测试失败: ${error?.msg ?? error?.message ?? error}`);
  } finally {
    busy[k] = false;
  }
}

async function handleQos(s: CleanerServiceItem) {
  const k = busyKey(s.service_code, 'qos');
  busy[k] = true;
  try {
    await pollCleanerQosApi(s.service_code);
    ElMessage.success('QoS 轮询已更新');
    await loadServices();
  } catch (error: any) {
    ElMessage.error(`QoS 轮询失败: ${error?.msg ?? error?.message ?? error}`);
  } finally {
    busy[k] = false;
  }
}

// ============ 选择因子（分页弹窗） ============
const pickerVisible = ref(false);
const pickerService = ref<CleanerServiceItem | null>(null);
const pickerLoading = ref(false);
const pickerImporting = ref(false);
const pickerRows = ref<RemoteFactorItem[]>([]);
const pickerTotal = ref(0);
const pickerPage = ref(1);
const pickerPageSize = ref(10);
const pickerKeyword = ref('');
const pickerCategory = ref('');
const pickerTableRef = ref();
/** 已勾选的因子代码（跨分页保留） */
const pickerSelected = ref<Set<string>>(new Set());
/** 类别下拉选项（远端因子库去重得出） */
const categories = ref<string[]>([]);

async function loadCategories() {
  const svc = pickerService.value;
  if (!svc) return;
  try {
    const res = await listCleanerServiceFactorsApi(svc.service_code, {
      page: 1,
      page_size: 500,
    });
    const set = new Set<string>();
    (res.items ?? []).forEach((i) => i.category && set.add(i.category));
    categories.value = [...set].toSorted();
  } catch {
    categories.value = [];
  }
}

async function loadPickerFactors() {
  const svc = pickerService.value;
  if (!svc) return;
  pickerLoading.value = true;
  try {
    const res = await listCleanerServiceFactorsApi(svc.service_code, {
      page: pickerPage.value,
      page_size: pickerPageSize.value,
      category: pickerCategory.value || undefined,
      search: pickerKeyword.value || undefined,
    });
    pickerRows.value = res.items ?? [];
    pickerTotal.value = res.total ?? 0;
    // 数据回来后回填本页勾选状态
    await nextTick();
    pickerRows.value.forEach((row) => {
      pickerTableRef.value?.toggleRowSelection?.(
        row,
        pickerSelected.value.has(row.code),
      );
    });
  } catch (error: any) {
    ElMessage.error(
      `加载因子库失败: ${error?.msg ?? error?.message ?? error}`,
    );
    pickerRows.value = [];
    pickerTotal.value = 0;
  } finally {
    pickerLoading.value = false;
  }
}

function openPicker(s: CleanerServiceItem) {
  pickerService.value = s;
  pickerPage.value = 1;
  pickerKeyword.value = '';
  pickerCategory.value = '';
  pickerSelected.value = new Set();
  pickerVisible.value = true;
  loadPickerFactors();
  loadCategories();
}

/** 本页勾选变化：先清掉本页记录，再按当前选中重建 */
function handlePickerSelectionChange(rows: RemoteFactorItem[]) {
  const pageCodes = pickerRows.value.map((r) => r.code);
  pageCodes.forEach((c) => pickerSelected.value.delete(c));
  rows.forEach((r) => pickerSelected.value.add(r.code));
}

function handlePickerPageChange(page: number) {
  pickerPage.value = page;
  loadPickerFactors();
}

function handlePickerSizeChange(size: number) {
  pickerPageSize.value = size;
  pickerPage.value = 1;
  loadPickerFactors();
}

/** 全量同步（等价于导入该服务的全部因子） */
async function handleSyncAll() {
  const svc = pickerService.value;
  if (!svc) return;
  pickerImporting.value = true;
  try {
    const r = await syncCleanerFactorsApi(svc.service_code);
    ElMessage.success(`同步完成：入库 ${r.synced} 个因子（服务状态 ${r.status}）`);
    await loadServices();
    await loadRegistry();
    await loadPickerFactors();
  } catch (error: any) {
    ElMessage.error(`同步失败: ${error?.msg ?? error?.message ?? error}`);
  } finally {
    pickerImporting.value = false;
  }
}

/** 确认入库：按勾选的因子写入 factor_registry */
async function confirmImport() {
  const svc = pickerService.value;
  if (!svc) return;
  const codes = [...pickerSelected.value];
  if (codes.length === 0) {
    ElMessage.warning('请至少勾选一个因子');
    return;
  }
  pickerImporting.value = true;
  try {
    const r = await importCleanerFactorsApi(svc.service_code, {
      factor_codes: codes,
    });
    ElMessage.success(
      `入库完成：新增 ${r.created} 个，更新 ${r.updated} 个`,
    );
    pickerVisible.value = false;
    await loadServices();
    await loadRegistry();
  } catch (error: any) {
    ElMessage.error(`入库失败: ${error?.msg ?? error?.message ?? error}`);
  } finally {
    pickerImporting.value = false;
  }
}

async function handleDelete(s: CleanerServiceItem) {
  await ElMessageBox.confirm(
    `确认删除清洗服务「${s.name}」？其下因子登记也会一并移除。`,
    '删除确认',
    { type: 'warning' },
  );
  const k = busyKey(s.service_code, 'del');
  busy[k] = true;
  try {
    await deleteCleanerServiceApi(s.service_code);
    ElMessage.success('服务已删除');
    await loadServices();
    await loadRegistry();
  } catch (error: any) {
    ElMessage.error(`删除失败: ${error?.msg ?? error?.message ?? error}`);
  } finally {
    busy[k] = false;
  }
}

// ============ 因子聚合注册表 ============
const registryLoading = ref(false);
const registry = ref<FactorRegistryItem[]>([]);
const selectedService = ref<string>('');   // '' = 全部
const onlyEnabled = ref(false);

// const serviceOptions = computed(() =>
//   services.value.map((s) => ({ label: `${s.name}（${s.service_code}）`, value: s.service_code })),
// );

async function loadRegistry() {
  registryLoading.value = true;
  try {
    registry.value = await listFactorRegistryApi({
      service_code: selectedService.value || undefined,
      only_enabled: onlyEnabled.value,
    });
  } catch (error: any) {
    ElMessage.error(`加载因子注册表失败: ${error?.msg ?? error?.message ?? error}`);
  } finally {
    registryLoading.value = false;
  }
}

/** 切换单个因子的启用状态 */
// async function toggleFactorEnabled(row: FactorRegistryItem, val: boolean) {
//   try {
//     await enableCleanerFactorsApi(row.service_code, {
//       factor_codes: [row.factor_code],
//       is_enabled: val,
//     });
//     row.is_enabled = val;
//     ElMessage.success(val ? '已启用入库' : '已取消入库');
//   } catch (error: any) {
//     row.is_enabled = !val; // 回滚
//     ElMessage.error(`更新失败: ${error?.msg ?? error?.message ?? error}`);
//   }
// }

onMounted(() => {
  loadServices();
  loadRegistry();
});
</script>

<template>
  <div class="cleaner-service">
    <!-- 清洗服务列表 -->
    <ElCard shadow="never" class="mb-4">
      <template #header>
        <div class="flex items-center justify-between">
          <span class="font-medium">清洗服务（多实例网关）</span>
          <ElButton type="primary" @click="openCreate">注册服务</ElButton>
        </div>
      </template>

      <ElTable v-loading="loading" :data="services" stripe border>
        <ElTableColumn prop="service_code" label="编码" width="140" fixed />
        <ElTableColumn prop="name" label="名称" min-width="200" />
        <ElTableColumn prop="base_url" label="地址" width="300" show-overflow-tooltip />
        <ElTableColumn label="状态" width="110" align="center">
          <template #default="{ row }">
            <ElTag :type="statusTagType(row)" size="small">
              {{ row.is_active ? row.status : '已停用' }}
            </ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="last_heartbeat" label="最近心跳" width="160" align="center">
          <template #default="{ row }">
            {{ row.last_heartbeat ? formatDate(row.last_heartbeat, 'YYYY-MM-DD HH:mm:ss') : '-' }}
          </template>
        </ElTableColumn>
        <ElTableColumn label="操作" width="360" fixed="right" align="center">
          <template #default="{ row }">
            <ElButton
              type="primary"
              :loading="busy[busyKey(row.service_code, 'test')]"
              @click="handleTest(row)"
            >
              测试
            </ElButton>
            <ElButton
              type="primary"
              :loading="busy[busyKey(row.service_code, 'qos')]"
              @click="handleQos(row)"
            >
              QoS
            </ElButton>
            <ElButton type="primary" @click="openPicker(row)">选择因子</ElButton>
            <ElButton
              type="danger"
              :loading="busy[busyKey(row.service_code, 'del')]"
              @click="handleDelete(row)"
            >
              删除
            </ElButton>
          </template>
        </ElTableColumn>
        <template #empty>
          <el-empty description="尚未注册任何清洗服务，点击右上角「注册服务」" :image-size="80" />
        </template>
      </ElTable>
    </ElCard>

    <!-- 因子聚合注册表 -->
    <!-- <el-card shadow="never">
      <template #header>
        <div class="flex items-center justify-between">
          <span class="font-medium">因子聚合注册表（FactorRegistry）</span>
          <div class="flex items-center gap-2">
            <el-select
              v-model="selectedService"
              placeholder="全部服务"
              clearable
              class="w-52"
              @change="loadRegistry"
            >
              <el-option
                v-for="s in serviceOptions"
                :key="s.value"
                :label="s.label"
                :value="s.value"
              />
            </el-select>
            <el-radio-group v-model="onlyEnabled" @change="loadRegistry">
              <el-radio :value="false">全部</el-radio>
              <el-radio :value="true">仅启用</el-radio>
            </el-radio-group>
            <el-button size="small" @click="loadRegistry">刷新</el-button>
          </div>
        </div>
      </template>

      <el-table v-loading="registryLoading" :data="registry" stripe border>
        <el-table-column prop="service_code" label="服务" width="140" />
        <el-table-column prop="factor_code" label="因子代码" width="140" />
        <el-table-column prop="name" label="名称" min-width="130" />
        <el-table-column prop="category" label="类别" width="100" />
        <el-table-column prop="frequency" label="频率" width="80" />
        <el-table-column prop="data_source" label="数据源" width="100" />
        <el-table-column prop="formula" label="公式" min-width="180" show-overflow-tooltip />
        <el-table-column prop="last_sync" label="同步时间" width="170" />
        <el-table-column label="入库" width="90" fixed="right">
          <template #default="{ row }">
            <el-switch
              :model-value="row.is_enabled"
              @change="(v: string | number | boolean) => toggleFactorEnabled(row, !!v)"
            />
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无因子登记，请先在上方「同步因子」" :image-size="80" />
        </template>
      </el-table>
    </el-card> -->

    <!-- 注册服务对话框 -->
    <ElDialog
      v-model="dialogVisible"
      title="注册清洗服务"
      width="520px"
      destroy-on-close
    >
      <ElForm :model="createForm" label-width="90px" @submit.prevent>
        <ElFormItem label="服务编码" required>
          <ElInput v-model="createForm.service_code" placeholder="如 cleaner_a" />
        </ElFormItem>
        <ElFormItem label="名称" required>
          <ElInput v-model="createForm.name" placeholder="如 清洗服务A" />
        </ElFormItem>
        <ElFormItem label="地址" required>
          <ElInput
            v-model="createForm.base_url"
            placeholder="如 http://127.0.0.1:8100"
          />
        </ElFormItem>
        <ElFormItem label="API Key" required>
          <ElInput
            v-model="createForm.api_key"
            type="password"
            show-password
            placeholder="与 data-cleaner 的 CLEANER_API_KEY 一致"
          />
        </ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton @click="dialogVisible = false">取消</ElButton>
        <ElButton type="primary" :loading="saving" @click="saveService">注册</ElButton>
      </template>
    </ElDialog>

    <!-- 选择因子对话框（分页列出该清洗服务的因子库） -->
    <ElDialog
      v-model="pickerVisible"
      :title="
        pickerService
          ? `选择因子 — ${pickerService.name}（${pickerService.service_code}）`
          : '选择因子'
      "
      width="60%"
      destroy-on-close
    >
      <div class="mb-3 flex items-center gap-2">
        <ElInput
          v-model="pickerKeyword"
          placeholder="因子代码 / 名称"
          clearable
          class="w-56"
          @keyup.enter="() => { pickerPage = 1; loadPickerFactors(); }"
          @clear="() => { pickerPage = 1; loadPickerFactors(); }"
        />
        <ElSelect
          v-model="pickerCategory"
          placeholder="全部类别"
          clearable
          class="w-40"
          @change="() => { pickerPage = 1; loadPickerFactors(); }"
        >
          <ElOption
            v-for="c in categories"
            :key="c"
            :label="c"
            :value="c"
          />
        </ElSelect>
        <ElButton
          size="default"
          :loading="pickerLoading"
          @click="() => { pickerPage = 1; loadPickerFactors(); }"
        >
          查询
        </ElButton>
        <ElButton size="default" :loading="pickerImporting" @click="handleSyncAll">
          全量同步
        </ElButton>
      </div>

      <ElTable
        ref="pickerTableRef"
        v-loading="pickerLoading"
        :data="pickerRows"
        row-key="code"
        stripe
        show-overflow-tooltip
        border
        max-height="500"
        @selection-change="handlePickerSelectionChange"
      >
        <ElTableColumn type="selection" width="50" reserve-selection />
        <ElTableColumn prop="code" label="因子代码" width="180" />
        <ElTableColumn prop="name" label="名称" min-width="150" />
        <ElTableColumn prop="category" label="类别" width="110" />
        <ElTableColumn prop="frequency" label="频率" width="90" />
        <ElTableColumn
          prop="data_source"
          label="数据源"
          width="100"
          show-overflow-tooltip
        />
        <ElTableColumn prop="formula" label="公式" min-width="160" />
        <ElTableColumn label="状态" width="100" align="center">
          <template #default="{ row }">
            <ElTag :type="row.is_enabled ? 'success' : 'info'" size="small">
              {{ row.is_enabled ? '已入库' : row.imported ? '已登记' : '未入库' }}
            </ElTag>
          </template>
        </ElTableColumn>
        <template #empty>
          <el-empty description="该服务暂无因子，或无法读取因子库" :image-size="80" />
        </template>
      </ElTable>

      <div class="mt-4 flex items-center justify-between">
        <span class="text-sm text-gray-500">
          已勾选 {{ pickerSelected.size }} 个 / 共 {{ pickerTotal }} 个
        </span>
        <ElPagination
          v-model:current-page="pickerPage"
          v-model:page-size="pickerPageSize"
          :total="pickerTotal"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next"
          @current-change="handlePickerPageChange"
          @size-change="handlePickerSizeChange"
        />
      </div>

      <template #footer>
        <ElButton @click="pickerVisible = false">取消</ElButton>
        <ElButton type="primary" :loading="pickerImporting" @click="confirmImport">
          确认入库
        </ElButton>
      </template>
    </ElDialog>
  </div>
</template>
