<script lang="ts" setup>
import { ref } from 'vue';

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
  {
    id: 1,
    strategyName: '双均线交叉策略',
    symbol: 'AAPL',
    period: '2023-01 ~ 2023-12',
    totalReturn: 15.6,
    sharpeRatio: 1.85,
    maxDrawdown: -8.2,
    winRate: 62.5,
    trades: 48,
    status: 'completed',
    createdAt: '2026-04-14 10:30',
  },
  {
    id: 2,
    strategyName: 'RSI均值回归策略',
    symbol: 'MSFT',
    period: '2023-06 ~ 2023-12',
    totalReturn: 8.7,
    sharpeRatio: 1.23,
    maxDrawdown: -12.1,
    winRate: 58.3,
    trades: 36,
    status: 'completed',
    createdAt: '2026-04-13 15:20',
  },
  {
    id: 3,
    strategyName: '动量突破策略',
    symbol: 'BTC/USDT',
    period: '2023-01 ~ 2023-12',
    totalReturn: 0,
    sharpeRatio: 0,
    maxDrawdown: 0,
    winRate: 0,
    trades: 0,
    status: 'running',
    createdAt: '2026-04-14 14:00',
  },
]);

const getStatusType = (status: string) => {
  const map: Record<string, string> = {
    completed: 'success',
    running: 'warning',
    failed: 'danger',
  };
  return map[status] || '';
};

const getStatusLabel = (status: string) => {
  const map: Record<string, string> = {
    completed: '已完成',
    running: '运行中',
    failed: '失败',
  };
  return map[status] || status;
};

const handleRunBacktest = () => {
  console.log('运行回测');
};

const handleExport = (row: BacktestRecord) => {
  console.log('导出结果:', row.id);
};
</script>

<template>
  <div class="backtest-page p-4">
    <!-- 顶部操作栏 -->
    <ElCard shadow="never" class="mb-4">
      <ElRow :gutter="12" align="middle">
        <ElCol>
          <ElSelect v-model="selectedStrategy" placeholder="选择策略" clearable style="width: 200px">
            <ElOption label="双均线交叉策略" value="1" />
            <ElOption label="RSI均值回归策略" value="2" />
            <ElOption label="动量突破策略" value="3" />
          </ElSelect>
        </ElCol>
        <ElCol>
          <ElSelect placeholder="数据源" style="width: 160px">
            <ElOption label="Yahoo Finance" value="yahoo" />
            <ElOption label="CCXT" value="ccxt" />
          </ElSelect>
        </ElCol>
        <ElCol>
          <ElInput placeholder="标的代码" style="width: 140px" />
        </ElCol>
        <ElCol :flex="1" />
        <ElCol>
          <ElButton type="primary" @click="handleRunBacktest">
            <Play class="w-4 h-4 mr-1" />
            运行回测
          </ElButton>
        </ElCol>
      </ElRow>
    </ElCard>

    <!-- 回测结果 -->
    <ElCard shadow="never">
      <ElTabs>
        <ElTabPane label="净值曲线">
          <div class="h-80 flex items-center justify-center text-gray-400 border-dashed border rounded">
            📈 ECharts 净值曲线图表区域
          </div>
        </ElTabPane>
        <ElTabPane label="收益分布">
          <div class="h-80 flex items-center justify-center text-gray-400 border-dashed border rounded">
            📊 ECharts 收益分布直方图
          </div>
        </ElTabPane>
        <ElTabPane label="详细指标">
          <ElRow :gutter="16">
            <ElCol :span="8">
              <ElCard header="收益指标" shadow="never" class="mb-3">
                <div class="space-y-2 text-sm">
                  <ElRow justify="space-between"><span>总收益率</span><span class="text-green-500">+15.6%</span></ElRow>
                  <ElRow justify="space-between"><span>年化收益</span><span class="text-green-500">+15.6%</span></ElRow>
                  <ElRow justify="space-between"><span>月均收益</span><span>+1.3%</span></ElRow>
                </div>
              </ElCard>
            </ElCol>
            <ElCol :span="8">
              <ElCard header="风险指标" shadow="never" class="mb-3">
                <div class="space-y-2 text-sm">
                  <ElRow justify="space-between"><span>波动率</span><span>12.3%</span></ElRow>
                  <ElRow justify="space-between"><span>最大回撤</span><span class="text-red-500">-8.2%</span></ElRow>
                  <ElRow justify="space-between"><span>VaR (95%)</span><span>-2.1%</span></ElRow>
                </div>
              </ElCard>
            </ElCol>
            <ElCol :span="8">
              <ElCard header="风险调整收益" shadow="never" class="mb-3">
                <div class="space-y-2 text-sm">
                  <ElRow justify="space-between"><span>夏普比率</span><span>1.85</span></ElRow>
                  <ElRow justify="space-between"><span>索提诺比率</span><span>2.4</span></ElRow>
                  <ElRow justify="space-between"><span>卡尔玛比率</span><span>1.9</span></ElRow>
                </div>
              </ElCard>
            </ElCol>
          </ElRow>
        </ElTabPane>
        <ElTabPane label="历史记录">
          <ElTable :data="backtestHistory" stripe>
            <ElTableColumn prop="strategyName" label="策略" width="160" />
            <ElTableColumn prop="symbol" label="标的" width="100" />
            <ElTableColumn prop="period" label="回测区间" width="160" />
            <ElTableColumn prop="totalReturn" label="总收益" width="90" align="right">
              <template #default="{ row }">
                <span :class="row.totalReturn >= 0 ? 'text-green-500' : 'text-red-500'">
                  {{ row.totalReturn >= 0 ? '+' : '' }}{{ row.totalReturn }}%
                </span>
              </template>
            </ElTableColumn>
            <ElTableColumn prop="sharpeRatio" label="夏普" width="70" align="right" />
            <ElTableColumn prop="maxDrawdown" label="回撤" width="80" align="right">
              <template #default="{ row }">
                <span class="text-red-500">{{ row.maxDrawdown }}%</span>
              </template>
            </ElTableColumn>
            <ElTableColumn prop="winRate" label="胜率" width="70" align="right">
              <template #default="{ row }">{{ row.winRate }}%</template>
            </ElTableColumn>
            <ElTableColumn prop="trades" label="交易次数" width="80" align="center" />
            <ElTableColumn prop="status" label="状态" width="80" align="center">
              <template #default="{ row }">
                <ElTag :type="getStatusType(row.status)">{{ getStatusLabel(row.status) }}</ElTag>
              </template>
            </ElTableColumn>
            <ElTableColumn label="操作" width="100" fixed="right">
              <template #default="{ row }">
                <ElButton link type="primary" size="small" @click="handleExport(row)">
                  <Download class="w-4 h-4 mr-1" />导出
                </ElButton>
              </template>
            </ElTableColumn>
          </ElTable>
        </ElTabPane>
      </ElTabs>
    </ElCard>
  </div>
</template>

<style scoped>
.backtest-page {
  min-height: 100%;
}
</style>
