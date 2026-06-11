<script lang="ts" setup>
import { ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Play } from '@lucide/vue';
import {
  ElButton,
  ElCard,
  ElCheckbox,
  ElCol,
  ElEmpty,
  ElOption,
  ElProgress,
  ElRow,
  ElSelect,
  ElSlider,
  ElTable,
  ElTableColumn,
} from 'element-plus';

import type { BacktestResult, Factor } from '../types';
import { runMultiFactorBacktest } from '../mock/factor-data';

const props = defineProps<{
  factors: Factor[];
}>();

const selectedIds = ref<string[]>([]);
const weightMethod = ref<'equal' | 'ic_weighted' | 'max_sharpe'>('ic_weighted');
const benchmark = ref<'CSI300' | 'CSI500' | 'SSE50'>('CSI300');
const rebalancePeriod = ref<'Monthly' | 'Quarterly' | 'Weekly'>('Monthly');
const transactionFee = ref(0.0015);

const isRunning = ref(false);
const stepMsg = ref('');
const result = ref<BacktestResult | null>(null);

const backtestChartRef = ref();
const { renderEcharts: renderBacktestChart } = useEcharts(backtestChartRef);

// Initialize with first 4 factors
watch(
  () => props.factors,
  (factors) => {
    if (factors.length > 0 && selectedIds.value.length === 0) {
      selectedIds.value = factors.slice(0, 4).map((f) => f.id);
    }
  },
  { immediate: true },
);

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
  '对齐 A 股全样本股票池...',
  '过滤停牌股与 ST 剔除...',
  '执行去极值与中性化...',
  '构建因子协方差矩阵...',
  '最优化求解因子权重...',
  '扣除交易滑点摩擦...',
  '生成组合复利曲线!',
];

async function triggerBacktest() {
  isRunning.value = true;
  result.value = null;

  for (let i = 0; i < steps.length; i++) {
    stepMsg.value = steps[i]!;
    await new Promise((r) => setTimeout(r, 250));
  }

  result.value = runMultiFactorBacktest(
    {
      selectedFactorIds: selectedIds.value,
      weightMethod: weightMethod.value,
      benchmark: benchmark.value,
      startDate: '2025-06',
      endDate: '2026-05',
      rebalancePeriod: rebalancePeriod.value,
      transactionFee: transactionFee.value,
    },
    props.factors,
  );
  isRunning.value = false;

  // Render chart after result
  setTimeout(() => renderResultChart(), 100);
}

function renderResultChart() {
  if (!result.value) return;
  renderBacktestChart({
    tooltip: { trigger: 'axis' },
    legend: { data: ['多因子组合', `${benchmark.value}基准`] },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: {
      type: 'category' as const,
      data: result.value.dates,
      boundaryGap: false,
    },
    yAxis: { type: 'value' as const, name: '累计净值(%)' },
    series: [
      {
        name: '多因子组合',
        type: 'line',
        data: result.value.portfolioReturns,
        smooth: true,
        lineStyle: { color: '#2563eb', width: 2.5 },
        itemStyle: { color: '#2563eb' },
        areaStyle: { opacity: 0.05 },
      },
      {
        name: `${benchmark.value}基准`,
        type: 'line',
        data: result.value.benchmarkReturns,
        lineStyle: { color: '#94a3b8', type: 'dashed', width: 1.5 },
        itemStyle: { color: '#94a3b8' },
      },
    ],
  } as any);
}

const weightOptions = [
  { id: 'equal', title: '等权重加权', desc: '各因子等比暴露，适用于因子间独立且风格平稳的市场。' },
  { id: 'ic_weighted', title: 'IC均值加权', desc: '按各因子IC历史均值大小线性分配权重，效益优先。' },
  { id: 'max_sharpe', title: '最大夏普优化', desc: '基于协方差惩罚性偏置，压制高相关重叠暴露。' },
];
</script>

