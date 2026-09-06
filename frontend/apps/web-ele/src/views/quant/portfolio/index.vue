<script lang="ts" setup>
import { computed, onMounted, reactive, ref } from 'vue';

import {
  ElButton,
  ElCard,
  ElDialog,
  ElDrawer,
  ElEmpty,
  ElForm,
  ElFormItem,
  ElInput,
  ElInputNumber,
  ElMessage,
  ElMessageBox,
  ElOption,
  ElSelect,
  ElSwitch,
  ElTable,
  ElTableColumn,
  ElTabPane,
  ElTabs,
  ElTag,
} from 'element-plus';
import { Plus, RefreshCw, Trash2, Wallet } from '@lucide/vue';

import {
  createPortfolioApi,
  deletePortfolioApi,
  getPortfolioOverviewApi,
  getPortfolioPositionsApi,
  getPortfolioRebalancesApi,
  getPortfoliosApi,
  getPortfolioValuesApi,
  triggerPortfolioRebalanceApi,
  updatePortfolioApi,
  type Portfolio,
  type PortfolioOverview,
  type PortfolioPosition,
  type PortfolioValuePoint,
  type RebalanceRecord,
  type TradeMode,
} from '#/api/core/portfolio';
import { getStrategiesApi, type Strategy } from '#/api/quant';

const loading = ref(false);
const portfolios = ref<Portfolio[]>([]);
const strategies = ref<Strategy[]>([]);

const strategyMap = computed<Record<number, string>>(() =>
  Object.fromEntries(strategies.value.map((s) => [s.id, s.name])),
);

// ---------------- 新建 / 编辑 ----------------
const dialogVisible = ref(false);
const editingId = ref<number | null>(null);
const submitting = ref(false);
const form = reactive({
  name: '',
  strategy_id: undefined as number | undefined,
  mode: 'paper' as TradeMode,
  initial_capital: 1000000,
  auto_rebalance: true,
  is_active: true,
  description: '',
});

function resetForm() {
  form.name = '';
  form.strategy_id = undefined;
  form.mode = 'paper';
  form.initial_capital = 1000000;
  form.auto_rebalance = true;
  form.is_active = true;
  form.description = '';
}

function openCreate() {
  editingId.value = null;
  resetForm();
  dialogVisible.value = true;
}

function openEdit(row: Portfolio) {
  editingId.value = row.id;
  form.name = row.name;
  form.strategy_id = row.strategy_id;
  form.mode = row.mode;
  form.initial_capital = row.initial_capital;
  form.auto_rebalance = row.auto_rebalance;
  form.is_active = row.is_active;
  form.description = row.description || '';
  dialogVisible.value = true;
}

async function submitForm() {
  if (!form.name.trim()) {
    ElMessage.warning('请填写组合名称');
    return;
  }
  if (!form.strategy_id) {
    ElMessage.warning('请选择绑定的策略');
    return;
  }
  submitting.value = true;
  try {
    if (editingId.value) {
      await updatePortfolioApi(editingId.value, {
        name: form.name.trim(),
        strategy_id: form.strategy_id,
        auto_rebalance: form.auto_rebalance,
        is_active: form.is_active,
        description: form.description || null,
      });
      ElMessage.success('组合已更新');
    } else {
      await createPortfolioApi({
        name: form.name.trim(),
        strategy_id: form.strategy_id,
        mode: form.mode,
        initial_capital: form.initial_capital,
        auto_rebalance: form.auto_rebalance,
        is_active: form.is_active,
        description: form.description || null,
      });
      ElMessage.success('组合已创建');
    }
    dialogVisible.value = false;
    await load();
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败');
  } finally {
    submitting.value = false;
  }
}

async function remove(row: Portfolio) {
  try {
    await ElMessageBox.confirm(
      `确认删除组合「${row.name}」？其持仓 / 订单 / 调仓记录将一并保留但不再归属该组合。`,
      '删除组合',
      { type: 'warning' },
    );
  } catch {
    return;
  }
  try {
    await deletePortfolioApi(row.id);
    ElMessage.success('已删除');
    await load();
  } catch (e: any) {
    ElMessage.error(e?.message || '删除失败');
  }
}

async function toggleAuto(row: Portfolio) {
  try {
    await updatePortfolioApi(row.id, { auto_rebalance: row.auto_rebalance });
    ElMessage.success(row.auto_rebalance ? '已开启自动调仓' : '已关闭自动调仓');
  } catch (e: any) {
    row.auto_rebalance = !row.auto_rebalance; // 回滚
    ElMessage.error(e?.message || '更新失败');
  }
}

