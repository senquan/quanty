<script lang="ts" setup>
import { computed, ref } from 'vue';

import { Filter, Layers, Plus, Search, TrendingUp, Wifi } from '@lucide/vue';
import {
  ElButton,
  ElCard,
  ElCol,
  ElEmpty,
  ElInput,
  ElOption,
  ElRadioButton,
  ElRadioGroup,
  ElRow,
  ElSelect,
} from 'element-plus';

import type { Factor, FactorCategory, UpdateFrequency } from '../types';
import FactorCard from './factor-card.vue';

const props = defineProps<{
  factors: Factor[];
  selectedFactorId?: string;
}>();

const emit = defineEmits<{
  'select-factor': [factor: Factor];
  'edit-factor': [factor: Factor];
  'delete-factor': [id: string];
  'add-factor': [];
  'ai-generate': [category: FactorCategory];
}>();

const searchQuery = ref('');
const selectedCategory = ref<FactorCategory | 'all'>('all');
const selectedFreq = ref<UpdateFrequency | 'all'>('all');
const selectedAuthor = ref<'all' | 'system' | 'user'>('all');

const filteredFactors = computed(() =>
  props.factors.filter((f) => {
    const matchesSearch =
      !searchQuery.value ||
      f.name.toLowerCase().includes(searchQuery.value.toLowerCase()) ||
      f.code.toLowerCase().includes(searchQuery.value.toLowerCase()) ||
      f.formula.toLowerCase().includes(searchQuery.value.toLowerCase());
    const matchesCategory =
      selectedCategory.value === 'all' || f.category === selectedCategory.value;
    const matchesAuthor =
      selectedAuthor.value === 'all' || f.author === selectedAuthor.value;
    const matchesFreq =
      selectedFreq.value === 'all' || f.frequency === selectedFreq.value;
    return matchesSearch && matchesCategory && matchesAuthor && matchesFreq;
  }),
);

const totalCount = computed(() => props.factors.length);
const userCount = computed(() => props.factors.filter((f) => f.author === 'user').length);
const systemCount = computed(() => props.factors.filter((f) => f.author === 'system').length);
const meanIC = computed(() => {
  if (totalCount.value === 0) return 0;
  return (
    props.factors.reduce((sum, f) => sum + Math.abs(f.icMean), 0) /
    totalCount.value
  );
});
const topFactor = computed(() =>
  props.factors.reduce<Factor | undefined>((prev, curr) => (!prev || curr.ir > prev.ir ? curr : prev), undefined),
);

const categoryOptions = [
  { key: 'all', label: '全部' },
  { key: 'momentum', label: '动量趋势' },
  { key: 'volatility', label: '价格波动' },
  { key: 'value', label: '价值分红' },
  { key: 'growth', label: '财务成长' },
  { key: 'sentiment', label: '情绪资金' },
];

const aiTemplates: { label: string; category: FactorCategory }[] = [
  { label: '通道非线性动量', category: 'momentum' },
  { label: '逆向偏度波动', category: 'volatility' },
  { label: '账面资本再溢价', category: 'value' },
  { label: '大单资金换手', category: 'sentiment' },
];
</script>

