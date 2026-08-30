<script lang="ts" setup>
import type {
  Factor,
  SelectedStock,
  StockSelectionResult,
} from '../types';

import type { FactorStrategyConfig, TargetPreview } from '#/api/factor-strategy';

import { computed, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Play } from '@lucide/vue';
import {
  ElButton,
  ElCard,
  ElCheckbox,
  ElCol,
  ElEmpty,
  ElMessage,
  ElOption,
  ElProgress,
  ElRadioButton,
  ElRadioGroup,
  ElRow,
  ElSelect,
  ElTable,
  ElTableColumn,
} from 'element-plus';

import { previewTargetApi } from '#/api/factor-strategy';

import { runStockSelection, STOCK_UNIVERSE } from '../mock/factor-data';

const props = defineProps<{
  factors: Factor[];
}>();

const selectedIds = ref<string[]>([]);
const weightMethod = ref<'equal' | 'ic_weighted' | 'max_sharpe'>('ic_weighted');
const stockPool = ref<('bj' | 'cyb' | 'kcb' | 'main')[]>([]);
const customEnabled = ref(false);
const customCodesText = ref<string>('');
const mode = ref<'long' | 'long_short'>('long');
const topN = ref(10);

/** 数据源：real=真实 data-cleaner 选股接口；mock=本地模拟 */
const dataSource = ref<'mock' | 'real'>('real');
const asOf = ref<string>('');

const isRunning = ref(false);
const stepMsg = ref('');
const result = ref<null | StockSelectionResult>(null);
const resultSource = ref<'mock' | 'real'>('mock');
const periodIndex = ref(0);

const scoreChartRef = ref();
const { renderEcharts: renderScoreChart } = useEcharts(scoreChartRef);

watch(
  () => props.factors,
  (factors) => {
    if (factors.length > 0 && selectedIds.value.length === 0) {
      selectedIds.value = factors.slice(0, 4).map((f) => f.id);
    }
  },
  { immediate: true },
);

// 真实引擎仅支持多头（compute_target 选 Top-N 多头），多空需本地模拟
const longShortDisabled = computed(() => dataSource.value === 'real');

function handleToggle(id: string) {
  if (selectedIds.value.includes(id)) {
    if (selectedIds.value.length <= 1) return;
    selectedIds.value = selectedIds.value.filter((x) => x !== id);
  } else {
    selectedIds.value = [...selectedIds.value, id];
  }
  result.value = null;
}

function handleSelectAll() {
  selectedIds.value = props.factors.map((f) => f.id);
  result.value = null;
}

function handleClearAll() {
  selectedIds.value = [props.factors[0]!.id];
  result.value = null;
}

const steps = [
  '对齐股票池与因子截面...',
  '标准化个股因子暴露...',
  '计算加权复合得分...',
  '剔除 ST 与停牌标的...',
  '按阈值筛选多头/空头...',
  '生成调仓清单!',
];

async function triggerSelection() {
  isRunning.value = true;
  result.value = null;

  for (const step of steps) {
    stepMsg.value = step!;
    await new Promise((r) => setTimeout(r, 250));
  }

  try {
    if (dataSource.value === 'real') {
      await runRealSelection();
    } else {
      const customCodes = parseCustomCodes(customCodesText.value);
      result.value = runStockSelection(
        {
          selectedFactorIds: selectedIds.value,
          weightMethod: weightMethod.value,
          universe: stockPool.value,
          customCodes: customEnabled.value ? customCodes : [],
          mode: mode.value,
          topN: topN.value,
        },
        props.factors,
      );
      periodIndex.value = result.value.periods.length - 1;
      resultSource.value = 'mock';
    }
  } finally {
    isRunning.value = false;
    setTimeout(() => renderResultChart(), 100);
  }
}

