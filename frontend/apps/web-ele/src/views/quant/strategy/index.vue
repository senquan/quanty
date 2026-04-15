<script lang="ts" setup>
import { ref, reactive } from 'vue';
import { useRouter } from 'vue-router';
import { Card, Table, Tag, Button, Input, Select, Row, Col } from 'element-plus';
import { Plus, Search, Play, Pause, Trash2, Edit } from 'lucide-vue-next';

const router = useRouter();

interface Strategy {
  id: number;
  name: string;
  description: string;
  status: 'running' | 'paused' | 'backtesting' | 'draft';
  totalReturn: number;
  sharpeRatio: number;
  maxDrawdown: number;
  winRate: number;
  updatedAt: string;
}

const searchKeyword = ref('');
const statusFilter = ref('');

const strategies = ref<Strategy[]>([
  {
    id: 1,
    name: '双均线交叉策略',
    description: '基于20日和50日移动平均线的交叉信号',
    status: 'running',
    totalReturn: 15.6,
    sharpeRatio: 1.85,
    maxDrawdown: -8.2,
    winRate: 62.5,
    updatedAt: '2026-04-14',
  },
  {
    id: 2,
    name: 'RSI均值回归策略',
    description: '利用RSI超买超卖信号进行反转交易',
    status: 'paused',
    totalReturn: 8.7,
    sharpeRatio: 1.23,
    maxDrawdown: -12.1,
    winRate: 58.3,
    updatedAt: '2026-04-13',
  },
  {
    id: 3,
    name: '动量突破策略',
    description: '基于价格突破关键阻力位的动量交易',
    status: 'backtesting',
    totalReturn: 0,
    sharpeRatio: 0,
    maxDrawdown: 0,
    winRate: 0,
    updatedAt: '2026-04-14',
  },
]);

const getStatusType = (status: string) => {
  const map: Record<string, string> = {
    running: 'success',
    paused: 'warning',
    backtesting: 'info',
    draft: '',
  };
  return map[status] || '';
};

const getStatusLabel = (status: string) => {
  const map: Record<string, string> = {
    running: '运行中',
    paused: '已暂停',
    backtesting: '回测中',
    draft: '草稿',
  };
  return map[status] || status;
};

const handleCreate = () => {
  router.push('/quant/strategy/edit');
};

const handleEdit = (row: Strategy) => {
  router.push({ path: '/quant/strategy/edit', query: { id: String(row.id) } });
};

const handleRun = (row: Strategy) => {
  console.log('运行策略:', row.name);
};

const handlePause = (row: Strategy) => {
  console.log('暂停策略:', row.name);
};

const handleDelete = (row: Strategy) => {
  console.log('删除策略:', row.name);
};
</script>

<template>
  <div class="strategy-list p-4">
    <Card shadow="never">
      <!-- 搜索和操作栏 -->
      <template #header>
        <Row justify="space-between" align="middle">
          <Col>
            <Row :gutter="12">
              <Col>
                <Input
                  v-model="searchKeyword"
                  placeholder="搜索策略名称..."
                  clearable
                  style="width: 240px"
                >
                  <template #prefix>
                    <Search class="w-4 h-4" />
                  </template>
                </Input>
              </Col>
              <Col>
                <Select v-model="statusFilter" placeholder="状态筛选" clearable style="width: 140px">
                  <el-option label="运行中" value="running" />
                  <el-option label="已暂停" value="paused" />
                  <el-option label="回测中" value="backtesting" />
                  <el-option label="草稿" value="draft" />
                </Select>
              </Col>
            </Row>
          </Col>
          <Col>
            <Button type="primary" @click="handleCreate">
              <Plus class="w-4 h-4 mr-1" />
              新建策略
            </Button>
          </Col>
        </Row>
      </template>

      <!-- 策略表格 -->
      <Table :data="strategies" stripe>
        <el-table-column prop="name" label="策略名称" min-width="180">
          <template #default="{ row }">
            <div>
              <div class="font-medium">{{ row.name }}</div>
              <div class="text-xs text-gray-400 mt-1">{{ row.description }}</div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100" align="center">
          <template #default="{ row }">
            <Tag :type="getStatusType(row.status)">{{ getStatusLabel(row.status) }}</Tag>
          </template>
        </el-table-column>
        <el-table-column prop="totalReturn" label="总收益率" width="100" align="right">
          <template #default="{ row }">
            <span :class="row.totalReturn >= 0 ? 'text-green-500' : 'text-red-500'">
              {{ row.totalReturn >= 0 ? '+' : '' }}{{ row.totalReturn }}%
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="sharpeRatio" label="夏普比率" width="100" align="right" />
        <el-table-column prop="maxDrawdown" label="最大回撤" width="100" align="right">
          <template #default="{ row }">
            <span class="text-red-500">{{ row.maxDrawdown }}%</span>
          </template>
        </el-table-column>
        <el-table-column prop="winRate" label="胜率" width="80" align="right">
          <template #default="{ row }">{{ row.winRate }}%</template>
        </el-table-column>
        <el-table-column prop="updatedAt" label="更新时间" width="110" />
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <Button link type="primary" size="small" @click="handleEdit(row)">
              <Edit class="w-4 h-4 mr-1" />编辑
            </Button>
            <Button link type="success" size="small" @click="handleRun(row)" v-if="row.status !== 'running'">
              <Play class="w-4 h-4 mr-1" />运行
            </Button>
            <Button link type="warning" size="small" @click="handlePause(row)" v-if="row.status === 'running'">
              <Pause class="w-4 h-4 mr-1" />暂停
            </Button>
            <Button link type="danger" size="small" @click="handleDelete(row)">
              <Trash2 class="w-4 h-4" />
            </Button>
          </template>
        </el-table-column>
      </Table>
    </Card>
  </div>
</template>

<style scoped>
.strategy-list {
  min-height: 100%;
}
</style>
