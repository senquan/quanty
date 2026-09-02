<script lang="ts" setup>
import { computed, onMounted, ref } from 'vue';

import {
  ElAlert,
  ElCard,
  ElCol,
  ElRadioButton,
  ElRadioGroup,
  ElRow,
  ElTag,
} from 'element-plus';

import {
  getAvailableSymbolsApi,
  getOverviewApi,
  getPortfolioValuesApi,
  getPositionsApi,
  getTradesApi,
  getTradingModeApi,
} from '#/api/core/trading';
import type {
  ModeInfo,
  PortfolioValuePoint,
  Position,
  TradeMode,
  TradeRecord,
  TradingOverview,
} from '#/api/core/trading';
import MetricCards from './components/MetricCards.vue';
import YieldCurveChart from './components/YieldCurveChart.vue';
import type { YieldPoint } from './components/YieldCurveChart.vue';
import PositionsTable from './components/PositionsTable.vue';
import type { PositionRow } from './components/PositionsTable.vue';
import RebalanceLogsTable from './components/RebalanceLogsTable.vue';
import type { RebalanceRow } from './components/RebalanceLogsTable.vue';

const loading = ref(false);
const errorMsg = ref('');
const mode = ref<TradeMode>('paper');

const modeInfo = ref<ModeInfo | null>(null);
const overview = ref<TradingOverview | null>(null);
const positions = ref<Position[]>([]);
const trades = ref<TradeRecord[]>([]);
const portfolioValues = ref<PortfolioValuePoint[]>([]);
const symbolNameMap = ref<Record<string, string>>({});

const currentMode = computed(() =>
  (modeInfo.value?.modes || []).find((m) => m.mode === mode.value),
);

function diffDays(a: string, b: string): number {
  const da = new Date(a).getTime();
  const db = new Date(b).getTime();
  return Math.max(0, Math.round((db - da) / 86_400_000));
}

/** 四张统计卡指标（真实数据 + 由组合收益快照派生） */
const metrics = computed(() => {
  const ov = overview.value;
  const pv = portfolioValues.value;
  const totalAssets = ov?.total_assets ?? 0;
  const marketValue = ov?.market_value ?? 0;
  const prev = pv.length >= 2 ? pv[pv.length - 2] : null;
  const latest = pv.length ? pv[pv.length - 1] : null;

  const todayPnl = latest && prev ? latest.total_assets - prev.total_assets : 0;
  const todayPnLPct = latest?.daily_return ?? 0;

  let annualized = 0;
  if (latest?.cumulative_return != null && pv.length >= 2) {
    const days = diffDays(pv[0].value_date, latest.value_date);
    if (days > 0) {
      annualized = (Math.pow(1 + latest.cumulative_return / 100, 365 / days) - 1) * 100;
    }
  }

  return {
    totalAssets,
    marketValue,
    availableCash: ov?.cash_balance ?? 0,
    frozenCash: ov?.frozen_cash ?? 0,
    positionRatio: totalAssets ? Math.round((marketValue / totalAssets) * 10_000) / 100 : 0,
    totalPnL: ov?.total_pnl ?? 0,
    totalPnLPct: ov?.total_pnl_pct ?? 0,
    todayPnL: todayPnl,
    todayPnLPct: todayPnLPct,
    annualizedReturn: Math.round(annualized * 100) / 100,
  };
});

const chartPoints = computed<YieldPoint[]>(() =>
  (portfolioValues.value || []).map((p) => ({
    date: (p.value_date || '').slice(0, 10),
    cumulativeReturn: p.cumulative_return ?? 0,
    totalAssets: p.total_assets,
  })),
);

