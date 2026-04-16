<script lang="ts" setup>
import { ref, onMounted } from 'vue';

import {
  ElButton,
  ElCard,
  ElCol,
  ElInput,
  ElOption,
  ElRow,
  ElSelect,
  ElTabPane,
  ElTabs,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import { Play, Download } from 'lucide-vue-next';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

// ============ ECharts ============
const equityChartRef = ref();
const drawdownChartRef = ref();
const distChartRef = ref();
const { renderEcharts: renderEquity } = useEcharts(equityChartRef);
const { renderEcharts: renderDrawdown } = useEcharts(drawdownChartRef);
const { renderEcharts: renderDist } = useEcharts(distChartRef);

// ============ 模拟数据 ============
const equityData = Array.from({ length: 250 }, (_, i) => {
  const date = `2023-${String(Math.floor(i / 21) + 1).padStart(2, '0')}-${String((i % 28) + 1).padStart(2, '0')}`;
  const value = 100000 + i * 62.4 + Math.sin(i / 10) * 5000 + Math.random() * 2000;
  return { date, value: Math.round(value) };
});

const dailyReturns = Array.from({ length: 250 }, () =>
  (Math.random() - 0.48) * 4
);

const benchmarkData = equityData.map((d, i) => ({
  date: d.date,
  value: Math.round(100000 + i * 31.2 + Math.sin(i / 15) * 3000 + Math.random() * 1500),
}));

const drawdownData = equityData.map((d, i) => {
  const peak = Math.max(...equityData.slice(0, i + 1).map(e => e.value));
  return { date: d.date, value: parseFloat((((d.value - peak) / peak) * 100).toFixed(2)) };
});

// ============ 图表渲染 ============
function renderEquityChart() {
  renderEquity({
    tooltip: { trigger: 'axis' },
    legend: { data: ['策略净值', '基准净值'] },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: { type: 'category', data: equityData.map(d => d.date), boundaryGap: false },
    yAxis: { type: 'value', name: '净值 (¥)' },
    series: [
      { name: '策略净值', type: 'line', data: equityData.map(d => d.value), smooth: true, lineStyle: { width: 2 }, areaStyle: { opacity: 0.1 } },
      { name: '基准净值', type: 'line', data: benchmarkData.map(d => d.value), smooth: true, lineStyle: { width: 1, type: 'dashed' } },
    ],
  });
}

function renderDrawdownChart() {
  renderDrawdown({
    tooltip: { trigger: 'axis', formatter: '{b}<br/>回撤: {c}%' },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: { type: 'category', data: drawdownData.map(d => d.date), boundaryGap: false },
    yAxis: { type: 'value', name: '回撤 (%)', max: 0 },
    series: [{
      name: '回撤',
      type: 'line',
      data: drawdownData.map(d => d.value),
      smooth: true,
      lineStyle: { width: 2, color: '#f56c6c' },
      areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(245,108,108,0.3)' }, { offset: 1, color: 'rgba(245,108,108,0.05)' }] } },
    }],
  });
}

function renderDistChart() {
  const bins = 20;
  const min = Math.min(...dailyReturns);
  const max = Math.max(...dailyReturns);
  const step = (max - min) / bins;
  const histogram = Array.from({ length: bins }, (_, i) => {
    const low = min + i * step;
    const high = low + step;
    const count = dailyReturns.filter(r => r >= low && r < high).length;
    return { range: `${low.toFixed(1)}%`, count };
  });

  renderDist({
    tooltip: { trigger: 'axis' },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: { type: 'category', data: histogram.map(h => h.range), axisLabel: { rotate: 45, fontSize: 10 } },
    yAxis: { type: 'value', name: '频次' },
    series: [{
      name: '日收益分布',
      type: 'bar',
      data: histogram.map(h => h.count),
      itemStyle: {
        color: (params: any) => {
          const idx = params.dataIndex;
          const mid = Math.floor(bins / 2);
          return idx < mid ? '#67c23a' : idx > mid ? '#f56c6c' : '#e6a23c';
        },
      },
    }],
  });
}

// ============ 回测历史 ============
interface BacktestRecord {
  id: number;
  strategyName: string;
  symbol: string;
  period: string;
  totalReturn: number;
  sharpeRatio: number;
  maxDrawdown: number;
  winRate: number;
  trades: number;
  status: 'completed' | 'running' | 'failed';
  createdAt: string;
}

const selectedStrategy = ref('');

const backtestHistory = ref<BacktestRecord[]>([
  { id: 1, strategyName: '双均线交叉策略', symbol: 'AAPL', period: '2023-01 ~ 2023-12', totalReturn: 15.6, sharpeRatio: 1.85, maxDrawdown: -8.2, winRate: 62.5, trades: 48, status: 'completed', createdAt: '2026-04-14 10:30' },
  { id: 2, strategyName: 'RSI均值回归策略', symbol: 'MSFT', period: '2023-06 ~ 2023-12', totalReturn: 8.7, sharpeRatio: 1.23, maxDrawdown: -12.1, winRate: 58.3, trades: 36, status: 'completed', createdAt: '2026-04-13 15:20' },
  { id: 3, strategyName: '动量突破策略', symbol: 'BTC/USDT', period: '2023-01 ~ 2023-12', totalReturn: 0, sharpeRatio: 0, maxDrawdown: 0, winRate: 0, trades: 0, status: 'running', createdAt: '2026-04-14 14:00' },
]);

const getStatusType = (status: string) => ({ completed: 'success', running: 'warning', failed: 'danger' }[status] || '');
const getStatusLabel = (status: string) => ({ completed: '已完成', running: '运行中', failed: '失败' }[status] || status);

const handleRunBacktest = () => console.log('运行回测');
const handleExport = (row: BacktestRecord) => console.log('导出结果:', row.id);

onMounted(() => {
  renderEquityChart();
  renderDrawdownChart();
  renderDistChart();
});
</script>

<template>
  <div class="backtest-page p-4">
    <!-- 顶部操作栏 -->
    <ElCard shadow="never" class="mb-4">
      <ElRow :gutter="12" align="middle">
        <ElCol><ElSelect v-model="selectedStrategy" placeholder="选择策略" clearable style="width: 200px"><ElOption label="双均线交叉策略" value="1" /><ElOption label="RSI均值回归策略" value="2" /><ElOption label="动量突破策略" value="3" /></ElSelect></ElCol>
        <ElCol><ElSelect placeholder="数据源" style="width: 160px"><ElOption label="Yahoo Finance" value="yahoo" /><ElOption label="CCXT" value="ccxt" /></ElSelect></ElCol>
        <ElCol><ElInput placeholder="标的代码" style="width: 140px" /></ElCol>
        <ElCol :flex="1" />
        <ElCol><ElButton type="primary" @click="handleRunBacktest"><Play class="w-4 h-4 mr-1" />运行回测</ElButton></ElCol>
      </ElRow>
    </ElCard>

    <!-- 图表区 -->
    <ElRow :gutter="16" class="mb-4">
      <ElCol :span="16">
        <ElCard shadow="never" header="📈 净值曲线">
          <EchartsUI ref="equityChartRef" height="320px" />
        </ElCard>
      </ElCol>
      <ElCol :span="8">
        <ElCard shadow="never" header="📊 收益分布">
          <EchartsUI ref="distChartRef" height="320px" />
        </ElCard>
      </ElCol>
    </ElRow>

    <!-- 回撤图 + 指标 -->
    <ElRow :gutter="16" class="mb-4">
      <ElCol :span="12">
        <ElCard shadow="never" header="📉 回撤分析">
          <EchartsUI ref="drawdownChartRef" height="260px" />
        </ElCard>
      </ElCol>
      <ElCol :span="12">
        <ElCard shadow="never" header="📋 详细指标">
          <ElRow :gutter="12">
            <ElCol :span="12">
              <div class="mb-3">
                <div class="text-xs text-gray-400">总收益率</div>
                <div class="text-lg font-bold text-green-500">+15.6%</div>
              </div>
              <div class="mb-3">
                <div class="text-xs text-gray-400">年化收益</div>
                <div class="text-lg font-bold text-green-500">+15.6%</div>
              </div>
              <div class="mb-3">
                <div class="text-xs text-gray-400">夏普比率</div>
                <div class="text-lg font-bold">1.85</div>
              </div>
              <div class="mb-3">
                <div class="text-xs text-gray-400">索提诺比率</div>
                <div class="text-lg font-bold">2.4</div>
              </div>
            </ElCol>
            <ElCol :span="12">
              <div class="mb-3">
                <div class="text-xs text-gray-400">最大回撤</div>
                <div class="text-lg font-bold text-red-500">-8.2%</div>
              </div>
              <div class="mb-3">
                <div class="text-xs text-gray-400">波动率</div>
                <div class="text-lg font-bold">12.3%</div>
              </div>
              <div class="mb-3">
                <div class="text-xs text-gray-400">胜率</div>
                <div class="text-lg font-bold">62.5%</div>
              </div>
              <div class="mb-3">
                <div class="text-xs text-gray-400">VaR (95%)</div>
                <div class="text-lg font-bold text-orange-500">-2.1%</div>
              </div>
            </ElCol>
          </ElRow>
        </ElCard>
      </ElCol>
    </ElRow>

    <!-- 历史记录 -->
    <ElCard shadow="never" header="历史回测记录">
      <ElTable :data="backtestHistory" stripe>
        <ElTableColumn prop="strategyName" label="策略" width="160" />
        <ElTableColumn prop="symbol" label="标的" width="100" />
        <ElTableColumn prop="period" label="回测区间" width="160" />
        <ElTableColumn prop="totalReturn" label="总收益" width="90" align="right">
          <template #default="{ row }"><span :class="row.totalReturn >= 0 ? 'text-green-500' : 'text-red-500'">{{ row.totalReturn >= 0 ? '+' : '' }}{{ row.totalReturn }}%</span></template>
        </ElTableColumn>
        <ElTableColumn prop="sharpeRatio" label="夏普" width="70" align="right" />
        <ElTableColumn prop="maxDrawdown" label="回撤" width="80" align="right">
          <template #default="{ row }"><span class="text-red-500">{{ row.maxDrawdown }}%</span></template>
        </ElTableColumn>
        <ElTableColumn prop="winRate" label="胜率" width="70" align="right"><template #default="{ row }">{{ row.winRate }}%</template></ElTableColumn>
        <ElTableColumn prop="trades" label="交易次数" width="80" align="center" />
        <ElTableColumn prop="status" label="状态" width="80" align="center">
          <template #default="{ row }"><ElTag :type="getStatusType(row.status)">{{ getStatusLabel(row.status) }}</ElTag></template>
        </ElTableColumn>
        <ElTableColumn label="操作" width="100" fixed="right">
          <template #default="{ row }"><ElButton link type="primary" size="small" @click="handleExport(row)"><Download class="w-4 h-4 mr-1" />导出</ElButton></template>
        </ElTableColumn>
      </ElTable>
    </ElCard>
  </div>
</template>

<style scoped>
.backtest-page { min-height: 100%; }
</style>
