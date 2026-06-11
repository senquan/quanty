<script lang="ts" setup>
import { computed, onMounted, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  ElAlert,
  ElCard,
  ElCheckbox,
  ElCol,
  ElRow,
} from 'element-plus';

import type { Factor } from '../types';
import { calculateCorrelationMatrix } from '../mock/factor-data';

const props = defineProps<{
  factors: Factor[];
}>();

const selectedCodes = ref<string[]>([]);
const hoveredCell = ref<{ x: string; y: string; val: number } | null>(null);

const heatmapChartRef = ref();
const { renderEcharts: renderHeatmap } = useEcharts(heatmapChartRef);

// Initialize with first 6 factors
watch(
  () => props.factors,
  (factors) => {
    if (factors.length > 0 && selectedCodes.value.length === 0) {
      selectedCodes.value = factors.slice(0, 6).map((f) => f.code);
    }
  },
  { immediate: true },
);

const activeFactors = computed(() =>
  props.factors.filter((f) => selectedCodes.value.includes(f.code)),
);

const correlationMatrix = computed(() =>
  calculateCorrelationMatrix(activeFactors.value),
);

const highCollPairs = computed(() => {
  const pairs: { f1: string; f2: string; val: number }[] = [];
  const factors = activeFactors.value;
  for (let i = 0; i < factors.length; i++) {
    for (let j = i + 1; j < factors.length; j++) {
      const val =
        correlationMatrix.value[factors[i]!.code]?.[factors[j]!.code] || 0;
      if (Math.abs(val) > 0.6) pairs.push({ f1: factors[i]!.code, f2: factors[j]!.code, val });
    }
  }
  return pairs;
});

function handleToggle(code: string) {
  if (selectedCodes.value.includes(code)) {
    if (selectedCodes.value.length <= 2) return;
    selectedCodes.value = selectedCodes.value.filter((c) => c !== code);
  } else {
    selectedCodes.value = [...selectedCodes.value, code];
  }
}

function buildHeatmapOptions() {
  const factors = activeFactors.value;
  const codes = factors.map((f) => f.code);
  const data: [number, number, number][] = [];

  for (let i = 0; i < factors.length; i++) {
    for (let j = 0; j < factors.length; j++) {
      const val =
        correlationMatrix.value[factors[i]!.code]?.[factors[j]!.code] ?? 0;
      data.push([j, i, val]);
    }
  }

  return {
    tooltip: {
      position: 'top',
      formatter: (params: any) => {
        const [x, y, val] = params.data;
        return `${codes[y]} vs ${codes[x]}<br/>r = ${val.toFixed(3)}`;
      },
    },
    grid: {
      left: '15%',
      right: '10%',
      bottom: '15%',
      top: '5%',
    },
    xAxis: {
      type: 'category' as const,
      data: codes,
      splitArea: { show: true },
      axisLabel: { fontSize: 10, rotate: -15 },
    },
    yAxis: {
      type: 'category' as const,
      data: codes,
      splitArea: { show: true },
      axisLabel: { fontSize: 10 },
    },
    visualMap: {
      min: -1,
      max: 1,
      calculable: true,
      orient: 'horizontal',
      left: 'center',
      bottom: '0%',
      inRange: {
        color: ['#0d9488', '#f1f5f9', '#f59e0b'],
      },
      textStyle: { fontSize: 10 },
    },
    series: [
      {
        type: 'heatmap',
        data,
        label: {
          show: true,
          formatter: (p: any) => p.data[2].toFixed(2),
          fontSize: 10,
        },
        emphasis: {
          itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,0,0,0.3)' },
        },
      },
    ],
  };
}

function renderChart() {
  if (activeFactors.value.length < 2) return;
  renderHeatmap(buildHeatmapOptions() as any);

  // Attach hover listener for diagnostics panel
  const instance = (heatmapChartRef.value as any)?.chartInstance;
  if (instance) {
    instance.off('mouseover');
    instance.on('mouseover', (params: any) => {
      if (params.data) {
        const [xIdx, yIdx, val] = params.data;
        const codes = activeFactors.value.map((f) => f.name);
        hoveredCell.value = {
          x: codes[xIdx] || '',
          y: codes[yIdx] || '',
          val,
        };
      }
    });
    instance.on('mouseout', () => {
      hoveredCell.value = null;
    });
  }
}

