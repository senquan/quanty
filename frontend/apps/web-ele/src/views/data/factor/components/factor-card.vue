<script lang="ts" setup>
import type { Factor, FactorCategory } from '../types';

import { computed } from 'vue';

import {
  Activity,
  Flame,
  Landmark,
  Scale,
  Settings,
  Trash2,
  TrendingUp,
  Zap,
} from '@lucide/vue';
import { ElButton, ElCard, ElRate, ElTag } from 'element-plus';

import {
  gradeColors,
  gradeFromScore,
  overallScore,
} from '../grading';

const props = withDefaults(
  defineProps<{
    factor: Factor;
    isSelected?: boolean;
  }>(),
  { isSelected: false },
);

const emit = defineEmits<{
  delete: [id: string];
  edit: [factor: Factor];
  select: [factor: Factor];
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
  size: { label: '规模类', type: 'success', icon: Scale, color: '#67c23a' },
  sentiment: { label: '情绪类', type: 'info', icon: Flame, color: '#909399' },
  technical: { label: '技术类', type: 'primary', icon: Activity, color: '#36cfc9' },
  custom: { label: '自定义类', type: 'info', icon: Settings, color: '#9b59b6' },
};

const theme = computed(() => categoryThemes[props.factor.category] || categoryThemes.custom);

/** 是否具备净值序列（真实接口暂不提供时为 false） */
const hasSeries = computed(() => (props.factor.longReturns?.length ?? 0) > 1);

const totalReturn = computed(() => {
  const data = props.factor.longReturns ?? [];
  if (data.length === 0) return 0;
  const last = data[data.length - 1]!;
  return (last / 100 - 1) * 100;
});

const isPositive = computed(() => totalReturn.value >= 0);

// SVG sparkline points
const sparklinePoints = computed(() => {
  const data = props.factor.longReturns ?? [];
  if (data.length < 2) return '';
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

// ============ 效能分级（IC / IR / 夏普） ============
function toGrade(score: number) {
  if (!score || score <= 0) return null;
  return gradeFromScore(score);
}

/** ElRate show-text 文案：数组下标 = 星数 - 1（1星=无效 … 5星=优秀） */
const gradeTexts = ['无效', '弱效', '合格', '良好', '优秀'];

/** ElRate 配色：低分(1~2)灰 → 中等(3~4)蓝 → 高分(5)金 */
const rateColors = ['#9ca3af', '#2563eb', '#b8860b'];

interface MetricGrade {
  key: string;
  title: string;
  display: string;
  valueClass: string;
  grade: ReturnType<typeof toGrade>;
}

const metricGrades = computed<MetricGrade[]>(() => {
  const f = props.factor;
  const items: Omit<MetricGrade, 'grade'>[] = [
    {
      key: 'ic',
      title: 'IC 均值',
      display: `${f.icMean >= 0 ? '+' : ''}${f.icMean.toFixed(3)}`,
      valueClass: f.icMean >= 0 ? 'text-emerald-500' : 'text-rose-500',
    },
    {
      key: 'ir',
      title: '信息比率(IR)',
      display: f.ir.toFixed(2),
      valueClass: f.ir >= 0.8 ? 'text-blue-500' : 'text-gray-700',
    },
    {
      key: 'sharpe',
      title: '夏普比率',
      display: f.sharpeRatio.toFixed(2),
      valueClass: 'text-gray-700',
    },
  ];
  const scores = [f.icRank, f.irRank, f.sharpeRank];
  return items.map((it, i) => ({ ...it, grade: toGrade(scores[i] ?? 0) }));
});

/** 综合评级：三项分值均值 */
const overall = computed(() => {
  const score = overallScore([
    props.factor.icRank,
    props.factor.irRank,
    props.factor.sharpeRank,
  ]);
  if (!score) return null;
  const g = gradeFromScore(score);
  return g ? { ...g, ...gradeColors(g.level) } : null;
});
</script>

<template>
  <ElCard
    shadow="hover"
    class="factor-card group transition-all duration-300 hover:-translate-y-0.5" :class="[
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
          <span
            v-if="overall"
            class="inline-flex items-center gap-0.5 rounded px-1.5 py-[1px] text-[10px] font-bold leading-4"
            :style="{ color: overall.color, backgroundColor: overall.bg }"
            :title="`综合评级 ${overall.level} 级（${overall.label}），由 IC / IR / 夏普三项分级取均值得到`"
          >
            综合 {{ overall.level }}
          </span>
          <span
            v-else
            class="inline-flex items-center rounded bg-gray-50 px-1.5 py-[1px] text-[10px] leading-4 text-gray-300"
            title="该因子尚未完成效能评估"
          >
            未评估
          </span>
        </div>
        <h4 class="text-sm font-semibold truncate group-hover:text-blue-500 transition-colors">
          {{ factor.name }}
        </h4>
      </div>

      <!-- Sparkline（无净值序列时展示 IC 摘要） -->
      <div class="w-28 h-10 flex-shrink-0 cursor-pointer" @click="emit('select', factor)">
        <svg v-if="hasSeries" class="w-full h-full overflow-visible">
          <polyline
            fill="none"
            :stroke="isPositive ? '#10b981' : '#f43f5e'"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            :points="sparklinePoints"
          />
        </svg>
        <div v-else class="w-full h-full flex items-center justify-end">
          <span class="text-[10px] text-gray-400">暂无净值序列</span>
        </div>
        <div
          v-if="hasSeries"
          class="text-[11px] text-right font-mono mt-0.5"
          :class="isPositive ? 'text-emerald-500' : 'text-rose-500'"
        >
          1年多头 {{ isPositive ? '+' : '' }}{{ totalReturn.toFixed(1) }}%
        </div>
        <div v-else class="text-[11px] text-right font-mono mt-0.5 text-gray-400">
          IC {{ factor.icMean ? factor.icMean.toFixed(3) : '未评估' }}
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
    <p
      class="text-xs text-gray-400 line-clamp-2 leading-relaxed mb-4 h-8 cursor-help"
      :title="factor.description"
    >
      {{ factor.description || '暂无说明' }}
    </p>

    <!-- Metrics（含 IC / IR / 夏普分级） -->
    <div class="grid grid-cols-3 gap-1 border-t border-gray-100 pt-3">
      <div v-for="m in metricGrades" :key="m.key" class="text-center">
        <div class="text-[11px] text-gray-400 mb-0.5">{{ m.title }}</div>
        <div class="text-sm font-semibold font-mono" :class="m.valueClass">
          {{ m.display }}
        </div>
        <div class="mt-1 flex items-center justify-center">
          <ElRate
            v-if="m.grade"
            class="grade-rate"
            :model-value="m.grade.score"
            :colors="rateColors"
            :texts="gradeTexts"
            disabled
            :title="`${m.grade.level} 级 (${m.grade.label})`"
          />
          <span v-else class="text-[10px] text-gray-300">未评估</span>
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

/* 只读评分区：缩小图标与文字，适配三列指标布局 */
.grade-rate {
  height: auto;
  line-height: 1;
}
.grade-rate :deep(.el-rate__icon) {
  font-size: 13px;
  margin-right: 1px;
}
.grade-rate :deep(.el-rate__text) {
  font-size: 10px;
  margin-left: 4px;
}
</style>