<template>
  <ElCard shadow="never" :body-style="{ padding: '20px' }">
    <!-- Header -->
    <div class="flex items-center justify-between mb-5 pb-4 border-b border-gray-100">
      <div>
        <span class="text-xs font-semibold text-blue-500 block">组合配置与策略仿真</span>
        <h3 class="text-lg font-bold mt-1">一键组合回测对比工作间</h3>
        <p class="text-xs text-gray-400 mt-1">
          融合多因子暴露特征，支持协方差惩罚多准则调权，计算复合复利曲线。
        </p>
      </div>
      <ElButton
        v-if="!result && !isRunning"
        type="primary"
        @click="triggerBacktest"
      >
        <Play class="w-4 h-4 mr-1" />
        一键回测分析
      </ElButton>
    </div>

    <ElRow :gutter="16">
      <!-- Left Config Panel -->
      <ElCol :span="8">
        <div class="p-4 rounded-xl bg-gray-50 border border-gray-100 space-y-5">
          <!-- Factor Selection -->
          <div>
            <div class="flex items-center justify-between mb-2">
              <span class="text-xs font-bold text-gray-500">选择因子</span>
              <div class="text-[10px] text-blue-500 font-semibold space-x-2">
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
                    ? 'bg-blue-50 border-blue-200 text-blue-700 font-semibold'
                    : 'border-gray-100 text-gray-500 hover:border-gray-200'
                "
              >
                <div class="flex items-center gap-2 truncate">
                  <ElCheckbox
                    :model-value="selectedIds.includes(f.id)"
                    @change="handleToggle(f.id)"
                  />
                  <span class="truncate">{{ f.name }}</span>
                </div>
                <span class="text-[9px] text-gray-400 font-mono">{{ f.code }}</span>
              </label>
            </div>
            <span class="text-[10px] text-gray-400 mt-1 block">
              已选 <span class="font-bold text-blue-500">{{ selectedIds.length }}</span> 个因子
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
                    ? 'border-blue-400 bg-white'
                    : 'border-gray-100 hover:border-gray-200'
                "
              >
                <div class="flex items-center gap-1.5 text-xs font-bold">
                  <input
                    type="radio"
                    :checked="weightMethod === opt.id"
                    class="accent-blue-500"
                    @change="weightMethod = opt.id as any; result = null"
                  />
                  {{ opt.title }}
                </div>
                <p class="text-[10px] text-gray-400 mt-1">{{ opt.desc }}</p>
              </label>
            </div>
          </div>

          <!-- Benchmark & Period -->
          <div class="grid grid-cols-2 gap-3">
            <div>
              <span class="text-xs font-bold text-gray-500 block mb-1.5">对标基准</span>
              <ElSelect v-model="benchmark" class="w-full" size="small" @change="result = null">
                <ElOption label="沪深300" value="CSI300" />
                <ElOption label="中证500" value="CSI500" />
                <ElOption label="上证50" value="SSE50" />
              </ElSelect>
            </div>
            <div>
              <span class="text-xs font-bold text-gray-500 block mb-1.5">换仓周期</span>
              <ElSelect v-model="rebalancePeriod" class="w-full" size="small" @change="result = null">
                <ElOption label="周频" value="Weekly" />
                <ElOption label="月频" value="Monthly" />
                <ElOption label="季度" value="Quarterly" />
              </ElSelect>
            </div>
          </div>

          <!-- Transaction Fee -->
          <div>
            <div class="flex justify-between items-center text-xs text-gray-500 mb-1">
              <span class="font-bold">单边交易扣费</span>
              <span class="font-mono font-bold text-blue-500">
                {{ (transactionFee * 100).toFixed(2) }}%
              </span>
            </div>
            <ElSlider
              v-model="transactionFee"
              :min="0.0005"
              :max="0.005"
              :step="0.0005"
              :show-tooltip="false"
              @change="result = null"
            />
            <div class="flex justify-between text-[9px] font-mono text-gray-400">
              <span>0.05%</span>
              <span>0.50%</span>
            </div>
          </div>
        </div>
      </ElCol>

      <!-- Right Results Panel -->
      <ElCol :span="16">
        <!-- Empty state -->
        <div v-if="!result && !isRunning" class="h-[460px] flex items-center justify-center">
          <ElEmpty description="配置左侧参数后点击一键回测">
            <ElButton type="primary" @click="triggerBacktest">
              <Play class="w-4 h-4 mr-1" />
              立刻测算
            </ElButton>
          </ElEmpty>
        </div>

        <!-- Running state -->
        <div
          v-else-if="isRunning"
          class="h-[460px] flex flex-col items-center justify-center rounded-2xl bg-gray-900 text-white"
        >
          <div class="mb-4 text-4xl animate-spin">⚙️</div>
          <h4 class="text-sm font-bold text-blue-300">A 股仿真引擎运转中</h4>
          <div class="p-3 bg-gray-800 rounded-lg max-w-md w-full border border-gray-700 mt-4 text-center">
            <span class="text-[10px] text-gray-400 block mb-1">正在执行</span>
            <p class="text-xs font-mono font-bold text-emerald-400 animate-pulse">
              {{ stepMsg }}
            </p>
          </div>
        </div>

        <!-- Results state -->
        <div v-else-if="result" class="space-y-5">
          <!-- Chart -->
          <ElCard shadow="never" header="资金复利曲线">
            <EchartsUI ref="backtestChartRef" height="300px" />
          </ElCard>

          <!-- Weight Allocation -->
          <ElCard shadow="never" :body-style="{ padding: '16px' }">
            <span class="text-[10px] text-gray-400 font-bold block mb-3">因子权重分配</span>
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
                  <span class="text-sm font-bold font-mono text-blue-500">
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

          <!-- Metrics Cards -->
          <ElRow :gutter="16">
            <ElCol :span="6">
              <ElCard shadow="hover" :body-style="{ padding: '16px', textAlign: 'center' }">
                <span class="text-[10px] text-gray-400 block">组合总收益率</span>
                <div class="text-xl font-mono font-bold text-emerald-500 mt-1">
                  +{{ result.metrics.totalReturn.toFixed(2) }}%
                </div>
                <span class="text-[9px] text-gray-400">
                  超额: +{{ (result.metrics.totalReturn - result.metrics.benchmarkReturn).toFixed(1) }}%
                </span>
              </ElCard>
            </ElCol>
            <ElCol :span="6">
              <ElCard shadow="hover" :body-style="{ padding: '16px', textAlign: 'center' }">
                <span class="text-[10px] text-gray-400 block">夏普比率</span>
                <div class="text-xl font-mono font-bold text-blue-500 mt-1">
                  {{ result.metrics.sharpeRatio.toFixed(2) }}
                </div>
                <span class="text-[9px] text-emerald-500 font-semibold">无风险利率: 2.5%</span>
              </ElCard>
            </ElCol>
            <ElCol :span="6">
              <ElCard shadow="hover" :body-style="{ padding: '16px', textAlign: 'center' }">
                <span class="text-[10px] text-gray-400 block">最大回撤</span>
                <div class="text-xl font-mono font-bold text-rose-500 mt-1">
                  -{{ result.metrics.maxDrawdown.toFixed(1) }}%
                </div>
              </ElCard>
            </ElCol>
            <ElCol :span="6">
              <ElCard shadow="hover" :body-style="{ padding: '16px', textAlign: 'center' }">
                <span class="text-[10px] text-gray-400 block">信息比率 (IR)</span>
                <div class="text-xl font-mono font-bold mt-1">
                  {{ result.metrics.informationRatio.toFixed(2) }}
                </div>
                <span class="text-[9px] text-gray-400">
                  换手率: {{ result.metrics.turnoverRate }}%
                </span>
              </ElCard>
            </ElCol>
          </ElRow>

          <!-- Detail Table -->
          <ElCard shadow="never" header="组合年度细节指标">
            <ElTable :data="[
              { metric: '标的基准收益率', value: `${result.metrics.benchmarkReturn.toFixed(2)}%`, note: `${benchmark} 指数总增长` },
              { metric: '超额阿尔法 (Alpha)', value: `+${result.metrics.alpha.toFixed(2)}%`, note: '剥离系统性Beta后的独立超额回报' },
              { metric: '组合贝塔 (Beta)', value: result.metrics.beta.toFixed(2), note: '相对大盘基准的弹性敞口' },
              { metric: '双边换手率估计', value: `${result.metrics.turnoverRate}% / 月`, note: '考虑滑点后的估计交易成本' },
            ]" stripe>
              <ElTableColumn prop="metric" label="指标" width="180" />
              <ElTableColumn prop="value" label="数值" width="140">
                <template #default="{ row }">
                  <span class="font-mono font-bold">{{ row.value }}</span>
                </template>
              </ElTableColumn>
              <ElTableColumn prop="note" label="说明" />
            </ElTable>
          </ElCard>
        </div>
      </ElCol>
    </ElRow>
  </ElCard>
</template>
