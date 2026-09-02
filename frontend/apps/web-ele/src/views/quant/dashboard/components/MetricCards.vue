<script setup lang="ts">
import { Wallet, PieChart, Coins, Award, TrendingUp, TrendingDown } from '@lucide/vue';

const props = defineProps<{
  metrics: {
    totalAssets: number;
    marketValue: number;
    availableCash: number;
    frozenCash: number;
    positionRatio: number;
    totalPnL: number;
    totalPnLPct: number;
    todayPnL: number;
    todayPnLPct: number;
    annualizedReturn: number;
  };
  mode: 'paper' | 'live';
}>();

const fmt = (v: number) =>
  new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(v);

const pct = (v: number) => `${v > 0 ? '+' : ''}${v.toFixed(2)}%`;
</script>

<template>
  <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
    <!-- 1. 总资产 (Total Assets) -->
    <div class="bg-white rounded-xl p-4 lg:p-5 border border-slate-200 hover:border-slate-300 transition-all shadow-sm flex flex-col justify-between">
      <div>
        <div class="flex items-center justify-between text-slate-500 text-xs mb-2">
          <span class="flex items-center gap-1.5 font-medium uppercase tracking-wide">
            <Wallet class="w-4 h-4 text-indigo-600" />
            组合总资产 (Total Assets)
          </span>
          <span
            class="px-2 py-0.5 rounded text-[10px] font-semibold"
            :class="props.mode === 'live' ? 'bg-emerald-50 text-emerald-700 border border-emerald-100' : 'bg-amber-50 text-amber-700 border border-amber-100'"
          >
            {{ props.mode === 'live' ? '实盘主账户' : '模拟账户' }}
          </span>
        </div>
        <div class="flex items-baseline gap-1 mt-1">
          <span class="text-xs text-slate-400 font-mono">¥</span>
          <span class="text-2xl lg:text-3xl font-bold font-mono tracking-tight text-slate-900">
            {{ fmt(props.metrics.totalAssets) }}
          </span>
        </div>
      </div>
      <div class="mt-4 pt-3 border-t border-slate-100 flex items-center justify-between text-xs font-mono">
        <span class="text-slate-500">今日盈亏:</span>
        <div
          class="flex items-center gap-1.5"
          :class="props.metrics.todayPnL >= 0 ? 'text-emerald-600' : 'text-rose-600'"
        >
          <TrendingUp v-if="props.metrics.todayPnL >= 0" class="w-3.5 h-3.5" />
          <TrendingDown v-else class="w-3.5 h-3.5" />
          <span class="font-bold">{{ props.metrics.todayPnL >= 0 ? '+' : '' }}¥{{ fmt(props.metrics.todayPnL) }}</span>
          <span class="font-semibold">({{ pct(props.metrics.todayPnLPct) }})</span>
        </div>
      </div>
    </div>

    <!-- 2. 持仓市值 (Holding Value) & 仓位占比 -->
    <div class="bg-white rounded-xl p-4 lg:p-5 border border-slate-200 hover:border-slate-300 transition-all shadow-sm flex flex-col justify-between">
      <div>
        <div class="flex items-center justify-between text-slate-500 text-xs mb-2">
          <span class="flex items-center gap-1.5 font-medium uppercase tracking-wide">
            <PieChart class="w-4 h-4 text-indigo-600" />
            持仓市值 (Holding Value)
          </span>
          <span class="text-xs font-mono font-semibold text-slate-700">
            仓位: <strong class="text-indigo-600">{{ props.metrics.positionRatio }}%</strong>
          </span>
        </div>
        <div class="flex items-baseline gap-1 mt-1">
          <span class="text-xs text-slate-400 font-mono">¥</span>
          <span class="text-2xl lg:text-3xl font-bold font-mono tracking-tight text-slate-900">
            {{ fmt(props.metrics.marketValue) }}
          </span>
        </div>
      </div>
      <div class="mt-4">
        <div class="w-full bg-slate-100 rounded-full h-1.5 overflow-hidden mb-2">
          <div
            class="bg-indigo-600 h-full rounded-full transition-all duration-500"
            :style="{ width: `${Math.min(100, Math.max(0, props.metrics.positionRatio))}%` }"
          ></div>
        </div>
        <div class="flex items-center justify-between text-xs text-slate-500 font-mono">
          <span>现金比例: {{ (100 - props.metrics.positionRatio).toFixed(1) }}%</span>
          <span class="text-slate-400">风控上限: 90%</span>
        </div>
      </div>
    </div>

    <!-- 3. 可用资金 (Available Cash) -->
    <div class="bg-white rounded-xl p-4 lg:p-5 border border-slate-200 hover:border-slate-300 transition-all shadow-sm flex flex-col justify-between">
      <div>
        <div class="flex items-center justify-between text-slate-500 text-xs mb-2">
          <span class="flex items-center gap-1.5 font-medium uppercase tracking-wide">
            <Coins class="w-4 h-4 text-indigo-600" />
            可用资金 (Available Cash)
          </span>
          <span class="text-emerald-600 font-medium text-xs font-mono bg-emerald-50 px-1.5 py-0.5 rounded border border-emerald-100">T+0 就绪</span>
        </div>
        <div class="flex items-baseline gap-1 mt-1">
          <span class="text-xs text-slate-400 font-mono">¥</span>
          <span class="text-2xl lg:text-3xl font-bold font-mono tracking-tight text-slate-900">
            {{ fmt(props.metrics.availableCash) }}
          </span>
        </div>
      </div>
      <div class="mt-4 pt-3 border-t border-slate-100 flex items-center justify-between text-xs font-mono">
        <span class="text-slate-500">冻结/衍生品保证金:</span>
        <span class="text-slate-800 font-medium">¥{{ fmt(props.metrics.frozenCash) }}</span>
      </div>
    </div>

    <!-- 4. 累计盈亏与量化收益率 -->
    <div class="bg-white rounded-xl p-4 lg:p-5 border border-slate-200 hover:border-slate-300 transition-all shadow-sm flex flex-col justify-between">
      <div>
        <div class="flex items-center justify-between text-slate-500 text-xs mb-2">
          <span class="flex items-center gap-1.5 font-medium uppercase tracking-wide">
            <Award class="w-4 h-4 text-indigo-600" />
            累计量化盈亏 (Total PnL)
          </span>
          <span class="px-2 py-0.5 rounded bg-indigo-50 text-indigo-700 border border-indigo-100 text-[10px] font-semibold">
            年化 {{ props.metrics.annualizedReturn }}%
          </span>
        </div>
        <div class="flex items-baseline gap-1 mt-1">
          <span class="text-xs font-mono" :class="props.metrics.totalPnL >= 0 ? 'text-emerald-600' : 'text-rose-600'">¥</span>
          <span
            class="text-2xl lg:text-3xl font-bold font-mono tracking-tight"
            :class="props.metrics.totalPnL >= 0 ? 'text-emerald-600' : 'text-rose-600'"
          >
            {{ props.metrics.totalPnL >= 0 ? '+' : '' }}{{ fmt(props.metrics.totalPnL) }}
          </span>
        </div>
      </div>
      <div class="mt-4 pt-3 border-t border-slate-100 flex items-center justify-between text-xs font-mono">
        <span class="text-slate-500">累计收益率:</span>
        <span
          class="font-bold text-sm"
          :class="props.metrics.totalPnLPct >= 0 ? 'text-emerald-600' : 'text-rose-600'"
        >
          {{ pct(props.metrics.totalPnLPct) }}
        </span>
      </div>
    </div>
  </div>
</template>
