<script lang="ts" setup>
import { computed, onMounted, reactive, ref } from 'vue';

import {
  ElAlert,
  ElButton,
  ElCard,
  ElCol,
  ElEmpty,
  ElForm,
  ElFormItem,
  ElInput,
  ElInputNumber,
  ElMessage,
  ElOption,
  ElRadioButton,
  ElRadioGroup,
  ElRow,
  ElSelect,
  ElTabPane,
  ElTabs,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';
import {
  ArrowDownLeft as ArrowDown,
  ArrowUpRight as ArrowUp,
  BarChart3,
  ShoppingCart,
  Wallet,
} from '@lucide/vue';

import {
  cancelOrderApi,
  createOrderApi,
  getAccountApi,
  getAvailableSymbolsApi,
  getOrdersApi,
  getRebalancesApi,
  getTradesApi,
  getTradingModeApi,
} from '#/api/core/trading';
import type {
  AccountInfo,
  ModeInfo,
  RebalanceRecord,
  TradeMode,
  TradeOrder,
  TradeRecord,
} from '#/api/core/trading';

const loading = ref(false);
const submitting = ref(false);
const errorMsg = ref('');
const mode = ref<TradeMode>('paper');

const modeInfo = ref<ModeInfo | null>(null);
const account = ref<AccountInfo | null>(null);
const orders = ref<TradeOrder[]>([]);
const trades = ref<TradeRecord[]>([]);
const rebalances = ref<RebalanceRecord[]>([]);
const availableSymbols = ref<{ symbol: string; name: string; price: number }[]>([]);

const currentMode = computed(() =>
  (modeInfo.value?.modes || []).find((m) => m.mode === mode.value),
);
const positions = computed(() => account.value?.positions || []);

const orderForm = reactive({
  symbol: '',
  side: 'BUY' as 'BUY' | 'SELL',
  orderType: 'LIMIT' as 'MARKET' | 'LIMIT',
  quantity: 100,
  price: 0,
});

function money(v?: number | null) {
  if (v === null || v === undefined) return '--';
  return `¥${v.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`;
}

const pnlColor = (v: number) => (v >= 0 ? 'text-green-500' : 'text-red-500');

const ORDER_STATUS_TYPE: Record<
  string,
  'primary' | 'warning' | 'success' | 'info' | 'danger'
> = {
  PENDING: 'warning',
  FILLED: 'success',
  CANCELLED: 'info',
  REJECTED: 'danger',
};

const orderStatusType = (status: string) => ORDER_STATUS_TYPE[status] ?? 'info';

const REBALANCE_STATUS_TYPE: Record<
  string,
  'primary' | 'warning' | 'success' | 'info' | 'danger'
> = {
  success: 'success',
  error: 'danger',
  skipped: 'info',
};

const rebalanceStatusType = (status: string) =>
  REBALANCE_STATUS_TYPE[status] ?? 'info';

async function load() {
  loading.value = true;
  errorMsg.value = '';
  try {
    const [mi, acc, ord, trd, reb] = await Promise.all([
      getTradingModeApi(),
      getAccountApi(mode.value),
      getOrdersApi(mode.value, { limit: 50 }),
      getTradesApi(mode.value, { limit: 100 }),
      getRebalancesApi(20),
    ]);
    modeInfo.value = mi;
    account.value = acc;
    orders.value = ord || [];
    trades.value = trd || [];
    rebalances.value = reb || [];
  } catch (e: any) {
    errorMsg.value = e?.message || '加载交易数据失败';
  } finally {
    loading.value = false;
  }
}

async function handleSubmitOrder() {
  const symbol = orderForm.symbol.trim().toUpperCase();
  if (!symbol) {
    ElMessage.warning('请填写标的代码');
    return;
  }
  submitting.value = true;
  try {
    const res = await createOrderApi({
      symbol,
      side: orderForm.side,
      order_type: orderForm.orderType,
      quantity: orderForm.quantity,
      price: orderForm.orderType === 'LIMIT' ? orderForm.price : null,
      mode: mode.value,
    });
    if (res.status === 'REJECTED') {
      ElMessage.warning(`下单被拒：${res.message || '未通过风控校验'}`);
    } else {
      ElMessage.success(`${res.side === 'BUY' ? '买入' : '卖出'}委托已提交：${res.status}`);
    }
    await load();
  } finally {
    submitting.value = false;
  }
}

async function handleCancelOrder(row: TradeOrder) {
  await cancelOrderApi(row.order_id, mode.value);
  ElMessage.success('已撤单');
  await load();
}

function useSymbol(symbol: string) {
  orderForm.symbol = symbol;
}

onMounted(async () => {
  await load();
  // 可交易标的仅用于快捷填充代码，失败不影响主流程
  try {
    const res = await getAvailableSymbolsApi();
    availableSymbols.value = res?.symbols || [];
  } catch {
    availableSymbols.value = [];
  }
});
</script>

<template>
  <div class="trading-page p-4">
    <ElAlert v-if="errorMsg" :title="errorMsg" type="error" show-icon class="mb-4" />

    <!-- 模式切换 -->
    <ElCard shadow="never" class="mb-4">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-3">
          <span class="text-sm text-gray-500">交易模式</span>
          <ElRadioGroup v-model="mode" @change="load">
            <ElRadioButton value="paper">模拟盘</ElRadioButton>
            <ElRadioButton value="live">实盘</ElRadioButton>
          </ElRadioGroup>
        </div>
        <ElTag v-if="currentMode" :type="currentMode.ready ? 'success' : 'warning'">
          {{ currentMode.message }}
        </ElTag>
      </div>
      <ElAlert
        v-if="mode === 'live'"
        class="mt-3"
        type="warning"
        show-icon
        :closable="false"
        title="实盘模式：下单将经由券商适配器。当前默认 BROKER_DRY_RUN=true，不会发起真实请求；关闭前请确认凭证与成交双校验语义。"
      />
    </ElCard>

    <!-- 账户信息 -->
    <ElRow :gutter="16" class="mb-4">
      <ElCol :span="6">
        <ElCard v-loading="loading" shadow="hover">
          <div class="text-center">
            <Wallet class="w-6 h-6 mx-auto mb-2 text-blue-500" />
            <div class="text-sm text-gray-500">总资产</div>
            <div class="text-xl font-bold">{{ money(account?.total_assets) }}</div>
          </div>
        </ElCard>
      </ElCol>
      <ElCol :span="6">
        <ElCard v-loading="loading" shadow="hover">
          <div class="text-center">
            <ShoppingCart class="w-6 h-6 mx-auto mb-2 text-green-500" />
            <div class="text-sm text-gray-500">可用资金</div>
            <div class="text-xl font-bold">{{ money(account?.cash_balance) }}</div>
          </div>
        </ElCard>
      </ElCol>
      <ElCol :span="6">
        <ElCard v-loading="loading" shadow="hover">
          <div class="text-center">
            <BarChart3 class="w-6 h-6 mx-auto mb-2 text-orange-500" />
            <div class="text-sm text-gray-500">持仓市值</div>
            <div class="text-xl font-bold">{{ money(account?.market_value) }}</div>
          </div>
        </ElCard>
      </ElCol>
      <ElCol :span="6">
        <ElCard v-loading="loading" shadow="hover">
          <div class="text-center">
            <ArrowUp v-if="(account?.total_pnl ?? 0) >= 0" class="w-6 h-6 mx-auto mb-2 text-green-500" />
            <ArrowDown v-else class="w-6 h-6 mx-auto mb-2 text-red-500" />
            <div class="text-sm text-gray-500">累计盈亏</div>
            <div :class="['text-xl font-bold', pnlColor(account?.total_pnl ?? 0)]">
              {{ (account?.total_pnl ?? 0) >= 0 ? '+' : '' }}{{ money(account?.total_pnl) }}
              <span class="text-sm ml-1">({{ account?.total_pnl_pct ?? 0 }}%)</span>
            </div>
          </div>
        </ElCard>
      </ElCol>
    </ElRow>

    <ElRow :gutter="16">
      <!-- 左侧：下单面板 -->
      <ElCol :span="8">
        <ElCard shadow="never" header="下单">
          <ElForm :model="orderForm" label-position="top">
            <ElFormItem label="标的代码" required>
              <ElInput v-model="orderForm.symbol" placeholder="如: 600519.SH" />
            </ElFormItem>
            <div v-if="availableSymbols.length" class="mb-3 flex flex-wrap gap-1">
              <ElTag
                v-for="s in availableSymbols"
                :key="s.symbol"
                size="small"
                class="cursor-pointer"
                @click="useSymbol(s.symbol)"
              >
                {{ s.name }}
              </ElTag>
            </div>
            <ElRow :gutter="12">
              <ElCol :span="12">
                <ElFormItem label="买卖方向">
                  <ElSelect v-model="orderForm.side" style="width: 100%">
                    <ElOption label="买入" value="BUY" />
                    <ElOption label="卖出" value="SELL" />
                  </ElSelect>
                </ElFormItem>
              </ElCol>
              <ElCol :span="12">
                <ElFormItem label="订单类型">
                  <ElSelect v-model="orderForm.orderType" style="width: 100%">
                    <ElOption label="市价单" value="MARKET" />
                    <ElOption label="限价单" value="LIMIT" />
                  </ElSelect>
                </ElFormItem>
              </ElCol>
            </ElRow>
            <ElFormItem label="数量" required>
              <ElInputNumber v-model="orderForm.quantity" :min="100" :step="100" style="width: 100%" />
            </ElFormItem>
            <ElFormItem v-if="orderForm.orderType === 'LIMIT'" label="价格" required>
              <ElInputNumber v-model="orderForm.price" :min="0" :precision="2" :step="0.01" style="width: 100%" />
            </ElFormItem>
            <ElButton
              type="primary"
              style="width: 100%"
              :loading="submitting"
              :disabled="!orderForm.symbol || !currentMode?.ready"
              @click="handleSubmitOrder"
            >
              {{ orderForm.side === 'BUY' ? '买入' : '卖出' }} {{ orderForm.symbol }}
            </ElButton>
          </ElForm>
        </ElCard>
      </ElCol>

      <!-- 右侧：持仓 / 委托 / 成交 / 调仓 -->
      <ElCol :span="16">
        <ElCard shadow="never">
          <ElTabs>
            <ElTabPane :label="`当前持仓(${positions.length})`">
              <ElTable v-loading="loading" :data="positions" stripe max-height="420">
                <ElTableColumn prop="symbol" label="证券代码" width="120" />
                <ElTableColumn prop="quantity" label="数量" width="90" align="right" />
                <ElTableColumn prop="avg_price" label="成本价" width="100" align="right" />
                <ElTableColumn prop="last_price" label="现价" width="100" align="right" />
                <ElTableColumn prop="market_value" label="市值" width="120" align="right" />
                <ElTableColumn label="盈亏" min-width="120" align="right">
                  <template #default="{ row }">
                    <span :class="pnlColor(row.unrealized_pnl)">
                      {{ row.unrealized_pnl >= 0 ? '+' : '' }}{{ money(row.unrealized_pnl) }}
                      ({{ row.pnl_percent }}%)
                    </span>
                  </template>
                </ElTableColumn>
                <ElTableColumn label="操作" width="80" fixed="right">
                  <template #default="{ row }">
                    <ElButton link type="primary" size="small" @click="useSymbol(row.symbol)">
                      下单
                    </ElButton>
                  </template>
                </ElTableColumn>
                <template #empty>
                  <ElEmpty description="暂无持仓" :image-size="60" />
                </template>
              </ElTable>
            </ElTabPane>

            <ElTabPane :label="`委托记录(${orders.length})`">
              <ElTable v-loading="loading" :data="orders" stripe max-height="420">
                <ElTableColumn prop="order_id" label="委托编号" width="180" show-overflow-tooltip />
                <ElTableColumn prop="symbol" label="证券代码" width="110" />
                <ElTableColumn label="方向" width="70" align="center">
                  <template #default="{ row }">
                    <ElTag :type="row.side === 'BUY' ? 'success' : 'danger'" size="small">
                      {{ row.side === 'BUY' ? '买' : '卖' }}
                    </ElTag>
                  </template>
                </ElTableColumn>
                <ElTableColumn prop="order_type" label="类型" width="80" align="center" />
                <ElTableColumn prop="quantity" label="数量" width="80" align="right" />
                <ElTableColumn prop="price" label="价格" width="90" align="right" />
                <ElTableColumn prop="source" label="来源" width="90" align="center">
                  <template #default="{ row }">
                    <ElTag size="small" :type="row.source === 'strategy' ? 'warning' : 'info'">
                      {{ row.source === 'strategy' ? '策略' : '手动' }}
                    </ElTag>
                  </template>
                </ElTableColumn>
                <ElTableColumn prop="status" label="状态" width="100" align="center">
                  <template #default="{ row }">
                    <ElTag size="small" :type="orderStatusType(row.status)">
                      {{ row.status }}
                    </ElTag>
                  </template>
                </ElTableColumn>
                <ElTableColumn label="操作" width="80" fixed="right">
                  <template #default="{ row }">
                    <ElButton
                      v-if="row.status === 'PENDING'"
                      link
                      type="danger"
                      size="small"
                      @click="handleCancelOrder(row)"
                    >
                      撤单
                    </ElButton>
                  </template>
                </ElTableColumn>
                <template #empty>
                  <ElEmpty description="暂无委托" :image-size="60" />
                </template>
              </ElTable>
            </ElTabPane>

            <ElTabPane :label="`成交记录(${trades.length})`">
              <ElTable v-loading="loading" :data="trades" stripe max-height="420">
                <ElTableColumn prop="symbol" label="证券代码" width="120" />
                <ElTableColumn label="方向" width="70" align="center">
                  <template #default="{ row }">
                    <ElTag :type="row.side === 'BUY' ? 'success' : 'danger'" size="small">
                      {{ row.side === 'BUY' ? '买' : '卖' }}
                    </ElTag>
                  </template>
                </ElTableColumn>
                <ElTableColumn prop="price" label="成交价" width="100" align="right" />
                <ElTableColumn prop="quantity" label="数量" width="90" align="right" />
                <ElTableColumn prop="amount" label="成交额" width="120" align="right" />
                <ElTableColumn prop="commission" label="手续费" width="100" align="right" />
                <ElTableColumn prop="trade_time" label="成交时间" min-width="180" />
                <template #empty>
                  <ElEmpty description="暂无成交" :image-size="60" />
                </template>
              </ElTable>
            </ElTabPane>

            <ElTabPane :label="`调仓记录(${rebalances.length})`">
              <ElTable v-loading="loading" :data="rebalances" stripe max-height="420">
                <ElTableColumn prop="strategy_name" label="策略" min-width="140" show-overflow-tooltip />
                <ElTableColumn prop="rebalance_date" label="调仓日" width="110" />
                <ElTableColumn prop="target_count" label="目标数" width="80" align="right" />
                <ElTableColumn prop="orders_placed" label="下单数" width="80" align="right" />
                <ElTableColumn prop="amount" label="金额" width="130" align="right" />
                <ElTableColumn prop="status" label="状态" width="90" align="center">
                  <template #default="{ row }">
                    <ElTag size="small" :type="rebalanceStatusType(row.status)">
                      {{ row.status }}
                    </ElTag>
                  </template>
                </ElTableColumn>
                <template #empty>
                  <ElEmpty description="暂无调仓记录" :image-size="60" />
                </template>
              </ElTable>
            </ElTabPane>
          </ElTabs>
        </ElCard>
      </ElCol>
    </ElRow>
  </div>
</template>

<style scoped>
.trading-page {
  min-height: 100%;
}
.cursor-pointer {
  cursor: pointer;
}
</style>