// ---------------- 详情 ----------------
const detailVisible = ref(false);
const detailRow = ref<Portfolio | null>(null);
const overview = ref<PortfolioOverview | null>(null);
const positions = ref<PortfolioPosition[]>([]);
const rebalances = ref<RebalanceRecord[]>([]);
const values = ref<PortfolioValuePoint[]>([]);
const detailLoading = ref(false);
const rebalancing = ref(false);

function money(v?: number | null) {
  if (v === null || v === undefined) return '--';
  return `¥${v.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`;
}
const pnlColor = (v: number) => (v >= 0 ? 'text-green-500' : 'text-red-500');

async function openDetail(row: Portfolio) {
  detailRow.value = row;
  detailVisible.value = true;
  detailLoading.value = true;
  try {
    const [ov, pos, reb, vals] = await Promise.all([
      getPortfolioOverviewApi(row.id),
      getPortfolioPositionsApi(row.id),
      getPortfolioRebalancesApi(row.id),
      getPortfolioValuesApi(row.id),
    ]);
    overview.value = ov;
    positions.value = pos || [];
    rebalances.value = reb || [];
    values.value = vals || [];
  } catch (e: any) {
    ElMessage.error(e?.message || '加载组合详情失败');
  } finally {
    detailLoading.value = false;
  }
}

async function triggerRebalance(row: Portfolio) {
  rebalancing.value = true;
  try {
    const res = await triggerPortfolioRebalanceApi(row.id, true);
    const status = (res as any)?.status;
    if (status === 'success') {
      ElMessage.success('调仓完成');
    } else if (status === 'skipped') {
      ElMessage.info('今日已执行，已跳过');
    } else {
      ElMessage.warning((res as any)?.reason || '调仓未产生下单');
    }
    await openDetail(row);
  } catch (e: any) {
    ElMessage.error(e?.message || '调仓失败');
  } finally {
    rebalancing.value = false;
  }
}

