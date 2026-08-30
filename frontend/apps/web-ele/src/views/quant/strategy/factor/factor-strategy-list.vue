<script lang="ts" setup>
import type {
  ExecutionRecord,
  FactorStrategy,
} from '#/api/factor-strategy';
import {
  deleteFactorStrategyApi,
  listBacktestsApi,
  listExecutionsApi,
  listFactorStrategiesApi,
  runBacktestApi,
  updateFactorStrategyApi,
} from '#/api/factor-strategy';
import type { FactorDefinition } from '#/api/factor-library';
import { listFactorsApi } from '#/api/factor-library';

import { computed, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { BarChart3, Plus, RefreshCw, Trash2, TrendingUp } from '@lucide/vue';
import {
  ElButton,
  ElCard,
  ElCol,
  ElEmpty,
  ElMessage,
  ElMessageBox,
  ElRow,
  ElSwitch,
  ElTag,
} from 'element-plus';

import FactorStrategyForm from './factor-strategy-form.vue';
import {
  categoryTheme,
  pct,
  pnlClass,
  rebalanceLabel,
} from './theme';

const router = useRouter();

const strategies = ref<FactorStrategy[]>([]);
const loading = ref(false);
const formVisible = ref(false);
const editing = ref<FactorStrategy | null>(null);

/** 因子码 -> 类别（用于卡片标签着色） */
const factorCatMap = ref<Record<string, string>>({});
/** 策略最新年化 */
const annualMap = ref<Record<number, number>>({});
/** 策略今日是否已执行 */
const executedToday = ref<Record<number, boolean>>({});

const todayStr = new Date().toISOString().slice(0, 10);

const stats = computed(() => {
  const total = strategies.value.length;
  const active = strategies.value.filter((s) => s.is_active).length;
  const vals = Object.values(annualMap.value).filter((v) => v != null);
  const avgAnnual = vals.length
    ? vals.reduce((a, b) => a + b, 0) / vals.length
    : 0;
  const todayDone = Object.values(executedToday.value).filter(Boolean).length;
  return { total, active, avgAnnual, todayDone };
});

async function loadFactors() {
  try {
    const fs: FactorDefinition[] = await listFactorsApi();
    const m: Record<string, string> = {};
    for (const f of fs) m[f.code] = f.category || 'custom';
    factorCatMap.value = m;
  } catch {
    /* 因子目录非必须 */
  }
}

async function enrich(strategies: FactorStrategy[]) {
  const [bt, ex] = await Promise.all([
    Promise.all(strategies.map((s) => listBacktestsApi(s.id).catch(() => []))),
    Promise.all(strategies.map((s) => listExecutionsApi(s.id, 2).catch(() => []))),
  ]);
  const ann: Record<number, number> = {};
  const td: Record<number, boolean> = {};
  strategies.forEach((s, i) => {
    const latest = bt[i]?.[0];
    if (latest?.metrics?.annualReturn != null) ann[s.id] = latest.metrics.annualReturn;
    const exs = (ex[i] || []) as ExecutionRecord[];
    td[s.id] = exs.some((e) => e.rebalance_date === todayStr && e.status === 'success');
  });
  annualMap.value = ann;
  executedToday.value = td;
}

async function load() {
  loading.value = true;
  try {
    const list = await listFactorStrategiesApi();
    strategies.value = list;
    await enrich(list);
  } catch {
    ElMessage.error('加载因子策略失败');
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  await loadFactors();
  await load();
});

function openCreate() {
  editing.value = null;
  formVisible.value = true;
}

function openEdit(row: FactorStrategy) {
  editing.value = row;
  formVisible.value = true;
}

async function toggleActive(row: FactorStrategy, val: boolean) {
  try {
    await updateFactorStrategyApi(row.id, { is_active: val });
    row.is_active = val;
    ElMessage.success(val ? '已启用自动调仓' : '已暂停');
  } catch {
    ElMessage.error('更新失败');
  }
}

async function handleBacktest(row: FactorStrategy) {
  try {
    await runBacktestApi(row.id);
    ElMessage.success('回测完成');
    router.push({
      path: '/quant/strategy/factor-detail',
      query: { id: String(row.id) },
    });
  } catch {
    ElMessage.error('回测失败，请检查因子是否有值');
  }
}

function handleDelete(row: FactorStrategy) {
  ElMessageBox.confirm(
    `确定删除策略「${row.name}」吗？关联的回测与执行记录将一并删除。`,
    '提示',
    { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
  )
    .then(async () => {
      try {
        await deleteFactorStrategyApi(row.id);
        ElMessage.success('删除成功');
        await load();
      } catch {
        ElMessage.error('删除失败');
      }
    })
    .catch(() => {});
}

function openDetail(row: FactorStrategy) {
  router.push({
    path: '/quant/strategy/factor-detail',
    query: { id: String(row.id) },
  });
}
</script>

<template>
  <div class="factor-strategy-list">
    <!-- 统计条 -->
    <ElRow :gutter="12" class="mb-4">
      <ElCol :span="6">
        <ElCard shadow="never" class="stat-card">
          <div class="flex items-center gap-3">
            <div class="stat-icon bg-indigo-50 text-indigo-500">
              <BarChart3 class="w-5 h-5" />
            </div>
            <div>
              <div class="text-2xl font-semibold font-mono">{{ stats.total }}</div>
              <div class="text-xs text-gray-400">策略总数</div>
            </div>
          </div>
        </ElCard>
      </ElCol>
      <ElCol :span="6">
        <ElCard shadow="never" class="stat-card">
          <div class="flex items-center gap-3">
            <div class="stat-icon bg-emerald-50 text-emerald-500">
              <TrendingUp class="w-5 h-5" />
            </div>
            <div>
              <div class="text-2xl font-semibold font-mono">{{ stats.active }}</div>
              <div class="text-xs text-gray-400">启用中</div>
            </div>
          </div>
        </ElCard>
      </ElCol>
      <ElCol :span="6">
        <ElCard shadow="never" class="stat-card">
          <div class="flex items-center gap-3">
            <div class="stat-icon bg-amber-50 text-amber-500">
              <RefreshCw class="w-5 h-5" />
            </div>
            <div>
              <div class="text-2xl font-semibold font-mono">{{ stats.todayDone }}</div>
              <div class="text-xs text-gray-400">今日已执行</div>
            </div>
          </div>
        </ElCard>
      </ElCol>
      <ElCol :span="6">
        <ElCard shadow="never" class="stat-card">
          <div class="flex items-center gap-3">
            <div class="stat-icon bg-blue-50 text-blue-500">
              <TrendingUp class="w-5 h-5" />
            </div>
            <div>
              <div
                class="text-2xl font-semibold font-mono"
                :class="pnlClass(stats.avgAnnual)"
              >
                {{ stats.avgAnnual ? pct(stats.avgAnnual) : '—' }}
              </div>
              <div class="text-xs text-gray-400">平均年化</div>
            </div>
          </div>
        </ElCard>
      </ElCol>
    </ElRow>

    <!-- 工具条 -->
    <div class="flex items-center justify-between mb-3">
      <h3 class="text-sm font-semibold text-gray-700">因子策略</h3>
      <ElButton type="primary" :icon="Plus" @click="openCreate">新建因子策略</ElButton>
    </div>

    <!-- 空态 -->
    <ElEmpty
      v-if="!loading && strategies.length === 0"
      description="还没有因子策略，点击「新建因子策略」创建第一个"
    />

    <!-- 卡片网格 -->
    <ElRow v-loading="loading" :gutter="16">
      <ElCol v-for="s in strategies" :key="s.id" :xs="24" :sm="12" :lg="8" class="mb-4">
        <ElCard shadow="hover" class="strategy-card group" :body-style="{ padding: '18px' }">
          <div class="flex items-start justify-between">
            <div class="min-w-0 flex-1">
              <div class="font-semibold truncate group-hover:text-blue-500 transition-colors">
                {{ s.name }}
              </div>
              <div v-if="s.description" class="text-xs text-gray-400 mt-1 line-clamp-1">
                {{ s.description }}
              </div>
            </div>
            <ElSwitch
              :model-value="s.is_active"
              size="small"
              @change="(v: any) => toggleActive(s, v)"
            />
          </div>

          <!-- 因子组合标签 -->
          <div class="flex flex-wrap gap-1 mt-3">
            <ElTag
              v-for="code in s.config.factor_codes"
              :key="code"
              size="small"
              effect="light"
              :type="categoryTheme(factorCatMap[code]).type"
            >
              {{ code }}
            </ElTag>
          </div>

          <!-- 配置摘要 -->
          <div class="flex flex-wrap gap-x-4 gap-y-1 mt-3 text-xs text-gray-500">
            <span>Top {{ s.config.top_n }}</span>
            <span>{{ rebalanceLabel(s.config.rebalance) }}</span>
            <span>{{ s.config.neutralize === 'industry' ? '行业中性' : '标准化' }}</span>
            <span class="font-mono">{{ s.config.trade_time }}</span>
          </div>

          <!-- 最新年化 -->
          <div class="mt-3 pt-3 border-t border-gray-100 flex items-center justify-between">
            <span class="text-xs text-gray-400">最新年化</span>
            <span
              v-if="annualMap[s.id] != null"
              class="font-semibold font-mono"
              :class="pnlClass(annualMap[s.id]!)"
            >
              {{ pct(annualMap[s.id]!) }}
            </span>
            <span v-else class="text-xs text-gray-300">未回测</span>
          </div>

          <!-- 操作 -->
          <div class="flex items-center gap-1 mt-3 pt-3 border-t border-gray-100">
            <ElButton link type="primary" size="small" @click="handleBacktest(s)">
              <RefreshCw class="w-4 h-4 mr-1" />回测
            </ElButton>
            <ElButton link type="primary" size="small" @click="openDetail(s)">
              详情
            </ElButton>
            <ElButton link size="small" @click="openEdit(s)">编辑</ElButton>
            <ElButton
              link
              type="danger"
              size="small"
              class="ml-auto"
              @click="handleDelete(s)"
            >
              <Trash2 class="w-4 h-4" />
            </ElButton>
          </div>
        </ElCard>
      </ElCol>
    </ElRow>

    <FactorStrategyForm
      v-model:visible="formVisible"
      :strategy="editing"
      @saved="load"
    />
  </div>
</template>

<style scoped>
.stat-card {
  border-radius: 14px;
}
.stat-icon {
  width: 40px;
  height: 40px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.strategy-card {
  border-radius: 16px;
  transition: all 0.3s ease;
}
.strategy-card:hover {
  transform: translateY(-2px);
}
</style>
