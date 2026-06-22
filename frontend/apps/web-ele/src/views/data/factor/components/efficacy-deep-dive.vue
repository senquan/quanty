<script lang="ts" setup>
import { computed, onMounted, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { ElCard, ElCol, ElDescriptions, ElDescriptionsItem, ElOption, ElRow, ElSelect, ElTabPane, ElTabs, ElTag } from 'element-plus';

import type { Factor } from '../types';

const props = defineProps<{
  factors: Factor[];
  selectedFactor: Factor | null;
}>();

const emit = defineEmits<{
  'update:selectedFactor': [factor: Factor | null];
}>();

const activeSubTab = ref('returns');

// Returns chart
const returnsChartRef = ref();
const { renderEcharts: renderReturns } = useEcharts(returnsChartRef);

// IC histogram chart
const icHistChartRef = ref();
const { renderEcharts: renderIcHist } = useEcharts(icHistChartRef);

const selectedFactorId = computed({
  get: () => props.selectedFactor?.id || '',
  set: (id: string) => {
    const f = props.factors.find((x) => x.id === id);
    emit('update:selectedFactor', f || null);
  },
});

const totalLong = computed(() => {
  if (!props.selectedFactor) return 0;
  const arr = props.selectedFactor.longReturns;
  return ((arr[arr.length - 1]! / 100) - 1) * 100;
});
const totalBench = computed(() => {
  if (!props.selectedFactor) return 0;
  const arr = props.selectedFactor.benchmarkReturns;
  return ((arr[arr.length - 1]! / 100) - 1) * 100;
});
const totalExcess = computed(() => totalLong.value - totalBench.value);

function buildReturnsOptions(factor: Factor) {
  const excessSeries = factor.longReturns.map(
    (val, idx) => 100 + (val - factor.benchmarkReturns[idx]!),
  );
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: ['多头组合', '空头组合', '基准(CSI300)', '多头超额'] },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: {
      type: 'category' as const,
      data: factor.dates,
      boundaryGap: false,
    },
    yAxis: { type: 'value' as const, name: '累计净值(%)' },
    series: [
      {
        name: '多头组合',
        type: 'line',
        data: factor.longReturns,
        smooth: true,
        lineStyle: { color: '#10b981', width: 2 },
        itemStyle: { color: '#10b981' },
      },
      {
        name: '空头组合',
        type: 'line',
        data: factor.shortReturns,
        smooth: true,
        lineStyle: { color: '#f43f5e', width: 2 },
        itemStyle: { color: '#f43f5e' },
      },
      {
        name: '基准(CSI300)',
        type: 'line',
        data: factor.benchmarkReturns,
        lineStyle: { color: '#94a3b8', type: 'dashed', width: 1.5 },
        itemStyle: { color: '#94a3b8' },
      },
      {
        name: '多头超额',
        type: 'line',
        data: excessSeries,
        smooth: true,
        lineStyle: { color: '#3b82f6', width: 2.5 },
        itemStyle: { color: '#3b82f6' },
      },
    ],
  };
}

function buildIcOptions(factor: Factor) {
  return {
    tooltip: {
      trigger: 'axis',
      formatter: (params: any) => {
        const p = params[0];
        return `${p.name}<br/>IC: ${p.value >= 0 ? '+' : ''}${p.value.toFixed(4)}`;
      },
    },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: { type: 'category' as const, data: factor.dates },
    yAxis: { type: 'value' as const, name: 'IC值' },
    series: [
      {
        type: 'bar',
        data: factor.icSeries.map((v) => ({
          value: v,
          itemStyle: {
            color: v >= 0 ? '#10b981' : '#f43f5e',
            borderRadius: v >= 0 ? [4, 4, 0, 0] : [0, 0, 4, 4],
          },
        })),
        barWidth: '50%',
      },
    ],
  };
}

