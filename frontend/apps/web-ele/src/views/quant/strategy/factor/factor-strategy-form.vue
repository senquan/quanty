<script lang="ts" setup>
import type {
  FactorDefinition,
  FactorStrategy,
  FactorStrategyConfig,
} from '#/api/factor-strategy';

import { computed, nextTick, reactive, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { RefreshCw, Save, Search, X } from '@lucide/vue';
import {
  ElButton,
  ElCheckbox,
  ElDialog,
  ElInput,
  ElInputNumber,
  ElMessage,
  ElOption,
  ElOptionGroup,
  ElRadio,
  ElRadioGroup,
  ElSelect,
  ElSlider,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTimePicker,
} from 'element-plus';

import { listFactorsApi } from '#/api/factor-library';
import {
  createFactorStrategyApi,
  factorAvailabilityApi,
  getFactorStrategyApi,
  runBacktestApi,
  updateFactorStrategyApi,
} from '#/api/factor-strategy';
import { getWatchlistApi, type WatchlistItem } from '#/api/watchlist';

import {
  categoryTheme,
  defaultConfig,
  neutralizeLabels,
  normalizeUniverse,
  rebalanceLabel,
  universeSummary,
  weightModeLabels,
} from './theme';

const props = defineProps<{
  strategy?: FactorStrategy | null;
  visible: boolean;
}>();

const emit = defineEmits<{
  saved: [];
  'update:visible': [v: boolean];
}>();

const router = useRouter();

const loading = ref(false);
const factors = ref<FactorDefinition[]>([]);
const availability = ref<Record<string, boolean>>({});
const submitting = ref(false);
const backtesting = ref(false);

const form = reactive<{
  config: FactorStrategyConfig;
  description: string;
  name: string;
}>({
  name: '',
  description: '',
  config: defaultConfig(),
});

const isEdit = computed(() => !!props.strategy?.id);

/** 按类别分组的可选因子（无因子值置灰） */
const groupedFactors = computed(() => {
  const map: Record<string, FactorDefinition[]> = {};
  for (const f of factors.value) {
    (map[f.category || 'custom'] ||= []).push(f);
  }
  return Object.entries(map).map(([cat, list]) => ({
    cat,
    theme: categoryTheme(cat),
    list: list.toSorted((a, b) => a.code.localeCompare(b.code)),
  }));
});

const selectedFactors = computed(() =>
  factors.value.filter((f) => form.config.factor_codes.includes(f.code)),
);

/** 权重模式为 manual 时，未显式赋权的因子默认等权基数 */
const manualWeight = (code: string): number => {
  return form.config.weights[code] ?? 1;
};
const setManualWeight = (code: string, v: number) => {
  form.config.weights[code] = v;
};

const tradeDate = computed({
  get() {
    const [h, m] = (form.config.trade_time || '10:00').split(':');
    return new Date(2020, 0, 1, Number(h) || 10, Number(m) || 0);
  },
  set(val: Date) {
    const h = String(val.getHours()).padStart(2, '0');
    const m = String(val.getMinutes()).padStart(2, '0');
    form.config.trade_time = `${h}:${m}`;
  },
});

/** 自选股代码文本（逗号/空格分隔 <-> custom_codes 数组） */
const customCodesText = computed({
  get: () => (form.config.custom_codes || []).join(', '),
  set: (v: string) => {
    form.config.custom_codes = v
      .split(/[,\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
  },
});

/** 是否启用自选股（与板块取并集；取消勾选即清空 custom_codes） */
const customEnabled = ref(false);
watch(customEnabled, (v) => {
  if (!v) form.config.custom_codes = [];
});

// ============ 从自选股选择 ============
const watchlistPickerVisible = ref(false);
const watchlistLoading = ref(false);
const watchlistItems = ref<WatchlistItem[]>([]);
const watchlistSelection = ref<WatchlistItem[]>([]);
const watchlistSearch = ref('');
const watchlistTableRef = ref<InstanceType<typeof ElTable>>();

const watchlistFiltered = computed(() => {
  const kw = watchlistSearch.value.trim().toLowerCase();
  if (!kw) return watchlistItems.value;
  return watchlistItems.value.filter(
    (i) =>
      i.code.toLowerCase().includes(kw) ||
      (i.name || '').toLowerCase().includes(kw),
  );
});

function openWatchlistPicker() {
  watchlistPickerVisible.value = true;
}

async function loadWatchlist() {
  watchlistLoading.value = true;
  try {
    watchlistItems.value = await getWatchlistApi();
    await nextTick();
    const current = new Set(form.config.custom_codes || []);
    for (const item of watchlistItems.value) {
      if (current.has(item.code)) {
        watchlistTableRef.value?.toggleRowSelection(item, true);
      }
    }
  } catch {
    ElMessage.error('加载自选股失败');
  } finally {
    watchlistLoading.value = false;
  }
}

function onWatchlistSelection(rows: WatchlistItem[]) {
  watchlistSelection.value = rows;
}

function applyWatchlistSelection() {
  const codes = new Set(form.config.custom_codes || []);
  for (const item of watchlistSelection.value) codes.add(item.code);
  form.config.custom_codes = [...codes];
  customEnabled.value = true;
  watchlistPickerVisible.value = false;
  ElMessage.success(`已加入 ${codes.size} 只自选股`);
}

function reset() {
  form.name = '';
  form.description = '';
  form.config = defaultConfig();
  customEnabled.value = false;
}

async function loadFactors() {
  try {
    const [fs, av] = await Promise.all([
      listFactorsApi({ with_metrics: true }),
      factorAvailabilityApi().catch(() => ({})),
    ]);
    factors.value = fs;
    availability.value = av;
  } catch {
    ElMessage.error('加载因子库失败');
  }
}

async function loadForEdit() {
  if (!props.strategy?.id) return;
  try {
    const s = await getFactorStrategyApi(props.strategy.id);
    form.name = s.name;
    form.description = s.description || '';
    form.config = {
      ...defaultConfig(),
      ...s.config,
      filters: { ...defaultConfig().filters, ...(s.config?.filters || {}) },
      universe: normalizeUniverse(s.config?.universe),
      custom_codes: Array.isArray(s.config?.custom_codes)
        ? s.config!.custom_codes
        : [],
    };
    customEnabled.value = (form.config.custom_codes || []).length > 0;
  } catch {
    ElMessage.error('加载策略详情失败');
  }
}

watch(
  () => props.visible,
  async (v) => {
    if (!v) return;
    if (factors.value.length === 0) await loadFactors();
    if (isEdit.value) await loadForEdit();
    else reset();
  },
  { immediate: true },
);

function buildPayload() {
  // 注意：form.config 是 reactive 代理，structuredClone 无法克隆 Proxy（会抛 DataCloneError）。
  // config 本就是要发成 JSON 的纯数据，用 JSON 深拷贝最稳妥。
  // eslint-disable-next-line unicorn/prefer-structured-clone
  const cfg: FactorStrategyConfig = JSON.parse(JSON.stringify(form.config));
  if (cfg.weight_mode === 'manual') {
    // 仅保留已选因子的权重，发送原始比例（后端会归一化）
    const w: Record<string, number> = {};
    for (const c of cfg.factor_codes) w[c] = cfg.weights[c] ?? 1;
    cfg.weights = w;
  } else {
    cfg.weights = {};
  }
  return {
    name: form.name.trim(),
    description: form.description || null,
    config: cfg,
    is_active: cfg.is_active,
  };
}

async function persist(): Promise<null | number> {
  if (!form.name.trim()) {
    ElMessage.warning('请填写策略名称');
    return null;
  }
  if (form.config.factor_codes.length === 0) {
    ElMessage.warning('请至少选择一个因子');
    return null;
  }
  submitting.value = true;
  try {
    if (isEdit.value && props.strategy) {
      await updateFactorStrategyApi(props.strategy.id, buildPayload());
      ElMessage.success('已保存');
      return props.strategy.id;
    }
    const created = await createFactorStrategyApi(buildPayload());
    ElMessage.success('已创建');
    return created.id;
  } catch (error: any) {
    // 直接展示后端透传的真实原因（含清洗服务报错），不再用写死的「保存失败」掩盖
    const msg =
      error?.response?.data?.msg ||
      error?.response?.data?.message ||
      error?.response?.data?.error ||
      error?.message ||
      '保存失败';
    ElMessage.error(msg);
    return null;
  } finally {
    submitting.value = false;
  }
}

async function handleSave() {
  const id = await persist();
  if (id != null) {
    emit('saved');
    emit('update:visible', false);
  }
}

async function handleSaveAndBacktest() {
  const id = await persist();
  if (id == null) return;
  backtesting.value = true;
  try {
    await runBacktestApi(id);
    ElMessage.success('回测完成，跳转到详情查看');
    emit('saved');
    emit('update:visible', false);
    router.push({
      path: '/quant/strategy/factor-detail',
      query: { id: String(id) },
    });
  } catch {
    ElMessage.error('回测失败，请检查因子是否有值');
  } finally {
    backtesting.value = false;
  }
}

function close() {
  emit('update:visible', false);
}
</script>

<template>
  <ElDialog
    :model-value="visible"
    :title="isEdit ? '编辑因子策略' : '新建因子策略'"
    width="900px"
    top="5vh"
    @update:model-value="(v: boolean) => emit('update:visible', v)"
    @close="close"
  >
    <div v-loading="loading" class="max-h-[72vh] overflow-y-auto pr-1">
      <!-- 基本信息 -->
      <section class="mb-5">
        <h3 class="text-sm font-semibold mb-3 text-gray-700">基本信息</h3>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="text-xs text-gray-400">策略名称</label>
            <ElInput v-model="form.name" placeholder="例如：价值+动量双因子" />
          </div>
          <div>
            <label class="text-xs text-gray-400">描述</label>
            <ElInput v-model="form.description" placeholder="策略说明（可选）" />
          </div>
        </div>
      </section>

      <!-- 因子组合 -->
      <section class="mb-5">
        <h3 class="text-sm font-semibold mb-3 text-gray-700">
          因子组合
          <span class="text-xs text-gray-400 font-normal ml-1">
            （多选，带类别/分级徽标；灰色为暂无因子值）
          </span>
        </h3>
        <ElSelect
          v-model="form.config.factor_codes"
          multiple
          filterable
          clearable
          placeholder="选择因子"
          class="w-full"
          @change="
            (codes: string[]) => {
              // 清理 manual 权重中已被移除的因子
              if (form.config.weight_mode === 'manual') {
                const next: Record<string, number> = {};
                for (const c of codes) next[c] = form.config.weights[c] ?? 1;
                form.config.weights = next;
              }
            }
          "
        >
          <ElOptionGroup
            v-for="g in groupedFactors"
            :key="g.cat"
            :label="g.theme.label"
          >
            <ElOption
              v-for="f in g.list"
              :key="f.code"
              :value="f.code"
              :disabled="availability[f.code] === false"
              :label="`${f.name}（${f.code}）`"
            >
              <div class="flex items-center justify-between gap-2">
                <span class="flex items-center gap-1.5">
                  <component
                    :is="g.theme.icon"
                    class="w-3.5 h-3.5"
                    :style="{ color: g.theme.color }"
                  />
                  <span class="text-gray-700">{{ f.name }}</span>
                  <span class="font-mono text-xs text-gray-400">{{ f.code }}</span>
                </span>
                <span class="flex items-center gap-2">
                  <span
                    v-if="availability[f.code] === false"
                    class="text-[10px] text-gray-300"
                  >
                    暂无因子值
                  </span>
                  <span
                    v-else-if="f.metrics?.ir != null"
                    class="text-[10px] font-mono"
                    :class="f.metrics.ir >= 0.5 ? 'text-blue-500' : 'text-gray-400'"
                  >
                    IR {{ f.metrics.ir.toFixed(2) }}
                  </span>
                </span>
              </div>
            </ElOption>
          </ElOptionGroup>
        </ElSelect>

        <!-- 已选因子 + 权重 -->
        <div
          v-if="selectedFactors.length > 0"
          class="mt-3 rounded-lg bg-gray-50 border border-gray-100 p-3 space-y-2"
        >
          <div class="flex items-center justify-between">
            <span class="text-xs text-gray-500">权重模式</span>
            <ElRadioGroup v-model="form.config.weight_mode" size="small">
              <ElRadio
                v-for="(label, mode) in weightModeLabels"
                :key="mode"
                :value="mode"
                >
                {{ label }}
            </ElRadio>
            </ElRadioGroup>
          </div>
          <div
            v-if="form.config.weight_mode === 'manual'"
            class="space-y-2 pt-1"
          >
            <div
              v-for="f in selectedFactors"
              :key="f.code"
              class="flex items-center gap-3"
            >
              <span class="w-44 truncate text-xs text-gray-600">{{ f.name }}</span>
              <ElSlider
                :model-value="manualWeight(f.code)"
                :min="0"
                :max="100"
                :step="1"
                class="flex-1"
                @update:model-value="(v) => setManualWeight(f.code, v as number)"
              />
              <span class="w-10 text-right font-mono text-xs text-gray-500">
                {{ manualWeight(f.code) }}
              </span>
            </div>
          </div>
          <div v-else class="text-xs text-gray-400 pt-1">
            回测时按各因子历史 |IR| 自动归一化权重；实时调仓复用最新一期 IR。
          </div>
        </div>
      </section>

      <!-- 处理与过滤 -->
      <section class="mb-5">
        <h3 class="text-sm font-semibold mb-3 text-gray-700">处理与过滤</h3>
        <div class="grid grid-cols-2 gap-3 items-center">
          <div class="col-span-2">
            <label class="text-xs text-gray-400 whitespace-nowrap block mb-1.5">标的股票池（可多选，未选板块 = 全市场）</label>
            <div class="flex items-center gap-3 flex-wrap">
              <ElSelect
                v-model="form.config.universe"
                multiple
                collapse-tags
                collapse-tags-tooltip
                :max-collapse-tags="3"
                size="small"
                clearable
                placeholder="未选板块 = 全市场"
                class="min-w-[240px]"
              >
                <ElOption value="main" label="沪深主板" />
                <ElOption value="cyb" label="创业板" />
                <ElOption value="kcb" label="科创板" />
                <ElOption value="bj" label="北交所" />
              </ElSelect>
              <ElCheckbox v-model="customEnabled" border>自选股</ElCheckbox>
              <ElInput
                v-if="customEnabled"
                v-model="customCodesText"
                size="small"
                class="flex-1 min-w-[220px]"
                placeholder="输入自选股代码，逗号或空格分隔，如 600519, 000001"
              />
              <ElButton
                v-if="customEnabled"
                size="small"
                @click="openWatchlistPicker"
              >
                <Search class="w-4 h-4 mr-1" />
                从自选股选择
              </ElButton>
            </div>
          </div>
          <div>
            <label class="text-xs text-gray-400">中性化</label>
            <ElRadioGroup v-model="form.config.neutralize" size="small" class="ml-2">
              <ElRadio
                v-for="(label, mode) in neutralizeLabels"
                :key="mode"
                :value="mode"
                >
                {{ label }}
                </ElRadio>
            </ElRadioGroup>
          </div>
          <div class="flex items-center gap-3">
            <label class="text-xs text-gray-400">选股数量 Top N</label>
            <ElInputNumber
              v-model="form.config.top_n"
              :min="5"
              :max="100"
              :step="1"
              size="small"
            />
          </div>
          <div class="flex items-center gap-3">
            <span class="text-xs text-gray-400">排除 ST</span>
            <ElSwitch v-model="form.config.filters.exclude_st" />
          </div>
          <div class="flex items-center gap-3">
            <label class="text-xs text-gray-400">最小上市天数</label>
            <ElInputNumber
              v-model="form.config.filters.min_list_days"
              :min="0"
              :max="3650"
              :step="10"
              size="small"
            />
          </div>
          <div class="flex items-center gap-3">
            <span class="text-xs text-gray-400">排除停牌</span>
            <ElSwitch v-model="form.config.filters.exclude_suspended" />
          </div>
          <div class="flex items-center gap-3">
            <span class="text-xs text-gray-400">买入排除涨停</span>
            <ElSwitch v-model="form.config.filters.exclude_limit_up" />
          </div>
          <div class="flex items-center gap-3">
            <span class="text-xs text-gray-400">卖出排除跌停</span>
            <ElSwitch v-model="form.config.filters.exclude_limit_down" />
          </div>
          <div class="flex items-center gap-3">
            <label class="text-xs text-gray-400">市值下限（亿元）</label>
            <ElInputNumber
              v-model="form.config.filters.min_cap"
              :min="0"
              :max="10000"
              :step="10"
              size="small"
              placeholder="不限"
            />
          </div>
        </div>
      </section>

      <!-- 交易方案 -->
      <section class="mb-2">
        <h3 class="text-sm font-semibold mb-3 text-gray-700">交易方案</h3>
        <div class="grid grid-cols-2 gap-3 items-center">
          <div class="flex items-center gap-3">
            <label class="text-xs text-gray-400">调仓周期</label>
            <ElRadioGroup v-model="form.config.rebalance.freq" size="small" class="ml-1">
              <ElRadio value="weekly">每周</ElRadio>
              <ElRadio value="monthly">每月</ElRadio>
              <ElRadio value="every_n_days">每 N 日</ElRadio>
            </ElRadioGroup>
          </div>
          <div v-if="form.config.rebalance.freq === 'every_n_days'" class="flex items-center gap-3">
            <label class="text-xs text-gray-400">N =</label>
            <ElInputNumber
              v-model="form.config.rebalance.every_n_days"
              :min="1"
              :max="60"
              size="small"
            />
          </div>
          <div class="flex items-center gap-3">
            <label class="text-xs text-gray-400">交易时间</label>
            <ElTimePicker
              v-model="tradeDate"
              format="HH:mm"
              size="small"
            />
          </div>
          <div class="flex items-center gap-3">
            <label class="text-xs text-gray-400">初始资金</label>
            <ElInputNumber
              v-model="form.config.initial_capital"
              :min="10000"
              :step="100000"
              size="small"
            />
          </div>
          <div
            v-if="form.config.weight_mode === 'auto_ir'"
            class="flex items-center gap-3"
          >
            <label class="text-xs text-gray-400">IR 回看天数</label>
            <ElInputNumber
              v-model="form.config.lookback_days"
              :min="20"
              :max="250"
              :step="10"
              size="small"
            />
          </div>
          <div class="flex items-center gap-3">
            <label class="text-xs text-gray-400">启用（参与自动调仓）</label>
            <ElSwitch v-model="form.config.is_active" />
          </div>
        </div>
      </section>

      <div
        class="mt-3 rounded-lg bg-blue-50 border border-blue-100 px-3 py-2 text-xs text-blue-600"
      >
        当前配置：{{ rebalanceLabel(form.config.rebalance) }}调仓 · {{
          form.config.neutralize === 'industry' ? '行业中性化' : '仅标准化'
        }}
        · {{ universeSummary(form.config.universe, form.config.custom_codes) }} · Top
        {{ form.config.top_n }} · {{ form.config.trade_time }}
      </div>
    </div>

    <template #footer>
      <div class="flex justify-end gap-2">
        <ElButton :icon="X" @click="close">取消</ElButton>
        <ElButton
          :icon="Save"
          :loading="submitting"
          type="primary"
          @click="handleSave"
        >
          保存
        </ElButton>
        <ElButton
          :icon="RefreshCw"
          :loading="backtesting"
          type="success"
          @click="handleSaveAndBacktest"
        >
          保存并回测
        </ElButton>
      </div>
    </template>
  </ElDialog>

  <!-- 从自选股选择 -->
  <ElDialog
    v-model="watchlistPickerVisible"
    title="从自选股选择"
    width="560px"
    @open="loadWatchlist"
  >
    <div class="space-y-2">
      <ElInput
        v-model="watchlistSearch"
        size="small"
        clearable
        placeholder="搜索代码或名称"
      >
        <template #prefix>
          <Search class="w-4 h-4" />
        </template>
      </ElInput>
      <ElTable
        ref="watchlistTableRef"
        v-loading="watchlistLoading"
        :data="watchlistFiltered"
        row-key="id"
        height="360"
        @selection-change="onWatchlistSelection"
      >
        <ElTableColumn type="selection" width="48" :reserve-selection="true" />
        <ElTableColumn prop="code" label="代码" width="140">
          <template #default="{ row }">
            <span class="font-mono">{{ row.code }}</span>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="name" label="名称" min-width="160" />
      </ElTable>
    </div>
    <template #footer>
      <ElButton @click="watchlistPickerVisible = false">取消</ElButton>
      <ElButton type="primary" @click="applyWatchlistSelection">添加所选</ElButton>
    </template>
  </ElDialog>
</template>
