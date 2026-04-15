<script lang="ts" setup>
import { ref, reactive } from 'vue';

import {
  ElButton,
  ElCard,
  ElCol,
  ElForm,
  ElFormItem,
  ElInput,
  ElMessage,
  ElOption,
  ElRow,
  ElSelect,
  ElTabPane,
  ElTabs,
  ElTable,
  ElTableColumn,
  ElTag,
} from 'element-plus';

import { ShoppingCart, Wallet, BarChart3, ArrowUpRight, ArrowDownLeft } from 'lucide-vue-next';

interface Order {
  id: string;
  symbol: string;
  side: 'BUY' | 'SELL';
  orderType: 'MARKET' | 'LIMIT';
  quantity: number;
  price: number;
  status: 'PENDING' | 'FILLED' | 'CANCELLED' | 'REJECTED';
  createdAt: string;
}

interface Position {
  symbol: string;
  side: 'LONG' | 'SHORT';
  quantity: number;
  avgPrice: number;
  currentPrice: number;
  pnl: number;
  pnlPercent: number;
}

const orderForm = reactive({
  symbol: '',
  orderType: 'MARKET',
  side: 'BUY',
  quantity: 100,
  price: 0,
});

const accountInfo = ref({
  totalAssets: 1050000,
  availableCash: 350000,
  marketValue: 700000,
  totalPnl: 50000,
  pnlPercent: 5.0,
});

const positions = ref<Position[]>([
  { symbol: '600519.SH', side: 'LONG', quantity: 100, avgPrice: 1850.00, currentPrice: 1920.50, pnl: 7050, pnlPercent: 3.81 },
  { symbol: '000858.SZ', side: 'LONG', quantity: 500, avgPrice: 165.00, currentPrice: 172.30, pnl: 3650, pnlPercent: 4.42 },
  { symbol: '601318.SH', side: 'LONG', quantity: 200, avgPrice: 48.50, currentPrice: 47.20, pnl: -260, pnlPercent: -2.68 },
]);

const orders = ref<Order[]>([
  { id: 'ORD001', symbol: '600519.SH', side: 'BUY', orderType: 'LIMIT', quantity: 100, price: 1850.00, status: 'FILLED', createdAt: '2026-04-14 09:30' },
  { id: 'ORD002', symbol: '000858.SZ', side: 'BUY', orderType: 'MARKET', quantity: 500, price: 165.00, status: 'FILLED', createdAt: '2026-04-14 10:15' },
  { id: 'ORD003', symbol: '601318.SH', side: 'BUY', orderType: 'LIMIT', quantity: 200, price: 48.50, status: 'FILLED', createdAt: '2026-04-14 11:00' },
  { id: 'ORD004', symbol: '600519.SH', side: 'SELL', orderType: 'LIMIT', quantity: 50, price: 1950.00, status: 'PENDING', createdAt: '2026-04-14 14:00' },
]);

const getSideType = (side: string) => side === 'BUY' ? 'success' : 'danger';
const getStatusType = (status: string) => {
  const map: Record<string, string> = {
    PENDING: 'warning',
    FILLED: 'success',
    CANCELLED: 'info',
    REJECTED: 'danger',
  };
  return map[status] || '';
};

const handleSubmitOrder = () => {
  console.log('提交订单:', orderForm);
  ElMessage.success('订单已提交');
};

const handleCancelOrder = (row: Order) => {
  console.log('撤单:', row.id);
  ElMessage.success('订单已撤销');
};
</script>

