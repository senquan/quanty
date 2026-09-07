<script lang="ts" setup>
import { computed, nextTick, onMounted, reactive, ref } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Download, Play } from '@lucide/vue';
import {
  ElAlert,
  ElButton,
  ElCard,
  ElCheckbox,
  ElCol,
  ElDatePicker,
  ElEmpty,
  ElInput,
  ElInputNumber,
  ElOption,
  ElRow,
  ElSelect,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import {
  BACKTEST_STYLES,
  PRICE_FIELDS,
  getBacktestHistoryApi,
  getBacktestStylesApi,
  getStrategiesApi,
  parseBacktestRefusal,
  runBacktestApi,
  type BacktestHistoryItem,
  type BacktestRefusal,
  type BacktestResult,
  type BacktestStyle,
  type BacktestStyleOption,
  type PriceField,
  type PriceFieldOption,
  type Strategy,
} from '#/api/quant';

// ============ ECharts ============
const equityChartRef = ref();
const drawdownChartRef = ref();
const { renderEcharts: renderEquity } = useEcharts(equityChartRef);
const { renderEcharts: renderDrawdown } = useEcharts(drawdownChartRef);

// ============ 回测参数 ============
function iso(d: Date) {
  return d.toISOString().slice(0, 10);
}

const defaultStart = new Date();
defaultStart.setFullYear(defaultStart.getFullYear() - 3);

const form = reactive({
  strategyId: undefined as number | undefined,
  // dc 只有 A 股行情（factor.raw_bars），默认值就该是个真跑得起来的 A 股代码
  symbol: '600519.SH',
  price_field: 'qfq' as PriceField,
  dateRange: [iso(defaultStart), iso(new Date())] as [string, string],
  initial_capital: 100_000,
  style: 'swing' as BacktestStyle,
  allow_short: false,
  apply_market_rules: true,
});

const strategies = ref<Strategy[]>([]);
const running = ref(false);
const result = ref<BacktestResult | null>(null);
const refusal = ref<BacktestRefusal | null>(null);

// 口径 / 复权口径表：拉得到就认服务端那份（闸口的事实来源在 dc 的 gate.py），
// 拉不到退回静态兜底，但要把「这不是服务端那份」说出来。
const styleOptions = ref<BacktestStyleOption[]>(
  BACKTEST_STYLES.map((s) => ({
    key: s.key,
    label: s.label,
    holding: s.holding,
    min_bars: s.minBars,
    why_min: s.whyMin,
  })),
);
const priceFieldOptions = ref<PriceFieldOption[]>(
  PRICE_FIELDS.map((p) => ({ key: p.key, note: p.note })),
);
const symbolHints = ref('A股 600519.SH / 000001.SZ / 920808.BJ（纯数字 600519 也能认）');
const stylesFallback = ref(false);

/** 口径说明只做说明，不做判断 —— 判断是后端闸口的事，前端不复制一份规则 */
const styleHint = computed(() => {
  const s = styleOptions.value.find((o) => o.key === form.style);
  if (!s) return '';
  const need = s.min_bars > 0 ? `至少约 ${s.min_bars} 根日线。` : '';
  return `${s.holding}。${need}${s.why_min ?? ''}`;
});

const priceFieldNote = computed(
  () => priceFieldOptions.value.find((p) => p.key === form.price_field)?.note ?? '',
);

async function loadStyleCatalog() {
  const catalog = await getBacktestStylesApi();
  if (!catalog) {
    stylesFallback.value = true;
    return;
  }
  if (catalog.styles?.length) styleOptions.value = catalog.styles;
  if (catalog.price_fields?.length) priceFieldOptions.value = catalog.price_fields;
  if (catalog.symbol_hints) symbolHints.value = catalog.symbol_hints;
}

async function loadStrategies() {
  try {
    strategies.value = await getStrategiesApi({ limit: 100 });
  } catch {
    strategies.value = [];
  }
}

// ============ 运行回测 ============
async function handleRunBacktest() {
  if (!form.strategyId) return;

  running.value = true;
  refusal.value = null;
  result.value = null;
  try {
    const data = await runBacktestApi({
      strategy_id: form.strategyId,
      symbol: form.symbol,
      start_date: form.dateRange[0],
      end_date: form.dateRange[1],
      initial_capital: form.initial_capital,
      style: form.style,
      allow_short: form.allow_short,
      apply_market_rules: form.apply_market_rules,
      price_field: form.price_field,
    });
    result.value = data;
    // 图表在 v-else 里，此时还没挂载 —— 不等 nextTick 的话 chartRef 为空，曲线画不出来
    await nextTick();
    renderResult(data);
    await loadHistory(form.strategyId);
  } catch (error: any) {
    // 闸口不放行：后端 422，detail 里是 {reason, remedy}
    const parsed = parseBacktestRefusal(error);
    if (parsed) {
      refusal.value = parsed;
    } else {
      refusal.value = {
        reason: '回测失败',
        remedy: error?.message ?? '请检查参数与数据源后重试',
      };
    }
  } finally {
    running.value = false;
  }
}

// ============ 图表 ============
function renderResult(data: BacktestResult) {
  const dates = data.portfolio_dates ?? [];
  const values = data.portfolio_values ?? [];

  renderEquity({
    tooltip: { trigger: 'axis' },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: { type: 'category', data: dates, boundaryGap: false },
    yAxis: { type: 'value', name: '组合价值 (¥)' },
    series: [
      {
        name: '组合价值',
        type: 'line',
        data: values,
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 2 },
        areaStyle: { opacity: 0.1 },
      },
    ],
  });

  let peak = values[0] ?? 0;
  const drawdown = values.map((v) => {
    peak = Math.max(peak, v);
    return peak > 0 ? Number((((v - peak) / peak) * 100).toFixed(2)) : 0;
  });

  renderDrawdown({
    tooltip: { trigger: 'axis', formatter: '{b}<br/>回撤: {c}%' },
    grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
    xAxis: { type: 'category', data: dates, boundaryGap: false },
    yAxis: { type: 'value', name: '回撤 (%)', max: 0 },
    series: [
      {
        name: '回撤',
        type: 'line',
        data: drawdown,
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 2, color: '#f56c6c' },
        areaStyle: { color: 'rgba(245,108,108,0.2)' },
      },
    ],
  });
}

