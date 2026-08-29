<script lang="ts" setup>
import { computed, onMounted, ref } from 'vue';

import {
  ElButton,
  ElCard,
  ElEmpty,
  ElSelect,
  ElStatistic,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import {
  type FactorRegistryItem,
  enableCleanerFactorsApi,
  listCleanerServicesApi,
  listFactorRegistryApi,
} from '#/api/cleaner-gateway';

const loading = ref(false);
const registry = ref<FactorRegistryItem[]>([]);
/** service_code -> 在线状态 */
const serviceStatus = ref<Record<string, { online: boolean; name: string }>>({});
const selectedService = ref<string>('');

const serviceOptions = computed(() =>
  Object.entries(serviceStatus.value).map(([code, v]) => ({
    label: `${v.name}（${code}）`,
    value: code,
  })),
);

const onlineCount = computed(
  () => registry.value.filter((r) => serviceStatus.value[r.service_code]?.online).length,
);
const offlineCount = computed(() => registry.value.length - onlineCount.value);
const enabledCount = computed(() => registry.value.filter((r) => r.is_enabled).length);

async function loadData() {
  loading.value = true;
  try {
    const [services, regs] = await Promise.all([
      listCleanerServicesApi(),
      listFactorRegistryApi({
        service_code: selectedService.value || undefined,
      }),
    ]);
    const map: Record<string, { online: boolean; name: string }> = {};
    for (const s of services) {
      map[s.service_code] = { online: s.is_active && s.status === 'online', name: s.name };
    }
    serviceStatus.value = map;
    registry.value = regs;
  } catch (e: any) {
    window.console.error(e);
  } finally {
    loading.value = false;
  }
}

/** 行是否在线（来源服务在线） */
function isOnline(row: FactorRegistryItem): boolean {
  return !!serviceStatus.value[row.service_code]?.online;
}

async function toggleEnabled(row: FactorRegistryItem, val: boolean) {
  try {
    const r = await enableCleanerFactorsApi(row.service_code, {
      factor_codes: [row.factor_code],
      is_enabled: val,
    });
    row.is_enabled = val;
    void r;
  } catch (e: any) {
    row.is_enabled = !val;
    window.console.error(e);
  }
}

onMounted(loadData);
</script>

<template>
  <div class="registry-library">
    <!-- 统计卡 -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
      <el-card shadow="never">
        <el-statistic title="聚合因子总数" :value="registry.length" />
      </el-card>
      <el-card shadow="never">
        <el-statistic title="在线因子" :value="onlineCount" />
        <span class="text-xs text-emerald-500">来源服务在线</span>
      </el-card>
      <el-card shadow="never">
        <el-statistic title="离线因子" :value="offlineCount" />
        <span class="text-xs text-gray-400">来源服务离线/停用</span>
      </el-card>
      <el-card shadow="never">
        <el-statistic title="已入库" :value="enabledCount" />
        <span class="text-xs text-blue-500">勾选启用</span>
      </el-card>
    </div>

    <!-- 过滤器 + 表格 -->
    <el-card shadow="never">
      <template #header>
        <div class="flex items-center justify-between">
          <span class="font-medium">聚合因子底册（多清洗服务）</span>
          <div class="flex items-center gap-2">
            <el-select
              v-model="selectedService"
              placeholder="全部服务"
              clearable
              class="w-52"
              @change="loadData"
            >
              <el-option
                v-for="s in serviceOptions"
                :key="s.value"
                :label="s.label"
                :value="s.value"
              />
            </el-select>
            <el-button size="small" @click="loadData">刷新</el-button>
          </div>
        </div>
      </template>

      <el-table v-loading="loading" :data="registry" stripe border>
        <el-table-column label="在线" width="80">
          <template #default="{ row }">
            <el-tag :type="isOnline(row) ? 'success' : 'info'" size="small">
              {{ isOnline(row) ? '在线' : '离线' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="service_code" label="来源服务" width="130" />
        <el-table-column prop="factor_code" label="因子代码" width="150" />
        <el-table-column prop="name" label="名称" min-width="150" />
        <el-table-column prop="category" label="类别" width="110" />
        <el-table-column prop="frequency" label="频率" width="90" />
        <el-table-column prop="data_source" label="数据源" width="110" />
        <el-table-column prop="formula" label="公式" min-width="200" show-overflow-tooltip />
        <el-table-column prop="last_sync" label="同步时间" width="170" />
        <el-table-column label="入库" width="90" fixed="right">
          <template #default="{ row }">
            <el-switch
              :model-value="row.is_enabled"
              :disabled="!isOnline(row)"
              @change="(v: string | number | boolean) => toggleEnabled(row, !!v)"
            />
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无因子登记，请先在「清洗服务」Tab 同步因子" :image-size="80" />
        </template>
      </el-table>
    </el-card>
  </div>
</template>