function renderCharts() {
  if (!props.selectedFactor) return;
  renderReturns(buildReturnsOptions(props.selectedFactor) as any);
  renderIcHist(buildIcOptions(props.selectedFactor) as any);
}

onMounted(renderCharts);
watch(() => props.selectedFactor, renderCharts);
</script>

<template>
  <div class="space-y-5">
    <!-- Factor Switcher -->
    <ElCard shadow="never" :body-style="{ padding: '16px' }">
      <div class="flex items-center gap-4">
        <span class="text-xs font-bold text-gray-400">切换因子评测:</span>
        <ElSelect
          v-model="selectedFactorId"
          placeholder="选择因子"
          class="flex-1"
          filterable
        >
          <ElOption
            v-for="f in factors"
            :key="f.id"
            :label="`【${f.category.toUpperCase()}】${f.name} (${f.code})`"
            :value="f.id"
          />
        </ElSelect>
      </div>
    </ElCard>

    <template v-if="selectedFactor">
      <ElTabs v-model="activeSubTab" type="card">
        <!-- Returns Chart Tab -->
        <ElTabPane label="分层多空超额回测" name="returns">
          <ElRow :gutter="16">
            <ElCol :span="17">
              <ElCard shadow="never" header="多头/空头/基准 累计收益曲线">
                <EchartsUI ref="returnsChartRef" height="350px" />
              </ElCard>
            </ElCol>
            <ElCol :span="7">
              <div class="space-y-4">
                <ElCard shadow="never" :body-style="{ padding: '16px' }">
                  <div class="space-y-4">
                    <div>
                      <span class="text-[11px] text-gray-400">多头累积总收益率</span>
                      <div class="text-2xl font-bold font-mono text-emerald-500 mt-1">
                        +{{ totalLong.toFixed(2) }}%
                      </div>
                    </div>
                    <div>
                      <span class="text-[11px] text-gray-400">超额阿尔法回报</span>
                      <div class="text-xl font-bold font-mono text-blue-500 mt-1">
                        +{{ totalExcess.toFixed(2) }}%
                      </div>
                    </div>
                    <div class="grid grid-cols-2 gap-3 pt-3 border-t border-gray-100">
                      <div>
                        <span class="text-[11px] text-gray-400">最大回撤</span>
                        <div class="text-sm font-semibold font-mono text-rose-500 mt-0.5">
                          {{ (selectedFactor.maxDrawdown * 100).toFixed(1) }}%
                        </div>
                      </div>
                      <div>
                        <span class="text-[11px] text-gray-400">夏普比率</span>
                        <div class="text-sm font-semibold font-mono mt-0.5">
                          {{ selectedFactor.sharpeRatio.toFixed(2) }}
                        </div>
                      </div>
                      <div>
                        <span class="text-[11px] text-gray-400">信息比率</span>
                        <div class="text-sm font-semibold font-mono mt-0.5">
                          {{ selectedFactor.ir.toFixed(2) }}
                        </div>
                      </div>
                      <div>
                        <span class="text-[11px] text-gray-400">胜率</span>
                        <div class="text-sm font-semibold font-mono mt-0.5">
                          {{ (selectedFactor.winRate * 100).toFixed(1) }}%
                        </div>
                      </div>
                    </div>
                  </div>
                </ElCard>
                <div class="p-3 rounded-xl bg-blue-50 border border-blue-100 text-xs text-blue-600 leading-relaxed">
                  <strong>多空回溯:</strong> 将全市场股票按因子得分分5等分，多头为最高20%，空头为最低20%，收益价差体现因子定价有效性。
                </div>
              </div>
            </ElCol>
          </ElRow>
        </ElTabPane>

        <!-- IC Histogram Tab -->
        <ElTabPane label="IC 历史分布探查" name="ic">
          <ElRow :gutter="16" class="mb-4">
            <ElCol :span="8">
              <ElCard shadow="never" :body-style="{ padding: '16px', textAlign: 'center' }">
                <span class="text-[11px] text-gray-400 block mb-1">IC 均值</span>
                <div
                  class="text-2xl font-bold font-mono"
                  :class="selectedFactor.icMean >= 0 ? 'text-emerald-500' : 'text-rose-500'"
                >
                  {{ selectedFactor.icMean >= 0 ? '+' : '' }}{{ selectedFactor.icMean.toFixed(4) }}
                </div>
                <p class="text-[11px] text-gray-400 mt-2">
                  绝对值越大，因子预测力越强。>0.02 为优质因子。
                </p>
              </ElCard>
            </ElCol>
            <ElCol :span="8">
              <ElCard shadow="never" :body-style="{ padding: '16px', textAlign: 'center' }">
                <span class="text-[11px] text-gray-400 block mb-1">IC 标准差</span>
                <div class="text-2xl font-bold font-mono">
                  {{ selectedFactor.icStd.toFixed(4) }}
                </div>
                <p class="text-[11px] text-gray-400 mt-2">
                  IC波动越小，因子定价效率越稳定。
                </p>
              </ElCard>
            </ElCol>
            <ElCol :span="8">
              <ElCard shadow="never" :body-style="{ padding: '16px', textAlign: 'center' }">
                <span class="text-[11px] text-gray-400 block mb-1">信息比率 (IR)</span>
                <div class="text-2xl font-bold font-mono text-blue-500">
                  {{ selectedFactor.ir.toFixed(3) }}
                </div>
                <p class="text-[11px] text-gray-400 mt-2">
                  IC均值/IC标准差，衡量因子单位波动的超额效率。
                </p>
              </ElCard>
            </ElCol>
          </ElRow>

          <ElCard shadow="never" header="月度 IC 走势分布">
            <EchartsUI ref="icHistChartRef" height="300px" />
          </ElCard>
        </ElTabPane>

        <!-- Documentation Tab -->
        <ElTabPane label="因子算法与数据源" name="doc">
          <ElCard shadow="never">
            <ElDescriptions :column="1" border>
              <ElDescriptionsItem label="因子名称">
                {{ selectedFactor.name }} ({{ selectedFactor.code }})
              </ElDescriptionsItem>
              <ElDescriptionsItem label="因子类别">
                <ElTag size="small">{{ selectedFactor.category }}</ElTag>
              </ElDescriptionsItem>
              <ElDescriptionsItem label="计算公式">
                <code class="px-2 py-0.5 rounded bg-gray-50 text-blue-500 font-mono text-xs">
                  {{ selectedFactor.formula }}
                </code>
              </ElDescriptionsItem>
              <ElDescriptionsItem label="因子说明">
                {{ selectedFactor.description }}
              </ElDescriptionsItem>
              <ElDescriptionsItem label="数据来源">
                <div class="flex flex-wrap gap-1">
                  <ElTag
                    v-for="src in selectedFactor.dataSources"
                    :key="src"
                    size="small"
                    type="success"
                  >
                    {{ src }}
                  </ElTag>
                </div>
              </ElDescriptionsItem>
              <ElDescriptionsItem label="更新频率">
                {{ selectedFactor.frequency === 'Daily' ? '每日收盘后17:00' :
                   selectedFactor.frequency === 'Weekly' ? '每周五收盘后' :
                   '每季财务报表披露后' }}
              </ElDescriptionsItem>
              <ElDescriptionsItem label="极值处理">
                MAD 中位数去极值，滤除偏离均值 3 倍标准差以上的异常值
              </ElDescriptionsItem>
              <ElDescriptionsItem label="中性化">
                行业中性化 + 市值中性化，消除特定风险暴露
              </ElDescriptionsItem>
            </ElDescriptions>
          </ElCard>
        </ElTabPane>
      </ElTabs>
    </template>

    <div v-else class="p-12 text-center text-gray-400">
      请先在因子底册库中添加因子，然后选择因子进行评测分析。
    </div>
  </div>
</template>