/** 持仓列表（补名称 / 仓位占比 / 今日盈亏估算 / 累计浮盈） */
const totalMarketValue = computed(() =>
  (positions.value || []).reduce((s, p) => s + (p.market_value || 0), 0),
);
const todayPnlAccount = computed(() => {
  const pv = portfolioValues.value;
  return pv.length >= 2 ? pv[pv.length - 1].total_assets - pv[pv.length - 2].total_assets : 0;
});
const positionRows = computed<PositionRow[]>(() => {
  const total = totalAssets.value;
  const mv = totalMarketValue.value;
  return (positions.value || []).map((p) => ({
    symbol: p.symbol,
    name: symbolNameMap.value[p.symbol] || p.symbol,
    quantity: p.quantity,
    avgPrice: p.avg_price,
    marketValue: p.market_value,
    weightPct: total ? Math.round((p.market_value / total) * 10_000) / 100 : 0,
    todayPnl: Math.round((mv ? todayPnlAccount.value * (p.market_value / mv) : 0) * 100) / 100,
    totalPnl: p.unrealized_pnl,
  }));
});

const rebalanceRows = computed<RebalanceRow[]>(() =>
  (trades.value || []).map((t) => ({
    time: t.trade_time ? t.trade_time.replace('T', ' ').slice(0, 19) : '--',
    symbol: t.symbol,
    name: symbolNameMap.value[t.symbol] || t.symbol,
    price: t.price,
    quantity: t.quantity,
    amount: t.amount,
    commission: t.commission,
  })),
);

const totalAssets = computed(() => overview.value?.total_assets ?? 0);

async function load() {
  loading.value = true;
  errorMsg.value = '';
  try {
    const [mi, ov, pos, tr, pv, syms] = await Promise.all([
      getTradingModeApi(),
      getOverviewApi(mode.value),
      getPositionsApi(mode.value),
      getTradesApi(mode.value, { limit: 200 }),
      getPortfolioValuesApi(mode.value, { limit: 180 }),
      getAvailableSymbolsApi(),
    ]);
    modeInfo.value = mi;
    overview.value = ov;
    positions.value = pos || [];
    trades.value = tr || [];
    portfolioValues.value = pv || [];
    symbolNameMap.value = Object.fromEntries(
      (syms?.symbols || []).map((s) => [s.symbol, s.name]),
    );
  } catch (e: any) {
    errorMsg.value = e?.message || '加载概览数据失败';
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="quant-dashboard p-4">
    <ElAlert v-if="errorMsg" :title="errorMsg" type="error" show-icon class="mb-4" />

    <!-- 模式切换（保持不变） -->
    <ElCard shadow="never" class="mb-4">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-3">
          <span class="text-sm text-gray-500">交易模式</span>
          <ElRadioGroup v-model="mode" @change="load">
            <ElRadioButton value="paper">模拟盘</ElRadioButton>
            <ElRadioButton value="live">实盘</ElRadioButton>
          </ElRadioGroup>
        </div>
        <ElTag v-if="currentMode" :type="currentMode.ready ? 'success' : 'warning'">
          {{ currentMode.message }}
        </ElTag>
      </div>
    </ElCard>

    <!-- 四个统计卡片 -->
    <MetricCards :metrics="metrics" :mode="mode" v-loading="loading" class="mb-4" />

    <!-- 实时收益率曲线 -->
    <YieldCurveChart
      :points="chartPoints"
      :initial-capital="overview?.initial_capital"
      :strategy-name="modeInfo ? (mode === 'live' ? '实盘组合' : '模拟组合') : undefined"
      v-loading="loading"
      class="mb-4"
    />

    <!-- 下方两张等高、可卷动表格 -->
    <ElRow :gutter="16">
      <ElCol :xs="24" :lg="12" class="mb-4 lg:mb-0">
        <PositionsTable :positions="positionRows" v-loading="loading" class="h-[480px]" />
      </ElCol>
      <ElCol :xs="24" :lg="12" class="mb-4 lg:mb-0">
        <RebalanceLogsTable :logs="rebalanceRows" v-loading="loading" class="h-[480px]" />
      </ElCol>
    </ElRow>
  </div>
</template>

<style scoped>
.quant-dashboard {
  min-height: 100%;
}
</style>
