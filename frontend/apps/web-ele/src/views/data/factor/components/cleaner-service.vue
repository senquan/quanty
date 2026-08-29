<script lang="ts" setup>
import { computed, onMounted, reactive, ref } from 'vue';

import {
  ElButton,
  ElCard,
  ElDialog,
  ElForm,
  ElFormItem,
  ElInput,
  ElMessage,
  ElMessageBox,
  ElRadio,
  ElRadioGroup,
  ElSelect,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import {
  type CleanerServiceCreatePayload,
  type CleanerServiceItem,
  type FactorRegistryItem,
  deleteCleanerServiceApi,
  enableCleanerFactorsApi,
  listCleanerServicesApi,
  listFactorRegistryApi,
  pollCleanerQosApi,
  registerCleanerServiceApi,
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
  } catch (e: any) {
    ElMessage.error(`加载服务列表失败: ${e?.msg ?? e?.message ?? e}`);
  } finally {
    loading.value = false;
  }
}

function statusTagType(s: CleanerServiceItem): 'success' | 'danger' | 'info' | 'warning' {
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
  } catch (e: any) {
    ElMessage.error(`注册失败: ${e?.msg ?? e?.message ?? e}`);
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
  } catch (e: any) {
    ElMessage.error(`测试失败: ${e?.msg ?? e?.message ?? e}`);
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
  } catch (e: any) {
    ElMessage.error(`QoS 轮询失败: ${e?.msg ?? e?.message ?? e}`);
  } finally {
    busy[k] = false;
  }
}

async function handleSync(s: CleanerServiceItem) {
  const k = busyKey(s.service_code, 'sync');
  busy[k] = true;
  try {
    const r = await syncCleanerFactorsApi(s.service_code);
    ElMessage.success(`同步完成：入库 ${r.synced} 个因子（服务状态 ${r.status}）`);
    await loadServices();
    await loadRegistry();
  } catch (e: any) {
    ElMessage.error(`同步失败: ${e?.msg ?? e?.message ?? e}`);
  } finally {
    busy[k] = false;
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
  } catch (e: any) {
    ElMessage.error(`删除失败: ${e?.msg ?? e?.message ?? e}`);
  } finally {
    busy[k] = false;
  }
}

// ============ 因子聚合注册表 ============
const registryLoading = ref(false);
const registry = ref<FactorRegistryItem[]>([]);
const selectedService = ref<string>('');   // '' = 全部
const onlyEnabled = ref(false);

const serviceOptions = computed(() =>
  services.value.map((s) => ({ label: `${s.name}（${s.service_code}）`, value: s.service_code })),
);

async function loadRegistry() {
  registryLoading.value = true;
  try {
    registry.value = await listFactorRegistryApi({
      service_code: selectedService.value || undefined,
      only_enabled: onlyEnabled.value,
    });
  } catch (e: any) {
    ElMessage.error(`加载因子注册表失败: ${e?.msg ?? e?.message ?? e}`);
  } finally {
    registryLoading.value = false;
  }
}

/** 切换单个因子的启用状态 */
async function toggleFactorEnabled(row: FactorRegistryItem, val: boolean) {
  try {
    await enableCleanerFactorsApi(row.service_code, {
      factor_codes: [row.factor_code],
      is_enabled: val,
    });
    row.is_enabled = val;
    ElMessage.success(val ? '已启用入库' : '已取消入库');
  } catch (e: any) {
    row.is_enabled = !val; // 回滚
    ElMessage.error(`更新失败: ${e?.msg ?? e?.message ?? e}`);
  }
}

onMounted(() => {
  loadServices();
  loadRegistry();
});
</script>

<template>
  <div class="cleaner-service">
    <!-- 清洗服务列表 -->
    <el-card shadow="never" class="mb-4">
      <template #header>
        <div class="flex items-center justify-between">
          <span class="font-medium">清洗服务（多实例网关）</span>
          <el-button type="primary" size="small" @click="openCreate">注册服务</el-button>
        </div>
      </template>

      <el-table v-loading="loading" :data="services" stripe border>
        <el-table-column prop="service_code" label="编码" width="140" fixed />
        <el-table-column prop="name" label="名称" min-width="140" />
        <el-table-column prop="base_url" label="地址" min-width="200" show-overflow-tooltip />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row)" size="small">
              {{ row.is_active ? row.status : '已停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="last_heartbeat" label="最近心跳" width="170" />
        <el-table-column label="操作" width="300" fixed="right">
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              size="small"
              :loading="busy[busyKey(row.service_code, 'test')]"
              @click="handleTest(row)"
            >
              测试
            </el-button>
            <el-button
              link
              type="primary"
              size="small"
              :loading="busy[busyKey(row.service_code, 'qos')]"
              @click="handleQos(row)"
            >
              QoS
            </el-button>
            <el-button
              link
              type="primary"
              size="small"
              :loading="busy[busyKey(row.service_code, 'sync')]"
              @click="handleSync(row)"
            >
              同步因子
            </el-button>
            <el-button
              link
              type="danger"
              size="small"
              :loading="busy[busyKey(row.service_code, 'del')]"
              @click="handleDelete(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="尚未注册任何清洗服务，点击右上角「注册服务」" :image-size="80" />
        </template>
      </el-table>
    </el-card>

    <!-- 因子聚合注册表 -->
    <el-card shadow="never">
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
    </el-card>

    <!-- 注册服务对话框 -->
    <el-dialog
      v-model="dialogVisible"
      title="注册清洗服务"
      width="520px"
      destroy-on-close
    >
      <el-form :model="createForm" label-width="90px" @submit.prevent>
        <el-form-item label="服务编码" required>
          <el-input v-model="createForm.service_code" placeholder="如 cleaner_a" />
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="createForm.name" placeholder="如 清洗服务A" />
        </el-form-item>
        <el-form-item label="地址" required>
          <el-input
            v-model="createForm.base_url"
            placeholder="如 http://127.0.0.1:8100"
          />
        </el-form-item>
        <el-form-item label="API Key" required>
          <el-input
            v-model="createForm.api_key"
            type="password"
            show-password
            placeholder="与 data-cleaner 的 CLEANER_API_KEY 一致"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveService">注册</el-button>
      </template>
    </el-dialog>
  </div>
</template>
