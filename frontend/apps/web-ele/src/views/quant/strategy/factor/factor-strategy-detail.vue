<script lang="ts" setup>
import type {
  BacktestDetail,
  ExecutionRecord,
  FactorStrategy,
} from '#/api/factor-strategy';
import {
  getBacktestDetailApi,
  getFactorStrategyApi,
  listBacktestsApi,
  listExecutionsApi,
  runBacktestApi,
  updateFactorStrategyApi,
} from '#/api/factor-strategy';

import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Activity,
  CheckCircle2,
  Pencil,
  RefreshCw,
  XCircle,
} from '@lucide/vue';
import {
  ElButton,
  ElCard,
  ElCol,
  ElEmpty,
  ElMessage,
  ElOption,
  ElRow,
  ElSelect,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import FactorStrategyForm from './factor-strategy-form.vue';
import {
  categoryTheme,
  hardRuleOpLabels,
  pct,
  pnlClass,
  rebalanceLabel,
} from './theme';

const route = useRoute();
const router = useRouter();

const strategy = ref<FactorStrategy | null>(null);
const backtests = ref<{ id: number; metrics: any; created_at: string }[]>([]);
const detail = ref<BacktestDetail | null>(null);
const executions = ref<ExecutionRecord[]>([]);
const loading = ref(false);
const backtesting = ref(false);
const formVisible = ref(false);

const selectedRebalanceIdx = ref(0);

const navChartRef = ref();
const weightChartRef = ref();
const { renderEcharts: renderNav } = useEcharts(navChartRef);
const { renderEcharts: renderWeight } = useEcharts(weightChartRef);

const metrics = computed(() => detail.value?.metrics ?? null);
const rebalances = computed(() => detail.value?.rebalances ?? []);
const selectedRebalance = computed(
  () => rebalances.value[selectedRebalanceIdx.value] ?? null,
);

const metricCards = computed(() => {
  const m = metrics.value;
  if (!m) return [];
  return [
    { key: 'totalReturn', title: '总收益', value: pct(m.totalReturn), cls: pnlClass(m.totalReturn) },
    { key: 'annualReturn', title: '年化收益', value: pct(m.annualReturn), cls: pnlClass(m.annualReturn) },
    { key: 'sharpe', title: '夏普比率', value: m.sharpe.toFixed(2), cls: m.sharpe >= 1 ? 'text-emerald-500' : 'text-gray-600' },
    { key: 'maxDrawdown', title: '最大回撤', value: pct(m.maxDrawdown), cls: 'text-rose-500' },
    { key: 'winRate', title: '胜率', value: pct(m.winRate), cls: 'text-gray-600' },
    { key: 'turnover', title: '换手率', value: pct(m.turnover), cls: 'text-gray-600' },
  ];
});

function drawNav() {
  if (!detail.value) return;
  const nav = detail.value.nav || [];
  const dates = nav.map((n) => n.date);
  const values = nav.map((n) => n.value);
  const base = strategy.value?.config.initial_capital ?? (values[0] || 0);
  renderNav({
    grid: { left: 60, right: 20, top: 30, bottom: 40 },
    tooltip: { trigger: 'axis' },
    legend: { data: ['组合净值', '初始资金'], top: 0 },
    xAxis: { type: 'category', data: dates, boundaryGap: false },
    yAxis: { type: 'value', scale: true },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18 }],
    series: [
      {
        name: '组合净值',
        type: 'line',
        data: values,
        smooth: true,
        showSymbol: false,
        lineStyle: { color: '#6366f1', width: 2 },
        areaStyle: { color: 'rgba(99,102,241,0.08)' },
      },
      {
        name: '初始资金',
        type: 'line',
        data: dates.map(() => base),
        showSymbol: false,
        lineStyle: { color: '#c0c4cc', width: 1, type: 'dashed' },
      },
    ],
  });
}