<template>
  <div class="trading-page p-4">
    <!-- 账户信息 -->
    <ElRow :gutter="16" class="mb-4">
      <ElCol :span="6">
        <ElCard shadow="hover">
          <div class="text-center">
            <Wallet class="w-6 h-6 mx-auto mb-2 text-blue-500" />
            <div class="text-sm text-gray-500">总资产</div>
            <div class="text-xl font-bold">¥{{ accountInfo.totalAssets.toLocaleString() }}</div>
          </div>
        </ElCard>
      </ElCol>
      <ElCol :span="6">
        <ElCard shadow="hover">
          <div class="text-center">
            <ShoppingCart class="w-6 h-6 mx-auto mb-2 text-green-500" />
            <div class="text-sm text-gray-500">可用资金</div>
            <div class="text-xl font-bold">¥{{ accountInfo.availableCash.toLocaleString() }}</div>
          </div>
        </ElCard>
      </ElCol>
      <ElCol :span="6">
        <ElCard shadow="hover">
          <div class="text-center">
            <BarChart3 class="w-6 h-6 mx-auto mb-2 text-orange-500" />
            <div class="text-sm text-gray-500">持仓市值</div>
            <div class="text-xl font-bold">¥{{ accountInfo.marketValue.toLocaleString() }}</div>
          </div>
        </ElCard>
      </ElCol>
      <ElCol :span="6">
        <ElCard shadow="hover">
          <div class="text-center">
            <ArrowUpRight v-if="accountInfo.totalPnl >= 0" class="w-6 h-6 mx-auto mb-2 text-green-500" />
            <ArrowDownLeft v-else class="w-6 h-6 mx-auto mb-2 text-red-500" />
            <div class="text-sm text-gray-500">总盈亏</div>
            <div :class="['text-xl font-bold', accountInfo.totalPnl >= 0 ? 'text-green-500' : 'text-red-500']">
              {{ accountInfo.totalPnl >= 0 ? '+' : '' }}¥{{ accountInfo.totalPnl.toLocaleString() }}
              <span class="text-sm ml-1">({{ accountInfo.pnlPercent }}%)</span>
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
              <ElInput v-model.number="orderForm.quantity" type="number" placeholder="100" />
            </ElFormItem>
            <ElFormItem label="价格" v-if="orderForm.orderType === 'LIMIT'">
              <ElInput v-model.number="orderForm.price" type="number" placeholder="0.00" />
            </ElFormItem>
            <ElButton
              type="primary"
              style="width: 100%"
              @click="handleSubmitOrder"
              :disabled="!orderForm.symbol"
            >
              {{ orderForm.side === 'BUY' ? '买入' : '卖出' }} {{ orderForm.symbol }}
            </ElButton>
          </ElForm>
        </ElCard>
      </ElCol>

      <!-- 右侧：持仓和订单 -->
      <ElCol :span="16">
        <ElCard shadow="never">
          <ElTabs>
            <ElTabPane label="当前持仓">
              <ElTable :data="positions" stripe>
                <ElTableColumn prop="symbol" label="证券代码" width="120" />
                <ElTableColumn prop="side" label="方向" width="80" align="center">
                  <template #default="{ row }">
                    <ElTag :type="row.side === 'LONG' ? 'success' : 'danger'">
                      {{ row.side === 'LONG' ? '多' : '空' }}
                    </ElTag>
                  </template>
                </ElTableColumn>
                <ElTableColumn prop="quantity" label="数量" width="80" align="right" />
                <ElTableColumn prop="avgPrice" label="成本价" width="100" align="right" />
                <ElTableColumn prop="currentPrice" label="现价" width="100" align="right" />
                <ElTableColumn prop="pnl" label="盈亏" width="100" align="right">
                  <template #default="{ row }">
                    <span :class="row.pnl >= 0 ? 'text-green-500' : 'text-red-500'">
                      {{ row.pnl >= 0 ? '+' : '' }}¥{{ row.pnl.toLocaleString() }}
                    </span>
                  </template>
                </ElTableColumn>
                <ElTableColumn prop="pnlPercent" label="盈亏%" width="90" align="right">
                  <template #default="{ row }">
                    <span :class="row.pnlPercent >= 0 ? 'text-green-500' : 'text-red-500'">
                      {{ row.pnlPercent >= 0 ? '+' : '' }}{{ row.pnlPercent }}%
                    </span>
                  </template>
                </ElTableColumn>
              </ElTable>
            </ElTabPane>
            <ElTabPane label="委托记录">
              <ElTable :data="orders" stripe>
                <ElTableColumn prop="id" label="委托编号" width="100" />
                <ElTableColumn prop="symbol" label="证券代码" width="120" />
                <ElTableColumn prop="side" label="方向" width="70" align="center">
                  <template #default="{ row }">
                    <ElTag :type="getSideType(row.side)">{{ row.side === 'BUY' ? '买' : '卖' }}</ElTag>
                  </template>
                </ElTableColumn>
                <ElTableColumn prop="orderType" label="类型" width="70" align="center" />
                <ElTableColumn prop="quantity" label="数量" width="70" align="right" />
                <ElTableColumn prop="price" label="价格" width="90" align="right" />
                <ElTableColumn prop="status" label="状态" width="80" align="center">
                  <template #default="{ row }">
                    <ElTag :type="getStatusType(row.status)">{{ row.status }}</ElTag>
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
</style>
