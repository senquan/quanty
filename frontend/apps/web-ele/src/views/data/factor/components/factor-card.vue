<script lang="ts" setup>
import { computed } from 'vue';

import {
  Activity,
  Flame,
  Landmark,
  Settings,
  Trash2,
  TrendingUp,
  Zap,
} from '@lucide/vue';
import { ElButton, ElCard, ElTag } from 'element-plus';

import type { Factor, FactorCategory } from '../types';

const props = withDefaults(
  defineProps<{
    factor: Factor;
    isSelected?: boolean;
  }>(),
  { isSelected: false },
);

const emit = defineEmits<{
  select: [factor: Factor];
  edit: [factor: Factor];
  delete: [id: string];
}>();

interface CategoryTheme {
  label: string;
  type: 'danger' | 'info' | 'primary' | 'success' | 'warning';
  icon: any;
  color: string;
}

const categoryThemes: Record<FactorCategory, CategoryTheme> = {
  momentum: { label: '动量类', type: 'warning', icon: TrendingUp, color: '#e6a23c' },
  volatility: { label: '波动率类', type: 'danger', icon: Activity, color: '#f56c6c' },
  value: { label: '价值类', type: 'success', icon: Landmark, color: '#67c23a' },
  growth: { label: '成长类', type: 'primary', icon: Zap, color: '#409eff' },
  sentiment: { label: '情绪类', type: 'info', icon: Flame, color: '#909399' },
  custom: { label: '自定义类', type: 'info', icon: Settings, color: '#9b59b6' },
};

const theme = computed(() => categoryThemes[props.factor.category] || categoryThemes.custom);

const totalReturn = computed(() => {
  const last = props.factor.longReturns[props.factor.longReturns.length - 1]!;
  return ((last / 100) - 1) * 100;
});

const isPositive = computed(() => totalReturn.value >= 0);

// SVG sparkline points
const sparklinePoints = computed(() => {
  const data = props.factor.longReturns;
  const minVal = Math.min(...data);
  const maxVal = Math.max(...data);
  return data
    .map((val, idx) => {
      const x = (idx / (data.length - 1)) * 110 + 5;
      const y =
        maxVal === minVal
          ? 20
          : 35 - ((val - minVal) / (maxVal - minVal)) * 30 + 3;
      return `${x},${y}`;
    })
    .join(' ');
});

const frequencyLabel = computed(() => {
  const map: Record<string, string> = {
    Daily: '日频',
    Weekly: '周频',
    Monthly: '月频',
    Quarterly: '季频',
  };
  return map[props.factor.frequency] || props.factor.frequency;
});
</script>

<template>
  <ElCard
    shadow="hover"
    :class="[
      'factor-card group transition-all duration-300 hover:-translate-y-0.5',
      { 'factor-card--selected': isSelected },
    ]"
    :body-style="{ padding: '20px' }"
  >
    <!-- Header -->
    <div class="flex items-start justify-between gap-2 mb-3">
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2 flex-wrap mb-1.5">
          <ElTag :type="theme.type" size="small" effect="light" round>
            <component :is="theme.icon" class="w-3 h-3 mr-1 inline" />
            {{ theme.label }}
          </ElTag>
          <ElTag size="small" effect="plain" class="font-mono">
            {{ factor.code }}
          </ElTag>
          <ElTag
            v-if="factor.author === 'user'"
            size="small"
            type="success"
            effect="light"
          >
            研究员创建
          </ElTag>
        </div>
        <h4 class="text-sm font-semibold truncate group-hover:text-blue-500 transition-colors">
          {{ factor.name }}
        </h4>
      </div>

      <!-- Sparkline -->
      <div class="w-28 h-10 flex-shrink-0 cursor-pointer" @click="emit('select', factor)">
        <svg class="w-full h-full overflow-visible">
          <polyline
            fill="none"
            :stroke="isPositive ? '#10b981' : '#f43f5e'"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            :points="sparklinePoints"
          />
        </svg>
        <div
          class="text-[10px] text-right font-mono mt-0.5"
          :class="isPositive ? 'text-emerald-500' : 'text-rose-500'"
        >
          1年多头 {{ isPositive ? '+' : '' }}{{ totalReturn.toFixed(1) }}%
        </div>
      </div>
    </div>

    <!-- Formula -->
    <div class="my-3 py-2 px-3 rounded-lg bg-gray-50 border border-gray-100">
      <label class="text-[11px] text-gray-400 block mb-0.5">算法公式</label>
      <p class="text-xs font-mono text-gray-600 truncate" :title="factor.formula">
        {{ factor.formula }}
      </p>
    </div>

    <!-- Description -->
    <p class="text-xs text-gray-400 line-clamp-2 leading-relaxed mb-4 h-8">
      {{ factor.description }}
    </p>

    <!-- Metrics -->
    <div class="grid grid-cols-3 gap-1 border-t border-gray-100 pt-3">
      <div class="text-center">
        <div class="text-[10px] text-gray-400 mb-0.5">IC 均值</div>
        <div
          class="text-sm font-semibold font-mono"
          :class="factor.icMean >= 0 ? 'text-emerald-500' : 'text-rose-500'"
        >
          {{ factor.icMean >= 0 ? '+' : '' }}{{ factor.icMean.toFixed(3) }}
        </div>
      </div>
      <div class="text-center border-x border-gray-100">
        <div class="text-[10px] text-gray-400 mb-0.5">信息比率(IR)</div>
        <div
          class="text-sm font-semibold font-mono"
          :class="factor.ir >= 0.8 ? 'text-blue-500' : 'text-gray-700'"
        >
          {{ factor.ir.toFixed(2) }}
        </div>
      </div>
      <div class="text-center">
        <div class="text-[10px] text-gray-400 mb-0.5">夏普比率</div>
        <div class="text-sm font-semibold font-mono text-gray-700">
          {{ factor.sharpeRatio.toFixed(2) }}
        </div>
      </div>
    </div>

    <!-- Footer -->
    <div class="flex items-center justify-between mt-4 pt-3 border-t border-gray-100">
      <span class="text-[11px] text-gray-400">
        更新: <span class="font-semibold text-gray-600">{{ frequencyLabel }}</span>
      </span>
      <div class="flex items-center gap-1.5">
        <ElButton link type="primary" size="small" @click="emit('select', factor)">
          评测详情
        </ElButton>
        <ElButton link size="small" @click="emit('edit', factor)">
          <Settings class="w-4 h-4 text-gray-400" />
        </ElButton>
        <ElButton
          v-if="factor.author === 'user'"
          link
          size="small"
          @click="emit('delete', factor.id)"
        >
          <Trash2 class="w-4 h-4 text-gray-400 hover:text-rose-500" />
        </ElButton>
      </div>
    </div>
  </ElCard>
</template>

<style scoped>
.factor-card {
  border-radius: 16px;
}
.factor-card--selected {
  border-color: #409eff;
  box-shadow: 0 0 0 2px rgba(64, 158, 255, 0.1);
}
</style>