function drawWeight() {
  const w = selectedRebalance.value?.weights ?? {};
  const entries = Object.entries(w).sort((a, b) => b[1] - a[1]);
  renderWeight({
    grid: { left: 120, right: 30, top: 10, bottom: 20 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: { type: 'value', max: 1 },
    yAxis: {
      type: 'category',
      data: entries.map((e) => e[0]).reverse(),
      axisLabel: { fontFamily: 'monospace', fontSize: 11 },
    },
    series: [
      {
        type: 'bar',
        data: entries.map((e) => e[1]).reverse(),
        itemStyle: { color: '#6366f1', borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: 'right', formatter: (p: any) => p.value.toFixed(3) },
      },
    ],
  });
}

watch(selectedRebalanceIdx, drawWeight);

async function loadBacktest() {
  if (!backtests.value.length) {
    detail.value = null;
    return;
  }
  const latest = backtests.value[0];
  if (!latest) return;
  detail.value = await getBacktestDetailApi(strategy.value!.id, latest.id);
  selectedRebalanceIdx.value = Math.max(0, rebalances.value.length - 1);
  drawNav();
  drawWeight();
}

async function load() {
  const id = Number(route.query.id);
  if (!id) return;
  loading.value = true;
  try {
    const [s, bt, ex] = await Promise.all([
      getFactorStrategyApi(id),
      listBacktestsApi(id),
      listExecutionsApi(id, 50),
    ]);
    strategy.value = s;
    backtests.value = bt;
    executions.value = ex;
    if (bt.length) await loadBacktest();
  } catch {
    ElMessage.error('加载策略详情失败');
  } finally {
    loading.value = false;
  }
}

async function handleBacktest() {
  if (!strategy.value) return;
  backtesting.value = true;
  try {
    await runBacktestApi(strategy.value.id);
    ElMessage.success('回测完成');
    await load();
  } catch {
    ElMessage.error('回测失败，请检查因子是否有值');
  } finally {
    backtesting.value = false;
  }
}

async function toggleActive(val: boolean) {
  if (!strategy.value) return;
  try {
    await updateFactorStrategyApi(strategy.value.id, { is_active: val });
    strategy.value.is_active = val;
    ElMessage.success(val ? '已启用自动调仓' : '已暂停');
  } catch {
    ElMessage.error('更新失败');
  }
}

function goBack() {
  router.push('/quant/strategy');
}

onMounted(load);
watch(
  () => route.query.id,
  () => load(),
);
</script>

<template>
  <div v-loading="loading" class="factor-strategy-detail p-4">
    <ElButton text :icon="Activity" class="mb-3 -ml-2" @click="goBack">
      返回策略列表
    </ElButton>

    <template v-if="strategy">
      <!-- 头部摘要 -->
      <ElCard shadow="never" class="mb-4">
        <div class="flex items-start justify-between flex-wrap gap-3">
          <div>
            <div class="flex items-center gap-2">
              <h2 class="text-lg font-semibold">{{ strategy.name }}</h2>
              <ElTag :type="strategy.is_active ? 'success' : 'info'" effect="light">
                {{ strategy.is_active ? '运行中' : '已暂停' }}
              </ElTag>
              <ElTag size="small" effect="plain" class="font-mono">
                ID {{ strategy.id }}
              </ElTag>
            </div>
            <div v-if="strategy.description" class="text-xs text-gray-400 mt-1">
              {{ strategy.description }}
            </div>
            <div class="flex flex-wrap gap-1.5 mt-2">
              <ElTag
                v-for="code in strategy.config.factor_codes"
                :key="code"
                size="small"
                effect="light"
                :type="categoryTheme(code).type"
              >
                {{ code }}
              </ElTag>
              <ElTag size="small" effect="plain">Top {{ strategy.config.top_n }}</ElTag>
              <ElTag size="small" effect="plain">{{ rebalanceLabel(strategy.config.rebalance) }}</ElTag>
              <ElTag size="small" effect="plain">
                {{ strategy.config.neutralize === 'industry' ? '行业中性化' : '仅标准化' }}
              </ElTag>
              <ElTag size="small" effect="plain" class="font-mono">
                {{ strategy.config.trade_time }}
              </ElTag>
              <template
                v-if="(strategy.config.filters?.hard_rules || []).length"
              >
                <ElTag
                  v-for="(r, i) in strategy.config.filters.hard_rules"
                  :key="'hr' + i"
                  size="small"
                  effect="plain"
                  type="warning"
                  class="font-mono"
                >
                  {{
                    r.factor +
                    ' ' +
                    hardRuleOpLabels[r.op] +
                    ' ' +
                    (r.dynamic ? '分位' + r.dynamic.quantile : r.value ?? '—')
                  }}
                </ElTag>
              </template>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <ElButton :icon="RefreshCw" :loading="backtesting" type="success" @click="handleBacktest">
              立即回测
            </ElButton>
            <ElButton
              :icon="CheckCircle2"
              :type="strategy.is_active ? 'warning' : 'primary'"
              @click="toggleActive(!strategy.is_active)"
            >
              {{ strategy.is_active ? '暂停' : '启用' }}
            </ElButton>
            <ElButton :icon="Pencil" @click="formVisible = true">编辑</ElButton>
          </div>
        </div>
      </ElCard>

      <!-- 无回测空态 -->
      <ElEmpty
        v-if="!detail"
        description="尚未回测，点击「立即回测」生成净值曲线与持仓"
        class="my-10"
      />

      <template v-else>
        <!-- 指标卡 -->
        <ElRow :gutter="12" class="mb-4">
          <ElCol v-for="m in metricCards" :key="m.key" :span="4">
            <ElCard shadow="never" class="metric-card">
              <div class="text-xs text-gray-400">{{ m.title }}</div>
              <div class="text-xl font-semibold font-mono mt-1" :class="m.cls">
                {{ m.value }}
              </div>
            </ElCard>
          </ElCol>
        </ElRow>

        <!-- 净值曲线 -->
        <ElCard shadow="never" class="mb-4">
          <template #header>
            <span class="text-sm font-semibold">净值曲线</span>
            <span class="text-xs text-gray-400 ml-2">
              {{ detail.start_date || '' }} ~ {{ detail.end_date || '' }}
            </span>
          </template>
          <EchartsUI ref="navChartRef" class="h-80 w-full" />
        </ElCard>

        <ElRow :gutter="16">
          <!-- 持仓明细 -->
          <ElCol :span="14">
            <ElCard shadow="never" class="mb-4">
              <template #header>
                <div class="flex items-center justify-between">
                  <span class="text-sm font-semibold">各期持仓</span>
                  <ElSelect
                    v-if="rebalances.length"
                    v-model="selectedRebalanceIdx"
                    size="small"
                    style="width: 160px"
                  >
                    <ElOption
                      v-for="(rb, i) in rebalances"
                      :key="rb.date"
                      :value="i"
                      :label="rb.date"
                    />
                  </ElSelect>
                </div>
              </template>
              <ElTable
                v-if="selectedRebalance"
                :data="selectedRebalance.holdings"
                max-height="420"
                stripe
              >
                <ElTableColumn type="expand">
                  <template #default="{ row }">
                    <div class="pl-4">
                      <div class="text-xs text-gray-500 mb-1">各因子 z 值（中性化后）</div>
                      <div class="flex flex-wrap gap-2">
                        <span
                          v-for="(z, code) in row.z_scores"
                          :key="code"
                          class="inline-flex items-center gap-1 text-xs"
                        >
                          <span class="font-mono text-gray-500">{{ code }}</span>
                          <span
                            class="font-mono font-semibold"
                            :class="z >= 0 ? 'text-emerald-500' : 'text-rose-500'"
                          >
                            {{ z.toFixed(2) }}
                          </span>
                        </span>
                      </div>
                    </div>
                  </template>
                </ElTableColumn>
                <ElTableColumn prop="symbol" label="标的" width="130" />
                <ElTableColumn prop="industry" label="行业" width="90" />
                <ElTableColumn label="综合得分" width="100">
                  <template #default="{ row }">
                    <span class="font-mono font-semibold">{{ row.score.toFixed(2) }}</span>
                  </template>
                </ElTableColumn>
                <ElTableColumn label="目标权重" width="90">
                  <template #default="{ row }">
                    <span class="font-mono">{{ pct(row.weight, 1) }}</span>
                  </template>
                </ElTableColumn>
              </ElTable>
              <ElEmpty v-else description="该期无有效持仓" />
            </ElCard>
          </ElCol>

          <!-- 权重快照 + 执行记录 -->
          <ElCol :span="10">
            <ElCard shadow="never" class="mb-4">
              <template #header>
                <span class="text-sm font-semibold">权重快照</span>
                <span class="text-xs text-gray-400 ml-2">
                  {{ strategy.config.weight_mode === 'manual' ? '手动权重' : '按 |IR| 自动归一化' }}
                </span>
              </template>
              <EchartsUI ref="weightChartRef" class="h-64 w-full" />
            </ElCard>
          </ElCol>
        </ElRow>

        <!-- 执行记录 -->
        <ElCard shadow="never">
          <template #header>
            <span class="text-sm font-semibold">调仓执行记录</span>
            <span class="text-xs text-gray-400 ml-2">（模拟盘自动下单）</span>
          </template>
          <ElTable :data="executions" max-height="360" stripe empty-text="暂无执行记录">
            <ElTableColumn prop="rebalance_date" label="调仓日" width="120" />
            <ElTableColumn prop="target_count" label="目标持仓" width="90" />
            <ElTableColumn prop="orders_placed" label="下单数" width="80" />
            <ElTableColumn label="成交金额" width="120">
              <template #default="{ row }">
                <span class="font-mono">{{ row.amount.toFixed(0) }}</span>
              </template>
            </ElTableColumn>
            <ElTableColumn label="状态" width="100">
              <template #default="{ row }">
                <ElTag
                  v-if="row.status === 'success'"
                  type="success"
                  effect="light"
                  size="small"
                >
                  <CheckCircle2 class="w-3.5 h-3.5 mr-1" />成功
                </ElTag>
                <ElTag v-else type="danger" effect="light" size="small">
                  <XCircle class="w-3.5 h-3.5 mr-1" />失败
                </ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn type="expand">
              <template #default="{ row }">
                <div class="pl-4 text-xs">
                  <div v-if="row.detail?.error" class="text-rose-500 mb-1">
                    错误：{{ row.detail.error }}
                  </div>
                  <div class="text-gray-500 mb-1">
                    订单明细（共 {{ (row.detail?.orders || []).length }} 笔）：
                  </div>
                  <div
                    v-for="(o, i) in row.detail?.orders || []"
                    :key="i"
                    class="font-mono text-gray-600"
                  >
                    {{ o.order.side }} {{ o.order.symbol }} ×{{ o.order.quantity }}
                    @{{ o.order.price }}
                    <span :class="o.ok ? 'text-emerald-500' : 'text-rose-500'">
                      {{ o.ok ? '✓' : '✗' }}
                    </span>
                  </div>
                </div>
              </template>
            </ElTableColumn>
          </ElTable>
        </ElCard>
      </template>
    </template>

    <FactorStrategyForm
      v-model:visible="formVisible"
      :strategy="strategy"
      @saved="load"
    />
  </div>
</template>

<style scoped>
.metric-card {
  border-radius: 12px;
}
</style>
