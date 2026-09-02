<script lang="ts" setup>
import { computed, onMounted, ref } from 'vue';

import {
  ElAlert,
  ElCard,
  ElCol,
  ElEmpty,
  ElProgress,
  ElRadioButton,
  ElRadioGroup,
  ElRow,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';
import { BarChart3, TrendingDown, TrendingUp, Wallet } from '@lucide/vue';

import { listFactorStrategiesApi } from '#/api/factor-strategy';
import {
  getOverviewApi,
  getPositionsApi,
  getRebalancesApi,
  getTradingModeApi,
} from '#/api/core/trading';
import type {
  ModeInfo,
  Position,
  RebalanceRecord,
  TradeMode,
  TradingOverview,
} from '#/api/core/trading';

const loading = ref(false);
const errorMsg = ref('');
const mode = ref<TradeMode>('paper');

const modeInfo = ref<ModeInfo | null>(null);
const overview = ref<TradingOverview | null>(null);
const positions = ref<Position[]>([]);
const rebalances = ref<RebalanceRecord[]>([]);
const strategyCount = ref(0);
const activeStrategyCount = ref(0);

const currentMode = computed(() =>
  (modeInfo.value?.modes || []).find((m) => m.mode === mode.value),
);

function money(v?: number | null) {
  if (v === null || v === undefined) return '--';
  return `¥${v.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`;
}

const stats = computed(() => {
  const ov = overview.value;
  const pnl = ov?.total_pnl ?? 0;
  return [
    { label: '总资产', value: money(ov?.total_assets), color: '#409eff', icon: 'wallet' },
    { label: '持仓市值', value: money(ov?.market_value), color: '#e6a23c', icon: 'chart' },
    { label: '可用资金', value: money(ov?.cash_balance), color: '#909399', icon: 'wallet' },
    {
      label: '累计盈亏',
      value: money(pnl),
      suffix: ov ? `(${ov.total_pnl_pct}%)` : '',
      color: pnl >= 0 ? '#67c23a' : '#f56c6c',
      icon: pnl >= 0 ? 'up' : 'down',
    },
  ];
});

/** 持仓分布（占总资产比重） */
const allocation = computed(() => {
  const total = overview.value?.total_assets || 0;
  return (positions.value || []).map((p) => ({
    symbol: p.symbol,
    quantity: p.quantity,
    market_value: p.market_value,
    pnl_percent: p.pnl_percent,
    weight: total ? Math.round((p.market_value / total) * 10_000) / 100 : 0,
  }));
});

async function load() {
  loading.value = true;
  errorMsg.value = '';
  try {
    const [mi, ov, pos, reb, strats] = await Promise.all([
      getTradingModeApi(),
      getOverviewApi(mode.value),
      getPositionsApi(mode.value),
      getRebalancesApi(20),
      listFactorStrategiesApi(),
    ]);
    modeInfo.value = mi;
    overview.value = ov;
    positions.value = pos || [];
    rebalances.value = reb || [];
    strategyCount.value = (strats || []).length;
    activeStrategyCount.value = (strats || []).filter((s) => s.is_active).length;
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

    <!-- 模式切换 -->
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

    <!-- 统计卡片 -->
    <ElRow :gutter="16" class="mb-4">
      <ElCol v-for="s in stats" :key="s.label" :span="6">
        <ElCard v-loading="loading" shadow="hover">
          <div class="text-sm text-gray-500 mb-1 flex items-center gap-1">
            <Wallet v-if="s.icon === 'wallet'" class="w-4 h-4" />
            <BarChart3 v-if="s.icon === 'chart'" class="w-4 h-4" />
            <TrendingUp v-if="s.icon === 'up'" class="w-4 h-4" />
            <TrendingDown v-if="s.icon === 'down'" class="w-4 h-4" />
            {{ s.label }}
          </div>
          <div class="text-xl font-bold" :style="{ color: s.color }">
            {{ s.value }}
            <span v-if="s.suffix" class="text-sm font-normal ml-1">{{ s.suffix }}</span>
          </div>
        </ElCard>
      </ElCol>
    </ElRow>

    <ElRow :gutter="16" class="mb-4">
      <!-- 持仓分布 -->
      <ElCol :span="10">
        <ElCard v-loading="loading" shadow="hover" header="持仓分布">
          <ElEmpty v-if="!allocation.length" description="暂无持仓" :image-size="60" />
          <div v-else class="space-y-3">
            <div v-for="p in allocation" :key="p.symbol">
              <div class="flex justify-between text-sm mb-1">
                <span>{{ p.symbol }}</span>
                <span class="text-gray-500">{{ money(p.market_value) }} · {{ p.weight }}%</span>
              </div>
              <ElProgress :percentage="Math.min(p.weight, 100)" :stroke-width="8" :show-text="false" />
            </div>
          </div>
        </ElCard>
      </ElCol>

      <!-- 策略与调仓 -->
      <ElCol :span="14">
        <ElCard v-loading="loading" shadow="hover" header="策略与调仓">
          <div class="flex gap-6 mb-3 text-sm">
            <span class="text-gray-500">策略总数：<b class="text-gray-900">{{ strategyCount }}</b></span>
            <span class="text-gray-500">启用中：<b class="text-green-600">{{ activeStrategyCount }}</b></span>
            <span class="text-gray-500">持仓数：<b class="text-gray-900">{{ overview?.position_count ?? 0 }}</b></span>
          </div>
          <ElTable :data="rebalances" size="small" max-height="260">
            <ElTableColumn prop="strategy_name" label="策略" min-width="120" show-overflow-tooltip />
            <ElTableColumn prop="rebalance_date" label="调仓日" width="110" />
            <ElTableColumn prop="target_count" label="目标数" width="80" align="right" />
            <ElTableColumn prop="orders_placed" label="下单数" width="80" align="right" />
            <ElTableColumn prop="status" label="状态" width="90" align="center">
              <template #default="{ row }">
                <ElTag :type="row.status === 'success' ? 'success' : row.status === 'error' ? 'danger' : 'info'" size="small">
                  {{ row.status }}
                </ElTag>
              </template>
            </ElTableColumn>
            <template #empty>
              <ElEmpty description="暂无调仓记录" :image-size="60" />
            </template>
          </ElTable>
        </ElCard>
      </ElCol>
    </ElRow>
  </div>
</template>

<style scoped>
.quant-dashboard {
  min-height: 100%;
}
</style>