onMounted(renderChart);
watch(selectedCodes, renderChart, { deep: true });
</script>

<template>
  <ElCard shadow="never" :body-style="{ padding: '20px' }">
    <!-- Header -->
    <div class="flex items-center justify-between mb-5 pb-4 border-b border-gray-100">
      <div>
        <span class="text-xs font-semibold text-amber-500 block">因子多重共线性排查</span>
        <h3 class="text-lg font-bold mt-1">Pearson 相关系数矩阵</h3>
        <p class="text-xs text-gray-400 mt-1">
          检测因子间的同源重叠，高相关意味着信息冗余，应避免在组合中同时暴露。
        </p>
      </div>

      <ElAlert
        v-if="highCollPairs.length > 0"
        :title="`发现 ${highCollPairs.length} 组高度冗余因子对 (|r| > 0.6)`"
        type="warning"
        :closable="false"
        show-icon
        class="w-auto"
      />
      <ElAlert
        v-else
        title="当前因子独立性良好"
        type="success"
        :closable="false"
        show-icon
        class="w-auto"
      />
    </div>

    <ElRow :gutter="16">
      <!-- Left: factor selection -->
      <ElCol :span="5">
        <span class="text-xs font-bold text-gray-400 block mb-3">参与评估因子 (最少2个)</span>
        <div class="border border-gray-100 rounded-xl max-h-80 overflow-y-auto p-3 space-y-2 bg-gray-50">
          <label
            v-for="f in factors"
            :key="f.id"
            class="flex items-center gap-2 px-2 py-1.5 rounded-lg border text-xs cursor-pointer transition-all"
            :class="
              selectedCodes.includes(f.code)
                ? 'bg-blue-50 border-blue-200 text-blue-700'
                : 'border-gray-100 text-gray-500 hover:border-gray-200'
            "
          >
            <ElCheckbox
              :model-value="selectedCodes.includes(f.code)"
              @change="handleToggle(f.code)"
            />
            <div class="truncate flex-1">
              <span class="block truncate text-xs">{{ f.name }}</span>
              <span class="text-[9px] text-gray-400 font-mono">{{ f.code }}</span>
            </div>
          </label>
        </div>
      </ElCol>

      <!-- Center: heatmap -->
      <ElCol :span="12">
        <EchartsUI ref="heatmapChartRef" height="420px" />
      </ElCol>

      <!-- Right: diagnostics -->
      <ElCol :span="7">
        <span class="text-xs font-bold text-gray-400 block mb-3">诊断性回馈</span>

        <div class="p-4 border border-gray-100 rounded-xl bg-gray-50 min-h-[200px]">
          <template v-if="hoveredCell">
            <span class="text-[10px] text-blue-500 font-bold block mb-1">相关系数</span>
            <div class="text-2xl font-bold font-mono mb-3">
              {{ hoveredCell.val >= 0 ? '+' : '' }}{{ hoveredCell.val.toFixed(3) }}
            </div>
            <div class="text-xs text-gray-600 mb-3">
              <p><strong>因子 A:</strong> {{ hoveredCell.y }}</p>
              <p><strong>因子 B:</strong> {{ hoveredCell.x }}</p>
            </div>
            <div class="p-2.5 rounded-lg bg-white border border-gray-100 text-[11px]">
              <span
                v-if="Math.abs(hoveredCell.val) > 0.65"
                class="text-rose-500 font-semibold"
              >
                已发现明显的多重共线性，合成模型时必须二选一。
              </span>
              <span
                v-else-if="Math.abs(hoveredCell.val) > 0.3"
                class="text-amber-500 font-semibold"
              >
                中度交叉，建议降低其中一方权重。
              </span>
              <span v-else class="text-emerald-500 font-semibold">
                独立性好，强烈推荐组合使用。
              </span>
            </div>
          </template>

          <div v-else class="flex flex-col items-center justify-center py-8 text-gray-400 text-xs text-center">
            将鼠标悬停在矩阵格子上，即可获得因子配对的共线性分析意见。
          </div>
        </div>

        <div class="mt-4 p-3 rounded-xl bg-blue-50 border border-blue-100 text-xs text-blue-600 leading-relaxed">
          <strong>双向因子中性化:</strong> 若两因子相关性过高，通常因共同暴露于市值因子，通过中性化可恢复独立性。
        </div>
      </ElCol>
    </ElRow>
  </ElCard>
</template>