// ---------------- 加载 ----------------
async function load() {
  loading.value = true;
  try {
    const [ps, ss] = await Promise.all([
      getPortfoliosApi(),
      getStrategiesApi(),
    ]);
    portfolios.value = ps || [];
    strategies.value = ss || [];
  } catch (e: any) {
    ElMessage.error(e?.message || '加载组合列表失败');
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="portfolio-page p-4">
    <ElCard shadow="never" class="mb-4">
      <div class="flex items-center justify-between">
        <div>
          <h2 class="text-lg font-bold flex items-center gap-2">
            <Wallet class="w-5 h-5 text-blue-500" /> 投资组合
          </h2>
          <p class="text-sm text-gray-500 mt-1">
            组合绑定一个策略、拥有独立资金池与持仓；调仓与估值均按组合核算，
            取代原「以策略牵头的模拟盘」。
          </p>
        </div>
        <ElButton type="primary" :icon="Plus" @click="openCreate">
          新建组合
        </ElButton>
      </div>
    </ElCard>

    <ElCard shadow="never" v-loading="loading">
      <ElTable :data="portfolios" stripe empty-text="暂无组合，点击「新建组合」创建">
        <ElTableColumn prop="name" label="组合名称" min-width="160" />
        <ElTableColumn label="绑定策略" min-width="160">
          <template #default="{ row }">
            {{ strategyMap[row.strategy_id] || `策略#${row.strategy_id}` }}
          </template>
        </ElTableColumn>
        <ElTableColumn label="模式" width="90" align="center">
          <template #default="{ row }">
            <ElTag :type="row.mode === 'paper' ? 'info' : 'warning'" size="small">
              {{ row.mode === 'paper' ? '模拟' : '实盘' }}
            </ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="initial_capital" label="初始资金" width="130" align="right">
          <template #default="{ row }">{{ money(row.initial_capital) }}</template>
        </ElTableColumn>
        <ElTableColumn prop="cash_balance" label="可用资金" width="130" align="right">
          <template #default="{ row }">{{ money(row.cash_balance) }}</template>
        </ElTableColumn>
        <ElTableColumn label="自动调仓" width="100" align="center">
          <template #default="{ row }">
            <ElSwitch v-model="row.auto_rebalance" @change="toggleAuto(row)" />
          </template>
        </ElTableColumn>
        <ElTableColumn label="状态" width="90" align="center">
          <template #default="{ row }">
            <ElTag :type="row.is_active ? 'success' : 'info'" size="small">
              {{ row.is_active ? '激活' : '停用' }}
            </ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn label="操作" width="260" fixed="right">
          <template #default="{ row }">
            <ElButton link type="primary" size="small" @click="openDetail(row)">
              查看
            </ElButton>
            <ElButton link type="primary" size="small" @click="openEdit(row)">
              编辑
            </ElButton>
            <ElButton
              link
              type="warning"
              size="small"
              :loading="rebalancing"
              @click="triggerRebalance(row)"
            >
              调仓
            </ElButton>
            <ElButton link type="danger" size="small" :icon="Trash2" @click="remove(row)">
              删除
            </ElButton>
          </template>
        </ElTableColumn>
        <template #empty>
          <ElEmpty description="暂无组合" :image-size="80" />
        </template>
      </ElTable>
    </ElCard>

    <!-- 新建 / 编辑 -->
    <ElDialog
      v-model="dialogVisible"
      :title="editingId ? '编辑组合' : '新建组合'"
      width="480px"
    >
      <ElForm :model="form" label-width="96px" label-position="right">
        <ElFormItem label="组合名称" required>
          <ElInput v-model="form.name" placeholder="如：沪深300均衡" />
        </ElFormItem>
        <ElFormItem label="绑定策略" required>
          <ElSelect v-model="form.strategy_id" placeholder="选择策略" style="width: 100%">
            <ElOption
              v-for="s in strategies"
              :key="s.id"
              :label="s.name"
              :value="s.id"
            />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="交易模式">
          <ElSelect v-model="form.mode" style="width: 100%">
            <ElOption label="模拟盘" value="paper" />
            <ElOption label="实盘" value="live" />
          </ElSelect>
        </ElFormItem>
        <ElFormItem label="初始资金">
          <ElInputNumber
            v-model="form.initial_capital"
            :min="1000"
            :step="10000"
            style="width: 100%"
          />
        </ElFormItem>
        <ElFormItem label="自动调仓">
          <ElSwitch v-model="form.auto_rebalance" />
        </ElFormItem>
        <ElFormItem label="激活">
          <ElSwitch v-model="form.is_active" />
        </ElFormItem>
        <ElFormItem label="备注">
          <ElInput
            v-model="form.description"
            type="textarea"
            :rows="2"
            placeholder="可选"
          />
        </ElFormItem>
      </ElForm>
      <template #footer>
        <ElButton @click="dialogVisible = false">取消</ElButton>
        <ElButton type="primary" :loading="submitting" @click="submitForm">
          保存
        </ElButton>
      </template>
    </ElDialog>

    <!-- 详情 -->
    <ElDrawer
      v-model="detailVisible"
      :title="`组合详情 · ${detailRow?.name || ''}`"
      size="60%"
      v-loading="detailLoading"
    >
      <div class="flex items-center justify-between mb-3">
        <ElTag :type="detailRow?.mode === 'paper' ? 'info' : 'warning'">
          {{ detailRow?.mode === 'paper' ? '模拟盘' : '实盘' }}
        </ElTag>
        <ElButton
          type="primary"
          :icon="RefreshCw"
          :loading="rebalancing"
          @click="detailRow && triggerRebalance(detailRow)"
        >
          手动调仓
        </ElButton>
      </div>

      <ElTabs>
        <ElTabPane label="概览">
          <ElRow :gutter="12">
            <ElCol :span="8">
              <ElCard shadow="hover">
                <div class="text-center">
                  <div class="text-sm text-gray-500">总资产</div>
                  <div class="text-xl font-bold">{{ money(overview?.total_assets) }}</div>
                </div>
              </ElCard>
            </ElCol>
            <ElCol :span="8">
              <ElCard shadow="hover">
                <div class="text-center">
                  <div class="text-sm text-gray-500">可用资金</div>
                  <div class="text-xl font-bold">{{ money(overview?.cash_balance) }}</div>
                </div>
              </ElCard>
            </ElCol>
            <ElCol :span="8">
              <ElCard shadow="hover">
                <div class="text-center">
                  <div class="text-sm text-gray-500">持仓市值</div>
                  <div class="text-xl font-bold">{{ money(overview?.market_value) }}</div>
                </div>
              </ElCard>
            </ElCol>
          </ElRow>
          <ElRow :gutter="12" class="mt-3">
            <ElCol :span="8">
              <ElCard shadow="hover">
                <div class="text-center">
                  <div class="text-sm text-gray-500">累计盈亏</div>
                  <div :class="['text-xl font-bold', pnlColor(overview?.total_pnl ?? 0)]">
                    {{ (overview?.total_pnl ?? 0) >= 0 ? '+' : ''
                    }}{{ money(overview?.total_pnl) }}
                    <span class="text-sm ml-1">({{ overview?.total_pnl_pct ?? 0 }}%)</span>
                  </div>
                </div>
              </ElCard>
            </ElCol>
            <ElCol :span="8">
              <ElCard shadow="hover">
                <div class="text-center">
                  <div class="text-sm text-gray-500">浮动盈亏</div>
                  <div :class="['text-xl font-bold', pnlColor(overview?.unrealized_pnl ?? 0)]">
                    {{ money(overview?.unrealized_pnl) }}
                  </div>
                </div>
              </ElCard>
            </ElCol>
            <ElCol :span="8">
              <ElCard shadow="hover">
                <div class="text-center">
                  <div class="text-sm text-gray-500">持仓数</div>
                  <div class="text-xl font-bold">{{ overview?.position_count ?? 0 }}</div>
                </div>
              </ElCard>
            </ElCol>
          </ElRow>
        </ElTabPane>

        <ElTabPane :label="`持仓(${positions.length})`">
          <ElTable :data="positions" stripe max-height="420">
            <ElTableColumn prop="symbol" label="代码" width="120" />
            <ElTableColumn prop="quantity" label="数量" width="90" align="right" />
            <ElTableColumn prop="avg_price" label="成本" width="100" align="right" />
            <ElTableColumn prop="last_price" label="现价" width="100" align="right" />
            <ElTableColumn prop="market_value" label="市值" width="120" align="right" />
            <ElTableColumn label="盈亏" min-width="130" align="right">
              <template #default="{ row }">
                <span :class="pnlColor(row.unrealized_pnl)">
                  {{ row.unrealized_pnl >= 0 ? '+' : '' }}{{ money(row.unrealized_pnl) }}
                  ({{ row.pnl_percent }}%)
                </span>
              </template>
            </ElTableColumn>
          </ElTable>
        </ElTabPane>

        <ElTabPane :label="`调仓记录(${rebalances.length})`">
          <ElTable :data="rebalances" stripe max-height="420">
            <ElTableColumn prop="strategy_name" label="策略" min-width="140" />
            <ElTableColumn prop="rebalance_date" label="调仓日" width="110" />
            <ElTableColumn prop="target_count" label="目标数" width="80" align="right" />
            <ElTableColumn prop="orders_placed" label="下单数" width="80" align="right" />
            <ElTableColumn prop="amount" label="金额" width="130" align="right" />
            <ElTableColumn label="状态" width="90" align="center">
              <template #default="{ row }">
                <ElTag size="small" :type="row.status === 'success' ? 'success' : 'danger'">
                  {{ row.status }}
                </ElTag>
              </template>
            </ElTableColumn>
          </ElTable>
        </ElTabPane>

        <ElTabPane :label="`收益快照(${values.length})`">
          <ElTable :data="values" stripe max-height="420">
            <ElTableColumn prop="value_date" label="日期" width="120" />
            <ElTableColumn prop="total_assets" label="总资产" width="140" align="right">
              <template #default="{ row }">{{ money(row.total_assets) }}</template>
            </ElTableColumn>
            <ElTableColumn prop="market_value" label="市值" width="140" align="right">
              <template #default="{ row }">{{ money(row.market_value) }}</template>
            </ElTableColumn>
            <ElTableColumn label="当日收益" width="110" align="right">
              <template #default="{ row }">
                <span :class="pnlColor((row.daily_return ?? 0) * 100)">
                  {{ row.daily_return == null ? '--' : `${(row.daily_return * 100).toFixed(2)}%` }}
                </span>
              </template>
            </ElTableColumn>
            <ElTableColumn label="累计收益" width="110" align="right">
              <template #default="{ row }">
                <span :class="pnlColor((row.cumulative_return ?? 0) * 100)">
                  {{ row.cumulative_return == null ? '--' : `${(row.cumulative_return * 100).toFixed(2)}%` }}
                </span>
              </template>
            </ElTableColumn>
          </ElTable>
        </ElTabPane>
      </ElTabs>
    </ElDrawer>
  </div>
</template>

<style scoped>
.portfolio-page {
  min-height: 100%;
}
</style>