function parseCustomCodes(text: string): string[] {
  return text
    .split(/[,\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/** 组装真实选股配置（factor_codes 即因子 code，data/factor 页 id===code） */
function buildConfig(): FactorStrategyConfig {
  const codes = selectedIds.value;
  const config: FactorStrategyConfig = {
    factor_codes: codes,
    weights: {},
    weight_mode: 'auto_ir',
    neutralize: 'industry',
    top_n: topN.value,
    rebalance: { freq: 'weekly' },
    trade_time: '15:00',
    initial_capital: 1_000_000,
    filters: {
      exclude_st: true,
      min_list_days: 60,
      exclude_suspended: true,
      exclude_limit_up: true,
      exclude_limit_down: false,
      min_cap: null,
    },
    lookback_days: 60,
    universe: stockPool.value,
    custom_codes: customEnabled.value
      ? parseCustomCodes(customCodesText.value)
      : [],
  };
  if (weightMethod.value === 'equal') {
    config.weight_mode = 'manual';
    config.weights = Object.fromEntries(codes.map((c) => [c, 1 / codes.length]));
  }
  // ic_weighted / max_sharpe 真实引擎统一走实时 IR 加权（auto_ir）
  return config;
}

function unwrap(resp: any): any {
  return resp && typeof resp === 'object' && 'data' in resp ? resp.data : resp;
}

async function runRealSelection() {
  const config = buildConfig();
  const raw = unwrap(await previewTargetApi(config, asOf.value || undefined));
  if (!raw || raw.error) {
    ElMessage.warning(raw?.error || '选股接口未返回有效结果（可能所选因子暂无因子值）');
    result.value = null;
    return;
  }
  result.value = buildRealResult(raw as TargetPreview);
  resultSource.value = 'real';
  periodIndex.value = 0;
}

const NAME_MAP = Object.fromEntries(STOCK_UNIVERSE.map((s) => [s.code, s.name]));

function buildRealResult(raw: TargetPreview): StockSelectionResult {
  const stocks: SelectedStock[] = (raw.holdings || []).map((h) => {
    const score = Number(h.score) || 0;
    return {
      code: h.symbol,
      name: NAME_MAP[h.symbol] || h.symbol,
      score,
      weight: Number(h.weight) || 0,
      expectedReturn: Number((score * 2.5).toFixed(2)),
      side: 'long',
      industry: h.industry,
    };
  });
  const n = stocks.length || 1;
  const avgScore = stocks.reduce((s, x) => s + x.score, 0) / n;
  const avgExp = stocks.reduce((s, x) => s + x.expectedReturn, 0) / n;
  return {
    periods: [
      {
        date: raw.date || '最新',
        stocks,
        avgScore: Number(avgScore.toFixed(3)),
        avgExpectedReturn: Number(avgExp.toFixed(2)),
      },
    ],
    metrics: {
      avgStocks: stocks.length,
      avgScore: Number(avgScore.toFixed(3)),
      avgExpectedReturn: Number(avgExp.toFixed(2)),
      // 实时快照无历史，命中率/换手率不适用
      hitRate: 0,
      turnover: 0,
    },
    factorWeights: raw.weights || {},
  };
}

function renderResultChart() {
  if (!result.value) return;
  if (resultSource.value === 'real') {
    const stocks = (result.value.periods[0]?.stocks ?? []).toSorted(
      (a, b) => a.score - b.score,
    );
    renderScoreChart({
      tooltip: { trigger: 'item' as const },
      grid: { left: '3%', right: '12%', bottom: '3%', containLabel: true },
      xAxis: { type: 'value' as const, name: '复合得分' },
      yAxis: {
        type: 'category' as const,
        data: stocks.map((s) => s.name),
        axisLabel: { fontSize: 10 },
      },
      series: [
        {
          type: 'bar',
          data: stocks.map((s) => Number(s.score.toFixed(3))),
          itemStyle: { color: '#16a34a' },
          barWidth: '60%',
        },
      ],
    } as any);
    return;
  }

  renderScoreChart({
    tooltip: { trigger: 'axis' },
    legend: { data: ['平均复合得分', '平均预测月超额(%)'] },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: {
      type: 'category' as const,
      data: result.value.periods.map((p) => p.date),
      boundaryGap: false,
    },
    yAxis: [
      { type: 'value' as const, name: '复合得分' },
      { type: 'value' as const, name: '月超额(%)', position: 'right' as const },
    ],
    series: [
      {
        name: '平均复合得分',
        type: 'bar',
        data: result.value.periods.map((p) => p.avgScore),
        itemStyle: { color: '#2563eb' },
        barWidth: '45%',
      },
      {
        name: '平均预测月超额(%)',
        type: 'line',
        yAxisIndex: 1,
        data: result.value.periods.map((p) => p.avgExpectedReturn),
        smooth: true,
        lineStyle: { color: '#16a34a', width: 2.5 },
        itemStyle: { color: '#16a34a' },
      },
    ],
  } as any);
}

const currentPeriod = computed(() =>
  result.value ? result.value.periods[periodIndex.value] : null,
);

const weightOptions = [
  { id: 'equal', title: '等权重加权', desc: '各因子等比暴露，适用于因子间独立且风格平稳的市场。' },
  { id: 'ic_weighted', title: 'IC均值加权', desc: '按各因子IR历史均值线性分配权重（真实接口走实时IR加权）。' },
  { id: 'max_sharpe', title: '最大夏普优化', desc: '本地模拟按协方差惩罚调权；真实接口近似走实时IR加权。' },
];

const modeOptions = [
  { id: 'long', title: '多头精选', desc: '选取复合得分最高的 Top-N 只构建纯多头组合。' },
  { id: 'long_short', title: '多空对冲', desc: '同时选取得分最高与最低的 Top-N 只，多空配对降低市场敞口（仅本地模拟支持）。' },
];
</script>

<template>
  <ElCard shadow="never" :body-style="{ padding: '20px' }">
    <!-- Header -->
    <div class="flex items-center justify-between mb-5 pb-4 border-b border-gray-100">
      <div>
        <span class="text-xs font-semibold text-emerald-500 block">多因子组合选股</span>
        <h3 class="text-lg font-bold mt-1">一键组合选股工作台</h3>
        <p class="text-xs text-gray-400 mt-1">
          融合多因子复合得分，按阈值筛选每月调仓标的，输出可落地的多头/多空组合。
        </p>
      </div>
      <div class="flex items-center gap-3">
        <ElRadioGroup v-model="dataSource" @change="result = null">
          <ElRadioButton value="real">真实选股接口</ElRadioButton>
          <ElRadioButton value="mock">本地模拟</ElRadioButton>
        </ElRadioGroup>
        <ElButton
          v-if="!result && !isRunning"
          type="success"
          @click="triggerSelection"
        >
          <Play class="w-4 h-4 mr-1" />
          一键选股
        </ElButton>
      </div>
    </div>

    <ElRow :gutter="16">
      <!-- Left Config Panel -->
      <ElCol :span="8">
        <div class="p-4 rounded-xl bg-gray-50 border border-gray-100 space-y-5">
          <!-- Factor Selection -->
          <div>
            <div class="flex items-center justify-between mb-2">
              <span class="text-xs font-bold text-gray-500">选择因子</span>
              <div class="text-[11px] text-emerald-500 font-semibold space-x-2">
                <span class="cursor-pointer hover:underline" @click="handleSelectAll">全选</span>
                <span class="cursor-pointer hover:underline" @click="handleClearAll">清除</span>
              </div>
            </div>
            <div class="max-h-48 overflow-y-auto border border-gray-100 rounded-xl p-2.5 bg-white space-y-1.5">
              <label
                v-for="f in factors"
                :key="f.id"
                class="flex items-center justify-between px-2 py-1.5 rounded-lg border text-xs cursor-pointer transition-all"
                :class="
                  selectedIds.includes(f.id)
                    ? 'bg-emerald-50 border-emerald-200 text-emerald-700 font-semibold'
                    : 'border-gray-100 text-gray-500 hover:border-gray-200'
                "
              >
                <div class="flex items-center gap-2 truncate">
                  <input
                    type="checkbox"
                    :checked="selectedIds.includes(f.id)"
                    class="accent-emerald-500"
                    @change="handleToggle(f.id)"
                  />
                  <span class="truncate">{{ f.name }}</span>
                </div>
                <span class="text-[9px] text-gray-400 font-mono">{{ f.code }}</span>
              </label>
            </div>
            <span class="text-[11px] text-gray-400 mt-1 block">
              已选 <span class="font-bold text-emerald-500">{{ selectedIds.length }}</span> 个因子
            </span>
          </div>

          <!-- Weight Method -->
          <div>
            <span class="text-xs font-bold text-gray-500 block mb-2">加权模型</span>
            <div class="space-y-1.5">
              <label
                v-for="opt in weightOptions"
                :key="opt.id"
                class="border rounded-xl p-2.5 flex flex-col cursor-pointer transition-all"
                :class="
                  weightMethod === opt.id
                    ? 'border-emerald-400 bg-white'
                    : 'border-gray-100 hover:border-gray-200'
                "
              >
                <div class="flex items-center gap-1.5 text-xs font-bold">
                  <input
                    type="radio"
                    :checked="weightMethod === opt.id"
                    class="accent-emerald-500"
                    @change="weightMethod = opt.id as any; result = null"
                  />
                  {{ opt.title }}
                </div>
                <p class="text-[11px] text-gray-400 mt-1">{{ opt.desc }}</p>
              </label>
            </div>
          </div>

          <!-- Selection Logic -->
          <div>
            <span class="text-xs font-bold text-gray-500 block mb-2">选股逻辑</span>
            <div class="space-y-1.5">
              <label
                v-for="opt in modeOptions"
                :key="opt.id"
                class="border rounded-xl p-2.5 flex flex-col cursor-pointer transition-all"
                :class="
                  mode === opt.id
                    ? 'border-emerald-400 bg-white'
                    : 'border-gray-100 hover:border-gray-200'
                "
              >
                <div class="flex items-center gap-1.5 text-xs font-bold">
                  <input
                    type="radio"
                    :checked="mode === opt.id"
                    class="accent-emerald-500"
                    :disabled="longShortDisabled && opt.id === 'long_short'"
                    @change="mode = opt.id as any; result = null"
                  />
                  {{ opt.title }}
                </div>
                <p class="text-[11px] text-gray-400 mt-1">{{ opt.desc }}</p>
              </label>
            </div>
            <span
              v-if="longShortDisabled"
              class="text-[10px] text-amber-500 mt-1 block"
            >
              真实选股接口仅返回多头组合（compute_target 选 Top-N 多头）
            </span>
          </div>

          <!-- Universe & TopN -->
          <div class="grid grid-cols-2 gap-3">
            <div class="col-span-2">
              <span class="text-xs font-bold text-gray-500 block mb-1.5">标的股票池（可多选，未选板块 = 全市场）</span>
              <div class="flex items-center gap-2 flex-wrap">
                <ElSelect
                  v-model="stockPool"
                  multiple
                  collapse-tags
                  collapse-tags-tooltip
                  :max-collapse-tags="3"
                  size="small"
                  clearable
                  placeholder="未选板块 = 全市场"
                  class="min-w-[240px]"
                  @change="result = null"
                >
                  <ElOption value="main" label="沪深主板" />
                  <ElOption value="cyb" label="创业板" />
                  <ElOption value="kcb" label="科创板" />
                  <ElOption value="bj" label="北交所" />
                </ElSelect>
                <ElCheckbox v-model="customEnabled" border @change="result = null">自选股</ElCheckbox>
                <ElInput
                  v-if="customEnabled"
                  v-model="customCodesText"
                  size="small"
                  class="flex-1 min-w-[200px]"
                  placeholder="输入自选股代码，逗号或空格分隔，如 600519, 000001"
                  @input="result = null"
                />
              </div>
              <span class="text-[10px] text-gray-400 mt-0.5 block">
                多板块与自选股取并集（真实接口与本地模拟均生效）
              </span>
            </div>
            <div>
              <span class="text-xs font-bold text-gray-500 block mb-1.5">Top-N 数量</span>
              <ElSelect
                v-model="topN"
                class="w-full"
                size="small"
                @change="result = null"
              >
                <ElOption v-for="n in [5, 10, 15, 20, 30]" :key="n" :label="`${n} 只`" :value="n" />
              </ElSelect>
            </div>
          </div>

          <ElButton
            v-if="!result && !isRunning"
            type="success"
            class="w-full"
            @click="triggerSelection"
          >
            <Play class="w-4 h-4 mr-1" />
            一键选股
          </ElButton>
        </div>
      </ElCol>

      <!-- Right Results Panel -->
      <ElCol :span="16">
        <!-- Empty state -->
        <div v-if="!result && !isRunning" class="h-[460px] flex items-center justify-center">
          <ElEmpty description="配置左侧因子与参数后点击一键选股">
            <ElButton type="success" @click="triggerSelection">
              <Play class="w-4 h-4 mr-1" />
              立刻选股
            </ElButton>
          </ElEmpty>
        </div>

        <!-- Running state -->
        <div
          v-else-if="isRunning"
          class="h-[460px] flex flex-col items-center justify-center rounded-2xl bg-gray-900 text-white"
        >
          <div class="mb-4 text-4xl animate-spin">⚙️</div>
          <h4 class="text-sm font-bold text-emerald-300">多因子选股引擎运转中</h4>
          <div class="p-3 bg-gray-800 rounded-lg max-w-md w-full border border-gray-700 mt-4 text-center">
            <span class="text-[11px] text-gray-400 block mb-1">正在执行</span>
            <p class="text-xs font-mono font-bold text-emerald-400 animate-pulse">
              {{ stepMsg }}
            </p>
          </div>
        </div>

        <!-- Results state -->
        <div v-else-if="result" class="space-y-5">
          <!-- Summary metrics -->
          <ElRow :gutter="16">
            <ElCol :span="6">
              <ElCard shadow="hover" :body-style="{ padding: '16px', textAlign: 'center' }">
                <span class="text-[11px] text-gray-400 block">平均选股数</span>
                <div class="text-xl font-mono font-bold text-emerald-500 mt-1">
                  {{ result.metrics.avgStocks }} 只
                </div>
                <span class="text-[9px] text-gray-400">
                  {{ resultSource === 'real' ? '实时快照' : mode === 'long_short' ? '多空' : '多头' }}
                </span>
              </ElCard>
            </ElCol>
            <ElCol :span="6">
              <ElCard shadow="hover" :body-style="{ padding: '16px', textAlign: 'center' }">
                <span class="text-[11px] text-gray-400 block">平均复合得分</span>
                <div class="text-xl font-mono font-bold mt-1">
                  {{ result.metrics.avgScore.toFixed(3) }}
                </div>
                <span class="text-[9px] text-emerald-500 font-semibold">加权因子暴露</span>
              </ElCard>
            </ElCol>
            <ElCol :span="6">
              <ElCard shadow="hover" :body-style="{ padding: '16px', textAlign: 'center' }">
                <span class="text-[11px] text-gray-400 block">平均预测月超额</span>
                <div class="text-xl font-mono font-bold text-emerald-500 mt-1">
                  +{{ result.metrics.avgExpectedReturn.toFixed(2) }}%
                </div>
                <span class="text-[9px] text-gray-400">模型估计</span>
              </ElCard>
            </ElCol>
            <ElCol :span="6">
              <ElCard shadow="hover" :body-style="{ padding: '16px', textAlign: 'center' }">
                <span class="text-[11px] text-gray-400 block">跑赢基准命中率</span>
                <div class="text-xl font-mono font-bold text-blue-500 mt-1">
                  {{ resultSource === 'real' ? '—' : `${result.metrics.hitRate}%` }}
                </div>
                <span class="text-[9px] text-gray-400">
                  换手: {{ resultSource === 'real' ? '—' : `${result.metrics.turnover}%` }}
                </span>
              </ElCard>
            </ElCol>
          </ElRow>

          <!-- Score chart -->
          <ElCard shadow="never" :header="resultSource === 'real' ? '持仓标的复合得分' : '各月组合复合得分 / 预测月超额'">
            <EchartsUI ref="scoreChartRef" height="300px" />
          </ElCard>

          <!-- Factor weights -->
          <ElCard shadow="never" :body-style="{ padding: '16px' }">
            <span class="text-[11px] text-gray-400 font-bold block mb-3">因子权重分配</span>
            <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
              <div
                v-for="(wt, code) in result.factorWeights"
                :key="code"
                class="border border-gray-100 rounded-xl p-3 bg-gray-50"
              >
                <div class="flex justify-between items-start gap-2">
                  <div class="truncate">
                    <span class="text-xs font-bold block truncate">
                      {{ factors.find((f) => f.code === code)?.name || code }}
                    </span>
                    <span class="text-[9px] text-gray-400 font-mono">{{ code }}</span>
                  </div>
                  <span class="text-sm font-bold font-mono text-emerald-500">
                    {{ ((wt as number) * 100).toFixed(1) }}%
                  </span>
                </div>
                <ElProgress
                  :percentage="Number((wt as number) * 100)"
                  :show-text="false"
                  :stroke-width="4"
                  class="mt-2"
                />
              </div>
            </div>
          </ElCard>

          <!-- Selected stocks by period -->
          <ElCard shadow="never">
            <template #header>
              <div class="flex items-center justify-between">
                <span>调仓标的清单{{ resultSource === 'real' ? `（${currentPeriod?.date} 实时）` : '' }}</span>
                <ElSelect
                  v-if="resultSource !== 'real'"
                  v-model="periodIndex"
                  size="small"
                  style="width: 140px"
                >
                  <ElOption
                    v-for="(p, idx) in result.periods"
                    :key="p.date"
                    :label="idx === result.periods.length - 1 ? `${p.date}（当前）` : p.date"
                    :value="idx"
                  />
                </ElSelect>
              </div>
            </template>
            <ElTable :data="currentPeriod?.stocks ?? []" stripe max-height="360">
              <ElTableColumn prop="code" label="代码" width="120">
                <template #default="{ row }">
                  <span class="font-mono text-xs">{{ row.code }}</span>
                </template>
              </ElTableColumn>
              <ElTableColumn prop="name" label="名称" width="110" />
              <ElTableColumn label="方向" width="70">
                <template #default="{ row }">
                  <span
                    class="text-[11px] px-1.5 py-0.5 rounded font-semibold"
                    :class="
                      row.side === 'long'
                        ? 'bg-emerald-50 text-emerald-600'
                        : 'bg-rose-50 text-rose-600'
                    "
                  >
                    {{ row.side === 'long' ? '多' : '空' }}
                  </span>
                </template>
              </ElTableColumn>
              <ElTableColumn prop="score" label="复合得分" width="110">
                <template #default="{ row }">
                  <span class="font-mono font-bold">{{ Number(row.score).toFixed(3) }}</span>
                </template>
              </ElTableColumn>
              <ElTableColumn label="预测月超额" width="110">
                <template #default="{ row }">
                  <span class="font-mono font-bold text-emerald-500">
                    +{{ row.expectedReturn }}%
                  </span>
                </template>
              </ElTableColumn>
              <ElTableColumn prop="weight" label="权重" width="90">
                <template #default="{ row }">
                  <span class="font-mono">{{ (Number(row.weight) * 100).toFixed(1) }}%</span>
                </template>
              </ElTableColumn>
              <ElTableColumn prop="industry" label="行业">
                <template #default="{ row }">
                  <span class="text-xs text-gray-500">{{ row.industry || '—' }}</span>
                </template>
              </ElTableColumn>
            </ElTable>
          </ElCard>
        </div>
      </ElCol>
    </ElRow>
  </ElCard>
</template>
