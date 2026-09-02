<script setup lang="ts">
import { ref, computed } from 'vue';
import { Activity, Calendar, Sparkles } from '@lucide/vue';

export interface YieldPoint {
  date: string; // YYYY-MM-DD
  cumulativeReturn: number; // 百分比，如 12.34 表示 +12.34%
  totalAssets: number;
}

const props = defineProps<{
  points: YieldPoint[];
  initialCapital?: number;
  strategyName?: string;
}>();

const selectedPeriod = ref<'intraday' | 'week' | 'month' | 'quarter' | 'year' | 'all'>('all');
const hovered = ref<YieldPoint | null>(null);
const hoverX = ref(0);

const periodLabels: Record<string, string> = {
  intraday: '实时',
  week: '近1周',
  month: '近1月',
  quarter: '近3月',
  year: '近1年',
  all: '成立以来',
};

const currentPoints = computed<YieldPoint[]>(() => {
  const pts = props.points || [];
  if (!pts.length) return [];
  if (selectedPeriod.value === 'all') return pts;
  const maxDate = pts[pts.length - 1].date;
  if (selectedPeriod.value === 'intraday') return pts.filter((p) => p.date === maxDate);
  const days = { week: 7, month: 30, quarter: 90, year: 365 }[selectedPeriod.value];
  const cutoff = new Date(maxDate);
  cutoff.setDate(cutoff.getDate() - days);
  const cs = cutoff.toISOString().slice(0, 10);
  return pts.filter((p) => p.date >= cs);
});

// SVG 几何
const W = 920;
const H = 300;
const pad = { top: 24, right: 28, bottom: 36, left: 52 };
const iw = W - pad.left - pad.right;
const ih = H - pad.top - pad.bottom;

const minMax = computed(() => {
  const pts = currentPoints.value;
  if (!pts.length) return { min: -2, max: 2, count: 0 };
  let min = Infinity;
  let max = -Infinity;
  pts.forEach((p) => {
    if (p.cumulativeReturn < min) min = p.cumulativeReturn;
    if (p.cumulativeReturn > max) max = p.cumulativeReturn;
  });
  const range = Math.max(0.5, max - min);
  min = Math.floor((min - range * 0.15) * 10) / 10;
  max = Math.ceil((max + range * 0.15) * 10) / 10;
  return { min, max, count: pts.length };
});

const getY = (v: number) => {
  const { min, max } = minMax.value;
  if (max === min) return pad.top + ih / 2;
  return pad.top + ih - ((v - min) / (max - min)) * ih;
};
const getX = (i: number) => {
  const c = minMax.value.count;
  if (c <= 1) return pad.left;
  return pad.left + (i / (c - 1)) * iw;
};

const linePath = computed(() =>
  currentPoints.value.reduce((acc, p, i) => {
    const x = getX(i);
    const y = getY(p.cumulativeReturn);
    return `${acc} ${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`;
  }, ''),
);
const areaPath = computed(() => {
  const pts = currentPoints.value;
  if (!pts.length) return '';
  const first = getX(0);
  const last = getX(pts.length - 1);
  const bottom = pad.top + ih;
  return `${linePath.value} L ${last.toFixed(1)} ${bottom} L ${first.toFixed(1)} ${bottom} Z`;
});
const zeroY = computed(() => getY(0));

const yTicks = computed(() => {
  const { min, max } = minMax.value;
  const step = (max - min) / 4;
  return [0, 1, 2, 3, 4].map((i) => {
    const v = Number((min + step * i).toFixed(2));
    return { v, y: getY(v), label: `${v > 0 ? '+' : ''}${v.toFixed(1)}%` };
  });
});
const xTicks = computed(() => {
  const pts = currentPoints.value;
  if (!pts.length) return [];
  const tickCount = Math.min(6, pts.length);
  const step = Math.max(1, Math.floor((pts.length - 1) / (tickCount - 1 || 1)));
  const ticks: { label: string; x: number }[] = [];
  for (let i = 0; i < pts.length; i += step) {
    if (ticks.length < tickCount) ticks.push({ label: pts[i].date.slice(5), x: getX(i) });
  }
  if (ticks.length && ticks[ticks.length - 1].x < getX(pts.length - 1) - 30) {
    ticks.push({ label: pts[pts.length - 1].date.slice(5), x: getX(pts.length - 1) });
  }
  return ticks;
});

