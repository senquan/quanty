<script setup lang="ts">
import { ref, computed } from 'vue';
import { Search, TrendingUp, TrendingDown } from '@lucide/vue';

export interface PositionRow {
  symbol: string;
  name: string;
  quantity: number;
  avgPrice: number;
  marketValue: number;
  weightPct: number;
  lastPrice: number;
  prevClose: number | null;
  todayPnl: number;
  totalPnl: number;
}

const props = defineProps<{ positions: PositionRow[] }>();

const searchQuery = ref('');

const fmt = (v: number) =>
  new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v);

const displayed = computed(() => {
  const q = searchQuery.value.trim().toLowerCase();
  if (!q) return props.positions;
  return props.positions.filter(
    (p) => p.symbol.toLowerCase().includes(q) || p.name.toLowerCase().includes(q),
  );
});
</script>

<template>
  <div class="bg-white rounded-xl p-4 lg:p-5 border border-slate-200 shadow-sm flex flex-col h-full">
    <!-- 工具栏 -->
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-4 border-b border-slate-100">
      <div>
        <div class="flex items-center gap-2">
          <h3 class="font-bold text-base text-slate-900 flex items-center gap-2">
            当前持仓列表 (Portfolio Positions)
          </h3>
          <span class="px-2 py-0.5 rounded-full text-xs font-mono font-semibold bg-indigo-50 text-indigo-700 border border-indigo-100">
            {{ displayed.length }} 标的
          </span>
        </div>
        <p class="text-xs text-slate-500">实时计算持仓市值、持仓均价与逐笔浮动盈亏</p>
      </div>
      <div class="relative">
        <Search class="w-3.5 h-3.5 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
        <input
          v-model="searchQuery"
          type="text"
          placeholder="搜索代码 / 名称"
          class="bg-slate-50 text-slate-900 text-xs pl-8 pr-3 py-1.5 rounded-xl border border-slate-200 focus:outline-none focus:bg-white focus:border-indigo-500 w-44 transition-all placeholder:text-slate-400 font-sans shadow-sm"
        />
      </div>
    </div>

    <!-- 表格（可卷动） -->
    <div class="overflow-y-auto flex-1 mt-2 -mx-4 sm:mx-0 sm:px-4">
      <table class="w-full text-left border-collapse">
        <thead class="sticky top-0 bg-white z-10">
          <tr class="border-b border-slate-200 bg-slate-50/80 text-[11px] font-sans font-semibold text-slate-500 uppercase tracking-wider">
            <th class="py-3 px-3">标的代码 / 名称</th>
            <th class="py-3 px-2 text-right">持仓</th>
            <th class="py-3 px-2 text-right">成本均价</th>
            <th class="py-3 px-2 text-right">持仓市值</th>
            <th class="py-3 px-2 text-right">仓位占比</th>
            <th class="py-3 px-2 text-right" title="（现价 − 昨收）× 持仓；无昨收记录时按组合当日收益按市值占比估算">今日盈亏</th>
            <th class="py-3 px-2 text-right">累计浮盈</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-100 text-xs font-mono">
          <tr v-for="pos in displayed" :key="pos.symbol" class="hover:bg-slate-50/80 transition-colors">
            <td class="py-3 px-3 font-sans">
              <div class="font-bold text-slate-900 text-sm">{{ pos.name }}</div>
              <div class="text-[11px] font-mono text-slate-500">{{ pos.symbol }}</div>
            </td>
            <td class="py-3 px-2 text-right font-bold text-slate-800">{{ pos.quantity.toLocaleString() }}</td>
            <td class="py-3 px-2 text-right text-slate-600">¥{{ pos.avgPrice.toFixed(2) }}</td>
            <td class="py-3 px-2 text-right font-bold text-slate-900">¥{{ fmt(pos.marketValue) }}</td>
            <td class="py-3 px-2 text-right">
              <div class="flex items-center justify-end gap-1.5">
                <span class="text-indigo-600 font-semibold">{{ pos.weightPct }}%</span>
                <div class="w-12 bg-slate-100 rounded-full h-1.5 overflow-hidden">
                  <div class="bg-indigo-600 h-full rounded-full" :style="{ width: `${Math.min(100, pos.weightPct * 3)}%` }"></div>
                </div>
              </div>
            </td>
            <td class="py-3 px-2 text-right">
              <div class="font-semibold" :class="pos.todayPnl >= 0 ? 'text-emerald-600' : 'text-rose-600'">
                {{ pos.todayPnl >= 0 ? '+' : '' }}¥{{ fmt(pos.todayPnl) }}
              </div>
            </td>
            <td class="py-3 px-2 text-right">
              <div class="font-bold" :class="pos.totalPnl >= 0 ? 'text-emerald-600' : 'text-rose-600'">
                {{ pos.totalPnl >= 0 ? '+' : '' }}¥{{ fmt(pos.totalPnl) }}
              </div>
            </td>
          </tr>
          <tr v-if="displayed.length === 0">
            <td colspan="7" class="py-10 text-center text-slate-500 font-sans">暂无持仓</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