<template>
  <div class="space-y-5">
    <!-- Stats Banner -->
    <ElRow :gutter="16">
      <ElCol :span="6">
        <ElCard shadow="hover" :body-style="{ padding: '16px' }">
          <div class="flex items-center gap-3">
            <div class="p-3 rounded-xl bg-blue-50 text-blue-500">
              <Layers class="w-5 h-5" />
            </div>
            <div>
              <span class="text-[11px] text-gray-400 font-bold block">在线因子数</span>
              <div class="text-xl font-bold font-mono mt-0.5">{{ totalCount }} 个</div>
              <span class="text-[9px] text-gray-400">系统 {{ systemCount }} | 自研 {{ userCount }}</span>
            </div>
          </div>
        </ElCard>
      </ElCol>
      <ElCol :span="6">
        <ElCard shadow="hover" :body-style="{ padding: '16px' }">
          <div class="flex items-center gap-3">
            <div class="p-3 rounded-xl bg-amber-50 text-amber-500">
              <TrendingUp class="w-5 h-5" />
            </div>
            <div>
              <span class="text-[11px] text-gray-400 font-bold block">平均信息系数 IC</span>
              <div class="text-xl font-bold font-mono mt-0.5">
                {{ (meanIC * 100).toFixed(2) }}%
              </div>
              <span class="text-[9px] text-emerald-500 font-semibold">健康稳健状态</span>
            </div>
          </div>
        </ElCard>
      </ElCol>
      <ElCol :span="6">
        <ElCard shadow="hover" :body-style="{ padding: '16px' }">
          <div class="flex items-center gap-3">
            <div class="p-3 rounded-xl bg-indigo-50 text-indigo-500">
              <TrendingUp class="w-5 h-5" />
            </div>
            <div>
              <span class="text-[11px] text-gray-400 font-bold block">最优单体因子</span>
              <div class="text-sm font-bold font-mono mt-0.5 truncate max-w-[130px]">
                {{ topFactor?.name || '无' }}
              </div>
              <span class="text-[9px] text-indigo-500 font-mono">
                IR: {{ topFactor?.ir.toFixed(2) }}
              </span>
            </div>
          </div>
        </ElCard>
      </ElCol>
      <ElCol :span="6">
        <ElCard shadow="hover" :body-style="{ padding: '16px' }">
          <div class="flex items-center gap-3">
            <div class="p-3 rounded-xl bg-emerald-50 text-emerald-500">
              <Wifi class="w-5 h-5" />
            </div>
            <div>
              <span class="text-[11px] text-gray-400 font-bold block">实时清洗服务</span>
              <div class="text-sm font-bold mt-0.5 flex items-center gap-1">
                上海 A机组
                <span class="w-2 h-2 rounded-full bg-emerald-500 inline-block animate-pulse" />
              </div>
              <span class="text-[9px] text-gray-400">每日收盘定点刷算</span>
            </div>
          </div>
        </ElCard>
      </ElCol>
    </ElRow>

    <!-- Search & Filter Panel -->
    <ElCard shadow="never" :body-style="{ padding: '20px' }">
      <div class="flex items-center gap-4 mb-4">
        <ElInput
          v-model="searchQuery"
          placeholder="输入因子名称、代码或公式检索..."
          clearable
          class="flex-1"
          :prefix-icon="Search"
        />
        <ElButton type="primary" @click="emit('add-factor')">
          <Plus class="w-4 h-4 mr-1" />
          构建新量化因子
        </ElButton>
      </div>

      <div class="flex flex-wrap items-center gap-4 pt-3 border-t border-gray-100 text-xs">
        <div class="flex items-center gap-2">
          <Filter class="w-3.5 h-3.5 text-gray-400" />
          <span class="font-bold text-gray-400">因子大类:</span>
          <ElRadioGroup v-model="selectedCategory" size="small">
            <ElRadioButton
              v-for="opt in categoryOptions"
              :key="opt.key"
              :value="opt.key"
            >
              {{ opt.label }}
            </ElRadioButton>
          </ElRadioGroup>
        </div>

        <ElSelect
          v-model="selectedFreq"
          placeholder="更新周期"
          size="small"
          style="width: 130px"
        >
          <ElOption label="所有周期" value="all" />
          <ElOption label="日频更新" value="Daily" />
          <ElOption label="周频定投" value="Weekly" />
          <ElOption label="月结算" value="Monthly" />
          <ElOption label="季评定" value="Quarterly" />
        </ElSelect>

        <ElSelect
          v-model="selectedAuthor"
          placeholder="作者来源"
          size="small"
          style="width: 140px"
        >
          <ElOption label="所有作者" value="all" />
          <ElOption label="系统底册" value="system" />
          <ElOption label="研究员自研" value="user" />
        </ElSelect>
      </div>
    </ElCard>

    <!-- AI Generation Bar -->
    <div class="p-4 rounded-2xl bg-gradient-to-r from-blue-50 to-indigo-50 border border-blue-100">
      <div class="flex items-center justify-between gap-4">
        <div>
          <span class="text-[11px] text-blue-500 font-bold block">AI 因子量化灵感</span>
          <p class="text-xs text-gray-500 mt-1">
            点击一键调用 AI 创意助手，智能合成包含高信息优势表达式的新型因子：
          </p>
        </div>
        <div class="flex gap-2 flex-shrink-0">
          <ElButton
            v-for="t in aiTemplates"
            :key="t.category"
            size="small"
            @click="emit('ai-generate', t.category)"
          >
            {{ t.label }}
          </ElButton>
        </div>
      </div>
    </div>

    <!-- Card Grid -->
    <div
      v-if="filteredFactors.length > 0"
      class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5"
    >
      <FactorCard
        v-for="f in filteredFactors"
        :key="f.id"
        :factor="f"
        :is-selected="f.id === selectedFactorId"
        @select="emit('select-factor', $event)"
        @edit="emit('edit-factor', $event)"
        @delete="emit('delete-factor', $event)"
      />
    </div>

    <ElEmpty v-else description="无匹配因子，请调整筛选条件" />
  </div>
</template>
