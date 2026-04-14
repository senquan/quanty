<script lang="ts" setup>
import { ref, reactive } from 'vue';
import { Card, Table, Tag, Button, Row, Col, Input, Select, Tabs, TabPane, Form, FormItem, Message } from 'element-plus';
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
  Message.success('订单已提交');
};

const handleCancelOrder = (row: Order) => {
  console.log('撤单:', row.id);
  Message.success('订单已撤销');
};
</script>

<template>
  <div class="trading-page p-4">
    <!-- 账户信息 -->
    <Row :gutter="16" class="mb-4">
      <Col :span="6">
        <Card shadow="hover">
          <div class="text-center">
            <Wallet class="w-6 h-6 mx-auto mb-2 text-blue-500" />
            <div class="text-sm text-gray-500">总资产</div>
            <div class="text-xl font-bold">¥{{ accountInfo.totalAssets.toLocaleString() }}</div>
          </div>
        </Card>
      </Col>
      <Col :span="6">
        <Card shadow="hover">
          <div class="text-center">
            <ShoppingCart class="w-6 h-6 mx-auto mb-2 text-green-500" />
            <div class="text-sm text-gray-500">可用资金</div>
            <div class="text-xl font-bold">¥{{ accountInfo.availableCash.toLocaleString() }}</div>
          </div>
        </Card>
      </Col>
      <Col :span="6">
        <Card shadow="hover">
          <div class="text-center">
            <BarChart3 class="w-6 h-6 mx-auto mb-2 text-orange-500" />
            <div class="text-sm text-gray-500">持仓市值</div>
            <div class="text-xl font-bold">¥{{ accountInfo.marketValue.toLocaleString() }}</div>
          </div>
        </Card>
      </Col>
      <Col :span="6">
        <Card shadow="hover">
          <div class="text-center">
            <ArrowUpRight v-if="accountInfo.totalPnl >= 0" class="w-6 h-6 mx-auto mb-2 text-green-500" />
            <ArrowDownLeft v-else class="w-6 h-6 mx-auto mb-2 text-red-500" />
            <div class="text-sm text-gray-500">总盈亏</div>
            <div :class="['text-xl font-bold', accountInfo.totalPnl >= 0 ? 'text-green-500' : 'text-red-500']">
              {{ accountInfo.totalPnl >= 0 ? '+' : '' }}¥{{ accountInfo.totalPnl.toLocaleString() }}
              <span class="text-sm ml-1">({{ accountInfo.pnlPercent }}%)</span>
            </div>
          </div>
        </Card>
      </Col>
    </Row>

    <Row :gutter="16">
      <!-- 左侧：下单面板 -->
      <Col :span="8">
        <Card shadow="never" header="下单">
          <Form :model="orderForm" label-position="top">
            <FormItem label="标的代码" required>
              <Input v-model="orderForm.symbol" placeholder="如: 600519.SH" />
            </FormItem>
            <Row :gutter="12">
              <Col :span="12">
                <FormItem label="买卖方向">
                  <Select v-model="orderForm.side" style="width: 100%">
                    <el-option label="买入" value="BUY" />
                    <el-option label="卖出" value="SELL" />
                  </Select>
                </FormItem>
              </Col>
              <Col :span="12">
                <FormItem label="订单类型">
                  <Select v-model="orderForm.orderType" style="width: 100%">
                    <el-option label="市价单" value="MARKET" />
                    <el-option label="限价单" value="LIMIT" />
                  </Select>
                </FormItem>
              </Col>
            </Row>
            <FormItem label="数量" required>
              <Input v-model.number="orderForm.quantity" type="number" placeholder="100" />
            </FormItem>
            <FormItem label="价格" v-if="orderForm.orderType === 'LIMIT'">
              <Input v-model.number="orderForm.price" type="number" placeholder="0.00" />
            </FormItem>
            <Button
              type="primary"
              style="width: 100%"
              @click="handleSubmitOrder"
              :disabled="!orderForm.symbol"
            >
              {{ orderForm.side === 'BUY' ? '买入' : '卖出' }} {{ orderForm.symbol }}
            </Button>
          </Form>
        </Card>
      </Col>

      <!-- 右侧：持仓和订单 -->
      <Col :span="16">
        <Card shadow="never">
          <Tabs>
            <TabPane label="当前持仓">
              <Table :data="positions" stripe>
                <el-table-column prop="symbol" label="证券代码" width="120" />
                <el-table-column prop="side" label="方向" width="80" align="center">
                  <template #default="{ row }">
                    <Tag :type="row.side === 'LONG' ? 'success' : 'danger'">
                      {{ row.side === 'LONG' ? '多' : '空' }}
                    </Tag>
                  </template>
                </el-table-column>
                <el-table-column prop="quantity" label="数量" width="80" align="right" />
                <el-table-column prop="avgPrice" label="成本价" width="100" align="right" />
                <el-table-column prop="currentPrice" label="现价" width="100" align="right" />
                <el-table-column prop="pnl" label="盈亏" width="100" align="right">
                  <template #default="{ row }">
                    <span :class="row.pnl >= 0 ? 'text-green-500' : 'text-red-500'">
                      {{ row.pnl >= 0 ? '+' : '' }}¥{{ row.pnl.toLocaleString() }}
                    </span>
                  </template>
                </el-table-column>
                <el-table-column prop="pnlPercent" label="盈亏%" width="90" align="right">
                  <template #default="{ row }">
                    <span :class="row.pnlPercent >= 0 ? 'text-green-500' : 'text-red-500'">
                      {{ row.pnlPercent >= 0 ? '+' : '' }}{{ row.pnlPercent }}%
                    </span>
                  </template>
                </el-table-column>
              </Table>
            </TabPane>
            <TabPane label="委托记录">
              <Table :data="orders" stripe>
                <el-table-column prop="id" label="委托编号" width="100" />
                <el-table-column prop="symbol" label="证券代码" width="120" />
                <el-table-column prop="side" label="方向" width="70" align="center">
                  <template #default="{ row }">
                    <Tag :type="getSideType(row.side)">{{ row.side === 'BUY' ? '买' : '卖' }}</Tag>
                  </template>
                </el-table-column>
                <el-table-column prop="orderType" label="类型" width="70" align="center" />
                <el-table-column prop="quantity" label="数量" width="70" align="right" />
                <el-table-column prop="price" label="价格" width="90" align="right" />
                <el-table-column prop="status" label="状态" width="80" align="center">
                  <template #default="{ row }">
                    <Tag :type="getStatusType(row.status)">{{ row.status }}</Tag>
                  </template>
                </el-table-column>
                <el-table-column label="操作" width="80" fixed="right">
                  <template #default="{ row }">
                    <Button
                      v-if="row.status === 'PENDING'"
                      link
                      type="danger"
                      size="small"
                      @click="handleCancelOrder(row)"
                    >
                      撤单
                    </Button>
                  </template>
                </el-table-column>
              </Table>
            </TabPane>
          </Tabs>
        </Card>
      </Col>
    </Row>
  </div>
</template>

<style scoped>
.trading-page {
  min-height: 100%;
}
</style>
