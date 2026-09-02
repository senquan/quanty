<script setup lang="ts">
import { ref, computed } from 'vue';
import { Search, Clock, TrendingUp, TrendingDown } from '@lucide/vue';

export interface RebalanceRow {
  time: string;
  symbol: string;
  name: string;
  price: number;
  quantity: number;
  amount: number;
  commission: number;
}

const props = defineProps<{ logs: RebalanceRow[] }>();

const searchQuery = ref('');

const fmt = (v: number) =>
  new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v);

const displayed = computed(() => {
  const q = searchQuery.value.trim().toLowerCase();
  if (!q) return props.logs;
  return props.logs.filter(
    (r) => r.symbol.toLowerCase().includes(q) || r.name.toLowerCase().includes(q),
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
            每日调仓流水记录 (Rebalance Logs)
          </h3>
          <span class="px-2 py-0.5 rounded-full text-xs font-mono font-semibold bg-indigo-50 text-indigo-700 border border-indigo-100">
            {{ displayed.length }} 笔
          </span>
        </div>
        <p class="text-xs text-slate-500">逐笔记录成交价格、数量、金额与手续费</p>
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
            <th class="py-3 px-3">调仓时间</th>
            <th class="py-3 px-2">标的代码 / 名称</th>
            <th class="py-3 px-2 text-right">成交价格</th>
            <th class="py-3 px-2 text-right">成交数量</th>
            <th class="py-3 px-2 text-right">成交金额</th>
            <th class="py-3 px-2 text-right">手续费</th>
          </tr>
        </thead>
        <tbody class="divide-y divide-slate-100 text-xs font-mono">
          <tr v-for="(r, i) in displayed" :key="r.symbol + r.time + i" class="hover:bg-slate-50/80 transition-colors">
            <td class="py-3 px-3 text-slate-500 text-[11px] whitespace-nowrap">
              <div class="flex items-center gap-1.5">
                <Clock class="w-3 h-3 text-slate-400" />
                {{ r.time }}
              </div>
            </td>
            <td class="py-3 px-2 font-sans">
              <div class="font-bold text-slate-900">{{ r.name }}</div>
              <div class="text-[11px] font-mono text-slate-500">{{ r.symbol }}</div>
            </td>
            <td class="py-3 px-2 text-right font-bold text-slate-800">¥{{ r.price.toFixed(2) }}</td>
            <td class="py-3 px-2 text-right font-bold text-slate-800">{{ r.quantity.toLocaleString() }}</td>
            <td class="py-3 px-2 text-right font-bold text-slate-900">¥{{ fmt(r.amount) }}</td>
            <td class="py-3 px-2 text-right text-slate-500 text-[11px]">¥{{ r.commission.toFixed(2) }}</td>
          </tr>
          <tr v-if="displayed.length === 0">
            <td colspan="6" class="py-10 text-center text-slate-500 font-sans">暂无调仓流水</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
