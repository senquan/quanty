<script lang="ts" setup>
import { ref, reactive } from 'vue';
import { Card, Table, Tag, Row, Col, Tabs, TabPane, Select, Button, DatePicker } from 'element-plus';
import { Play, Download, RefreshCw } from 'lucide-vue-next';

interface BacktestRecord {
  id: number;
  strategyName: string;
  symbol: string;
  period: string;
  totalReturn: number;
  sharpeRatio: number;
  maxDrawdown: number;
  winRate: number;
  trades: number;
  status: 'completed' | 'running' | 'failed';
  createdAt: string;
}

const selectedStrategy = ref('');
const dateRange = ref<[string, string]>(['2023-01-01', '2023-12-31']);

const backtestHistory = ref<BacktestRecord[]>([
  {
    id: 1,
    strategyName: '双均线交叉策略',
    symbol: 'AAPL',
    period: '2023-01 ~ 2023-12',
    totalReturn: 15.6,
    sharpeRatio: 1.85,
    maxDrawdown: -8.2,
    winRate: 62.5,
    trades: 48,
    status: 'completed',
    createdAt: '2026-04-14 10:30',
  },
  {
    id: 2,
    strategyName: 'RSI均值回归策略',
    symbol: 'MSFT',
    period: '2023-06 ~ 2023-12',
    totalReturn: 8.7,
    sharpeRatio: 1.23,
    maxDrawdown: -12.1,
    winRate: 58.3,
    trades: 36,
    status: 'completed',
    createdAt: '2026-04-13 15:20',
  },
  {
    id: 3,
    strategyName: '动量突破策略',
    symbol: 'BTC/USDT',
    period: '2023-01 ~ 2023-12',
    totalReturn: 0,
    sharpeRatio: 0,
    maxDrawdown: 0,
    winRate: 0,
    trades: 0,
    status: 'running',
    createdAt: '2026-04-14 14:00',
  },
]);

const getStatusType = (status: string) => {
  const map: Record<string, string> = {
    completed: 'success',
    running: 'warning',
    failed: 'danger',
  };
  return map[status] || '';
};

const getStatusLabel = (status: string) => {
  const map: Record<string, string> = {
    completed: '已完成',
    running: '运行中',
    failed: '失败',
  };
  return map[status] || status;
};

const handleRunBacktest = () => {
  console.log('运行回测');
};

const handleExport = (row: BacktestRecord) => {
  console.log('导出结果:', row.id);
};
</script>

<template>
  <div class="backtest-page p-4">
    <!-- 顶部操作栏 -->
    <Card shadow="never" class="mb-4">
      <Row :gutter="12" align="middle">
        <Col>
          <Select v-model="selectedStrategy" placeholder="选择策略" clearable style="width: 200px">
            <el-option label="双均线交叉策略" value="1" />
            <el-option label="RSI均值回归策略" value="2" />
            <el-option label="动量突破策略" value="3" />
          </Select>
        </Col>
        <Col>
          <Select placeholder="数据源" style="width: 160px">
            <el-option label="Yahoo Finance" value="yahoo" />
            <el-option label="CCXT" value="ccxt" />
          </Select>
        </Col>
        <Col>
          <Input placeholder="标的代码" style="width: 140px" />
        </Col>
        <Col :flex="1" />
        <Col>
          <Button type="primary" @click="handleRunBacktest">
            <Play class="w-4 h-4 mr-1" />
            运行回测
          </Button>
        </Col>
      </Row>
    </Card>

    <!-- 回测结果 -->
    <Card shadow="never">
      <Tabs>
        <TabPane label="净值曲线">
          <div class="h-80 flex items-center justify-center text-gray-400 border-dashed border rounded">
            📈 ECharts 净值曲线图表区域
            <br />（集成后显示收益走势和回撤分析）
          </div>
        </TabPane>
        <TabPane label="收益分布">
          <div class="h-80 flex items-center justify-center text-gray-400 border-dashed border rounded">
            📊 ECharts 收益分布直方图
          </div>
        </TabPane>
        <TabPane label="详细指标">
          <Row :gutter="16">
            <Col :span="8">
              <Card header="收益指标" shadow="never" class="mb-3">
                <div class="space-y-2 text-sm">
                  <Row justify="space-between"><span>总收益率</span><span class="text-green-500">+15.6%</span></Row>
                  <Row justify="space-between"><span>年化收益</span><span class="text-green-500">+15.6%</span></Row>
                  <Row justify="space-between"><span>月均收益</span><span>+1.3%</span></Row>
                </div>
              </Card>
            </Col>
            <Col :span="8">
              <Card header="风险指标" shadow="never" class="mb-3">
                <div class="space-y-2 text-sm">
                  <Row justify="space-between"><span>波动率</span><span>12.3%</span></Row>
                  <Row justify="space-between"><span>最大回撤</span><span class="text-red-500">-8.2%</span></Row>
                  <Row justify="space-between"><span>VaR (95%)</span><span>-2.1%</span></Row>
                </div>
              </Card>
            </Col>
            <Col :span="8">
              <Card header="风险调整收益" shadow="never" class="mb-3">
                <div class="space-y-2 text-sm">
                  <Row justify="space-between"><span>夏普比率</span><span>1.85</span></Row>
                  <Row justify="space-between"><span>索提诺比率</span><span>2.4</span></Row>
                  <Row justify="space-between"><span>卡尔玛比率</span><span>1.9</span></Row>
                </div>
              </Card>
            </Col>
          </Row>
        </TabPane>
        <TabPane label="历史记录">
          <Table :data="backtestHistory" stripe>
            <el-table-column prop="strategyName" label="策略" width="160" />
            <el-table-column prop="symbol" label="标的" width="100" />
            <el-table-column prop="period" label="回测区间" width="160" />
            <el-table-column prop="totalReturn" label="总收益" width="90" align="right">
              <template #default="{ row }">
                <span :class="row.totalReturn >= 0 ? 'text-green-500' : 'text-red-500'">
                  {{ row.totalReturn >= 0 ? '+' : '' }}{{ row.totalReturn }}%
                </span>
              </template>
            </el-table-column>
            <el-table-column prop="sharpeRatio" label="夏普" width="70" align="right" />
            <el-table-column prop="maxDrawdown" label="回撤" width="80" align="right">
              <template #default="{ row }">
                <span class="text-red-500">{{ row.maxDrawdown }}%</span>
              </template>
            </el-table-column>
            <el-table-column prop="winRate" label="胜率" width="70" align="right">
              <template #default="{ row }">{{ row.winRate }}%</template>
            </el-table-column>
            <el-table-column prop="trades" label="交易次数" width="80" align="center" />
            <el-table-column prop="status" label="状态" width="80" align="center">
              <template #default="{ row }">
                <Tag :type="getStatusType(row.status)">{{ getStatusLabel(row.status) }}</Tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="100" fixed="right">
              <template #default="{ row }">
                <Button link type="primary" size="small" @click="handleExport(row)">
                  <Download class="w-4 h-4 mr-1" />导出
                </Button>
              </template>
            </el-table-column>
          </Table>
        </TabPane>
      </Tabs>
    </Card>
  </div>
</template>

<style scoped>
.backtest-page {
  min-height: 100%;
}
</style>