const latest = computed(() =>
  currentPoints.value.length ? currentPoints.value[currentPoints.value.length - 1] : null,
);
const navOf = (p: YieldPoint) =>
  props.initialCapital ? p.totalAssets / props.initialCapital : p.totalAssets;

const onMove = (e: MouseEvent) => {
  const svg = e.currentTarget as SVGSVGElement;
  const rect = svg.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const sx = (mx / rect.width) * W;
  const pts = currentPoints.value;
  if (!pts.length) return;
  let idx = 0;
  let md = Infinity;
  pts.forEach((p, i) => {
    const d = Math.abs(getX(i) - sx);
    if (d < md) {
      md = d;
      idx = i;
    }
  });
  hovered.value = pts[idx];
  hoverX.value = getX(idx);
};
const onLeave = () => {
  hovered.value = null;
};
</script>

<template>
  <div class="bg-white rounded-xl p-4 lg:p-5 border border-slate-200 shadow-sm flex flex-col justify-between">
    <!-- 标题 + 周期切换 -->
    <div class="flex flex-col md:flex-row md:items-center justify-between gap-3 pb-3 border-b border-slate-100">
      <div class="flex flex-wrap items-center gap-3">
        <div class="flex items-center gap-2">
          <div class="p-1.5 rounded-lg bg-indigo-50 text-indigo-600 border border-indigo-100">
            <Activity class="w-4 h-4" />
          </div>
          <div>
            <h2 class="font-bold text-sm lg:text-base text-slate-900 flex items-center gap-2">
              实时收益率曲线 (Yield Curve)
              <span
                v-if="selectedPeriod === 'intraday'"
                class="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-50 text-emerald-700 border border-emerald-100"
              >
                <span class="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse mr-1"></span>
                分时实时流
              </span>
            </h2>
            <p class="text-xs text-slate-500">
              策略: <span class="text-slate-800 font-medium">{{ props.strategyName || '全部量化组合' }}</span>
            </p>
          </div>
        </div>

        <div
          v-if="latest"
          class="hidden sm:flex items-center gap-3 ml-2 px-3 py-1 rounded-xl bg-slate-50 border border-slate-200 text-xs font-mono text-slate-700"
        >
          <div class="flex items-center gap-1.5">
            <span class="text-slate-500">净值:</span>
            <span class="font-bold text-slate-900">{{ navOf(latest).toFixed(4) }}</span>
          </div>
          <div class="w-px h-3 bg-slate-200"></div>
          <div class="flex items-center gap-1.5">
            <span class="text-slate-500">累计收益:</span>
            <span class="font-bold" :class="latest.cumulativeReturn >= 0 ? 'text-emerald-600' : 'text-rose-600'">
              {{ latest.cumulativeReturn >= 0 ? '+' : '' }}{{ latest.cumulativeReturn.toFixed(2) }}%
            </span>
          </div>
        </div>
      </div>

      <div class="flex items-center gap-0.5 bg-slate-100 p-1 rounded-xl border border-slate-200 text-xs self-start md:self-auto">
        <button
          v-for="(label, key) in periodLabels"
          :key="key"
          class="px-2.5 py-1 rounded-lg font-medium transition-all cursor-pointer text-xs"
          :class="selectedPeriod === key
            ? 'bg-white text-indigo-600 shadow-sm font-semibold'
            : 'text-slate-600 hover:text-slate-900 hover:bg-white/50'"
          @click="selectedPeriod = key as any"
        >
          {{ label }}
        </button>
      </div>
    </div>

    <!-- 曲线 -->
    <div class="relative w-full mt-3 select-none">
      <div class="flex items-center justify-end gap-4 text-xs mb-2">
        <div class="flex items-center gap-1.5">
          <div class="w-3 h-1 bg-indigo-600 rounded-full shadow-sm"></div>
          <span class="text-slate-800 font-semibold">组合累计收益率</span>
        </div>
      </div>

      <svg
        :viewBox="`0 0 ${W} ${H}`"
        class="w-full h-[260px] md:h-[300px] overflow-visible cursor-crosshair"
        @mousemove="onMove"
        @mouseleave="onLeave"
      >
        <defs>
          <linearGradient id="strategyGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#4f46e5" stop-opacity="0.18" />
            <stop offset="60%" stop-color="#4f46e5" stop-opacity="0.04" />
            <stop offset="100%" stop-color="#4f46e5" stop-opacity="0.0" />
          </linearGradient>
        </defs>

        <g class="grid-y">
          <template v-for="(tick, idx) in yTicks" :key="'ytick-' + idx">
            <line :x1="pad.left" :y1="tick.y" :x2="W - pad.right" :y2="tick.y" stroke="#f1f5f9" stroke-width="1" />
            <text :x="pad.left - 8" :y="tick.y + 4" text-anchor="end" class="text-[11px] font-mono fill-slate-400 font-medium">
              {{ tick.label }}
            </text>
          </template>
        </g>

        <line
          v-if="zeroY >= pad.top && zeroY <= pad.top + ih"
          :x1="pad.left" :y1="zeroY" :x2="W - pad.right" :y2="zeroY"
          stroke="#cbd5e1" stroke-width="1.2"
        />

        <g class="grid-x">
          <template v-for="(xtick, idx) in xTicks" :key="'xtick-' + idx">
            <line :x1="xtick.x" :y1="pad.top + ih" :x2="xtick.x" :y2="pad.top + ih + 5" stroke="#e2e8f0" stroke-width="1" />
            <text :x="xtick.x" :y="pad.top + ih + 20" text-anchor="middle" class="text-[10px] font-mono fill-slate-500 font-medium">
              {{ xtick.label }}
            </text>
          </template>
        </g>

        <path :d="areaPath" fill="url(#strategyGradient)" />

        <path
          :d="linePath" fill="none" stroke="#4f46e5" stroke-width="2.6"
          stroke-linecap="round" stroke-linejoin="round"
        />

        <template v-if="currentPoints.length > 0">
          <g :transform="`translate(${getX(currentPoints.length - 1)}, ${getY(currentPoints[currentPoints.length - 1].cumulativeReturn)})`">
            <circle r="6" fill="#4f46e5" opacity="0.25" class="animate-ping" />
            <circle r="4" fill="#4f46e5" stroke="#ffffff" stroke-width="1.5" />
          </g>
        </template>

        <template v-if="hovered">
          <line
            :x1="hoverX" :y1="pad.top" :x2="hoverX" :y2="pad.top + ih"
            stroke="#94a3b8" stroke-dasharray="3 3" stroke-width="1.2"
          />
          <circle :cx="hoverX" :cy="getY(hovered.cumulativeReturn)" r="5" fill="#4f46e5" stroke="#ffffff" stroke-width="2" />
        </template>
      </svg>

      <div
        v-if="hovered"
        class="absolute pointer-events-none z-20 bg-white/95 backdrop-blur-md rounded-xl p-3.5 border border-slate-200 shadow-xl text-xs font-mono text-slate-800"
        :style="{
          left: `${Math.min(W - 200, Math.max(20, (hoverX / W) * 100))}%`,
          top: '10px',
          transform: 'translateX(-50%)',
        }"
      >
        <div class="flex items-center justify-between gap-4 pb-1.5 border-b border-slate-100 text-slate-500">
          <span class="flex items-center gap-1 font-sans">
            <Calendar class="w-3.5 h-3.5 text-indigo-600" />
            {{ hovered.date }}
          </span>
          <span>净值: <strong class="text-slate-900">{{ navOf(hovered).toFixed(4) }}</strong></span>
        </div>
        <div class="mt-2 flex items-center justify-between gap-6">
          <span class="text-indigo-600 flex items-center gap-1 font-semibold">
            <Sparkles class="w-3 h-3" /> 累计收益:
          </span>
          <span class="font-bold" :class="hovered.cumulativeReturn >= 0 ? 'text-emerald-600' : 'text-rose-600'">
            {{ hovered.cumulativeReturn >= 0 ? '+' : '' }}{{ hovered.cumulativeReturn.toFixed(2) }}%
          </span>
        </div>
      </div>
    </div>
  </div>
</template>