// ============ 历史记录 ============
const history = ref<BacktestHistoryItem[]>([]);

async function loadHistory(strategyId: number) {
  try {
    history.value = await getBacktestHistoryApi(strategyId);
  } catch {
    history.value = [];
  }
}

/**
 * 导出历史记录为 JSON。
 * 后端只存了指标（backtest_results 表不含逐笔成交与净值），
 * 所以这里导出的就是这一行的指标，不假装能还原完整回测。
 */
function handleExport(row: BacktestHistoryItem) {
  const blob = new Blob([JSON.stringify(row, null, 2)], {
    type: 'application/json',
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `backtest-${row.id}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function fmtDate(s?: string) {
  return s ? String(s).slice(0, 10) : '-';
}

const rejectionSummary = computed(() => {
  const list = result.value?.rejections ?? [];
  const byReason = new Map<string, number>();
  for (const r of list) {
    byReason.set(r.reason, (byReason.get(r.reason) ?? 0) + 1);
  }
  return [...byReason.entries()].map(([reason, count]) => ({ reason, count }));
});

/**
 * 数据侧元信息 —— 「-15.41%」这个数字本身没有语境。
 * 实际吃进去多少根 bar、用的哪条价格序列、区间被截到哪天，必须跟数字一起看。
 */
const dataMetaLine = computed(() => {
  const m = result.value?.data;
  if (!m) return '';
  const parts = [`${m.bars} 根日线（${m.first_date} ~ ${m.last_date}）`];
  const pf = String(m.price_field ?? '').toUpperCase();
  if (pf) parts.push(`复权口径 ${pf}`);
  if (pf === 'HFQ' && m.hfq_factor) {
    parts.push(`名义价 ×${Number(m.hfq_factor).toFixed(4)}`);
  }
  return parts.join(' · ');
});

function fmtMoney(v?: number) {
  return typeof v === 'number' ? v.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) : '-';
}

onMounted(() => {
  loadStrategies();
  loadStyleCatalog();
});
</script>

<template>
  <div class="backtest-page p-4">
    <!-- 闸口拒绝：必须给出路，只有报错没有补救等于把人堵死 -->
    <ElAlert
      v-if="refusal"
      class="mb-4"
      type="error"
      show-icon
      :closable="false"
      title="这个回测不成立，已被闸口拦下"
    >
      <p class="mb-1">{{ refusal.reason }}</p>
      <p class="text-gray-500">怎么办：{{ refusal.remedy }}</p>
    </ElAlert>

    <!-- 口径表没拉到：下拉还在、能跑，但用的不是服务端那份，得说清楚 -->
    <ElAlert
      v-if="stylesFallback"
      class="mb-4"
      type="info"
      show-icon
      :closable="false"
      title="口径表用的是前端兜底版本"
    >
      没能从服务端拉到口径与复权口径表，当前用的是内置副本。
      能跑，但如果与闸口的实际要求对不上，以闸口拒绝时给出的提示为准。
    </ElAlert>

    <!-- 参数栏 -->
    <ElCard shadow="never" class="mb-4">
      <ElRow :gutter="12" align="middle" class="mb-3">
        <ElCol :span="6">
          <div class="mb-1 text-xs text-gray-400">策略</div>
          <ElSelect
            v-model="form.strategyId"
            placeholder="选择策略"
            clearable
            class="w-full"
          >
            <ElOption
              v-for="s in strategies"
              :key="s.id"
              :label="s.name"
              :value="s.id"
            />
          </ElSelect>
        </ElCol>
        <ElCol :span="5">
          <div class="mb-1 text-xs text-gray-400">A 股代码</div>
          <ElInput
            v-model="form.symbol"
            :placeholder="symbolHints"
            :title="symbolHints"
          />
        </ElCol>
        <ElCol :span="4">
          <div class="mb-1 text-xs text-gray-400">回测口径</div>
          <ElSelect v-model="form.style" class="w-full">
            <ElOption
              v-for="s in styleOptions"
              :key="s.key"
              :label="s.label"
              :value="s.key"
            />
          </ElSelect>
        </ElCol>
        <ElCol :span="4">
          <div class="mb-1 text-xs text-gray-400">
            复权口径
            <span class="ml-1 cursor-help text-gray-300" :title="priceFieldNote">
              ⓘ
            </span>
          </div>
          <ElSelect v-model="form.price_field" class="w-full">
            <ElOption
              v-for="p in priceFieldOptions"
              :key="p.key"
              :label="p.key.toUpperCase()"
              :value="p.key"
            />
          </ElSelect>
        </ElCol>
        <ElCol :span="5">
          <div class="mb-1 text-xs text-gray-400">回测区间</div>
          <ElDatePicker
            v-model="form.dateRange"
            type="daterange"
            value-format="YYYY-MM-DD"
            start-placeholder="开始"
            end-placeholder="结束"
            class="w-full"
          />
        </ElCol>
      </ElRow>

      <ElRow :gutter="12" align="middle">
        <ElCol :span="4">
          <div class="mb-1 text-xs text-gray-400">初始资金</div>
          <ElInputNumber
            v-model="form.initial_capital"
            :min="1000"
            :step="10000"
            class="w-full"
          />
        </ElCol>
        <ElCol :span="10">
          <div class="mb-1 text-xs text-gray-400">口径说明</div>
          <div
            class="text-xs leading-6"
            :class="form.style === 'intraday' ? 'text-orange-500' : 'text-gray-500'"
          >
            {{ styleHint }}
          </div>
        </ElCol>
        <ElCol :span="6">
          <div class="mb-1 text-xs text-gray-400">规则</div>
          <ElCheckbox v-model="form.allow_short">允许做空</ElCheckbox>
          <ElCheckbox v-model="form.apply_market_rules">
            套用市场规则
          </ElCheckbox>
        </ElCol>
        <ElCol :span="4" class="text-right">
          <div class="mb-1 text-xs text-gray-400">&nbsp;</div>
          <ElButton
            type="primary"
            :loading="running"
            :disabled="!form.strategyId"
            @click="handleRunBacktest"
          >
            <Play class="mr-1 h-4 w-4" />运行回测
          </ElButton>
        </ElCol>
      </ElRow>
    </ElCard>

    <ElEmpty v-if="!result" description="选择策略后运行回测" />

    <template v-else>
      <!-- 限制说明与警告：跟数字一起看，分开看等于没看 -->
      <ElCard v-if="result.limits?.length" shadow="never" class="mb-4">
        <template #header>
          <span>⚖️ 本次回测的限制（闸口判定：{{ result.plan?.market_label }} /
            {{ result.plan?.style }}）</span>
        </template>
        <ul class="m-0 pl-4 text-xs leading-6 text-gray-600">
          <li v-for="(l, i) in result.limits" :key="`limit-${i}`">{{ l }}</li>
        </ul>
      </ElCard>

      <ElAlert
        v-if="result.warnings?.length"
        class="mb-4"
        type="warning"
        show-icon
        :closable="false"
        title="结果的前提假设"
      >
        <ul class="m-0 pl-4">
          <li v-for="(w, i) in result.warnings" :key="`warn-${i}`">{{ w }}</li>
        </ul>
      </ElAlert>

      <ElAlert
        v-if="rejectionSummary.length"
        class="mb-4"
        type="warning"
        show-icon
        :closable="false"
        title="被市场规则挡下的信号（未成交）"
      >
        <ul class="m-0 pl-4">
          <li v-for="r in rejectionSummary" :key="r.reason">
            {{ r.reason }} × {{ r.count }}
          </li>
        </ul>
      </ElAlert>

      <!-- 数字本身没有语境：实际吃进去多少根 bar、用的哪条价格序列，一起给 -->
      <ElAlert
        v-if="dataMetaLine"
        class="mb-4"
        type="info"
        show-icon
        :closable="false"
        :title="dataMetaLine"
      />

      <!-- 指标卡 -->
      <ElCard shadow="never" class="mb-4" header="📋 核心指标">
        <ElRow :gutter="12">
          <ElCol :span="4">
            <div class="text-xs text-gray-400">总收益率</div>
            <div
              class="text-lg font-bold"
              :class="result.total_return >= 0 ? 'text-red-500' : 'text-green-500'"
            >
              {{ result.total_return >= 0 ? '+' : '' }}{{ result.total_return }}%
            </div>
          </ElCol>
          <ElCol :span="4">
            <div class="text-xs text-gray-400">夏普比率</div>
            <div class="text-lg font-bold">{{ result.sharpe_ratio }}</div>
          </ElCol>
          <ElCol :span="4">
            <div class="text-xs text-gray-400">最大回撤</div>
            <div class="text-lg font-bold text-green-500">
              -{{ result.max_drawdown }}%
            </div>
          </ElCol>
          <ElCol :span="4">
            <div class="text-xs text-gray-400">胜率</div>
            <div class="text-lg font-bold">{{ result.win_rate }}%</div>
          </ElCol>
          <ElCol :span="4">
            <div class="text-xs text-gray-400">交易笔数</div>
            <div class="text-lg font-bold">{{ result.total_trades }}</div>
          </ElCol>
          <ElCol :span="4">
            <div class="text-xs text-gray-400">期末价值</div>
            <div class="text-lg font-bold">
              ¥{{ fmtMoney(result.final_capital) }}
            </div>
          </ElCol>
        </ElRow>
        <ElRow :gutter="12" class="mt-3 border-t pt-3">
          <ElCol :span="4">
            <div class="text-xs text-gray-400">累计费用</div>
            <div class="text-sm">¥{{ fmtMoney(result.total_fees) }}</div>
          </ElCol>
          <ElCol :span="4">
            <div class="text-xs text-gray-400">期末持仓</div>
            <div class="text-sm">{{ result.final_position ?? 0 }} 股</div>
          </ElCol>
          <ElCol :span="4">
            <div class="text-xs text-gray-400">可用现金</div>
            <div class="text-sm">¥{{ fmtMoney(result.cash) }}</div>
          </ElCol>
          <ElCol :span="12">
            <div class="text-xs text-gray-400">说明</div>
            <div class="text-xs leading-5 text-gray-500">
              期末价值 = 现金 + 持仓市值；可用现金是不含持仓的那部分。
            </div>
          </ElCol>
        </ElRow>
      </ElCard>

      <!-- 图表 -->
      <ElRow :gutter="16" class="mb-4">
        <ElCol :span="14">
          <ElCard shadow="never" header="📈 组合价值曲线">
            <EchartsUI ref="equityChartRef" height="300px" />
          </ElCard>
        </ElCol>
        <ElCol :span="10">
          <ElCard shadow="never" header="📉 回撤">
            <EchartsUI ref="drawdownChartRef" height="300px" />
          </ElCard>
        </ElCol>
      </ElRow>

      <!-- 成交明细 -->
      <ElCard shadow="never" class="mb-4" header="🧾 成交明细">
        <ElTable :data="result.trades" stripe height="300">
          <ElTableColumn prop="timestamp" label="时间" width="180">
            <template #default="{ row }">
              {{ String(row.timestamp).slice(0, 19) }}
            </template>
          </ElTableColumn>
          <ElTableColumn prop="type" label="方向" width="80" align="center">
            <template #default="{ row }">
              <ElTag :type="row.type === 'buy' ? 'danger' : 'success'">
                {{ row.type === 'buy' ? '买入' : '卖出' }}
              </ElTag>
            </template>
          </ElTableColumn>
          <ElTableColumn prop="price" label="价格" align="right" />
          <ElTableColumn prop="quantity" label="数量" align="right" />
        </ElTable>
      </ElCard>
    </template>

    <!-- 历史记录 -->
    <ElCard shadow="never" header="历史回测记录">
      <ElTable :data="history" stripe>
        <ElTableColumn label="区间" width="200">
          <template #default="{ row }">
            {{ fmtDate(row.start_date) }} ~ {{ fmtDate(row.end_date) }}
          </template>
        </ElTableColumn>
        <ElTableColumn prop="total_return" label="总收益" width="100" align="right">
          <template #default="{ row }">
            <!-- A 股口径：涨红跌绿 -->
            <span :class="row.total_return >= 0 ? 'text-red-500' : 'text-green-500'">
              {{ row.total_return >= 0 ? '+' : '' }}{{ row.total_return }}%
            </span>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="sharpe_ratio" label="夏普" width="80" align="right" />
        <ElTableColumn prop="max_drawdown" label="回撤" width="90" align="right">
          <template #default="{ row }">
            <span class="text-green-500">-{{ row.max_drawdown }}%</span>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="win_rate" label="胜率" width="80" align="right">
          <template #default="{ row }">{{ row.win_rate }}%</template>
        </ElTableColumn>
        <ElTableColumn prop="trades_count" label="交易次数" width="90" align="center" />
        <ElTableColumn prop="created_at" label="时间" width="180">
          <template #default="{ row }">
            {{ String(row.created_at ?? '').slice(0, 19) }}
          </template>
        </ElTableColumn>
        <ElTableColumn label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <ElButton
              link
              type="primary"
              size="small"
              @click="handleExport(row)"
            >
              <Download class="mr-1 h-4 w-4" />导出
            </ElButton>
          </template>
        </ElTableColumn>
      </ElTable>
    </ElCard>
  </div>
</template>

<style scoped>
.backtest-page {
  min-height: 100%;
}
</style>
