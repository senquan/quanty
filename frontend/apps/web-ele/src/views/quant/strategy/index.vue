<script lang="ts" setup>
import { ref } from 'vue';
import { useRouter } from 'vue-router';

import {
  ElButton,
  ElCard,
  ElCol,
  ElInput,
  ElRow,
  ElSelect,
  ElTable,
  ElTableColumn,
  ElTag,
  ElOption,
} from 'element-plus';

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
    <ElCard shadow="never">
      <!-- 搜索和操作栏 -->
      <template #header>
        <ElRow justify="space-between" align="middle">
          <ElCol>
            <ElRow :gutter="12">
              <ElCol>
                <ElInput
                  v-model="searchKeyword"
                  placeholder="搜索策略名称..."
                  clearable
                  style="width: 240px"
                >
                  <template #prefix>
                    <Search class="w-4 h-4" />
                  </template>
                </ElInput>
              </ElCol>
              <ElCol>
                <ElSelect v-model="statusFilter" placeholder="状态筛选" clearable style="width: 140px">
                  <ElOption label="运行中" value="running" />
                  <ElOption label="已暂停" value="paused" />
                  <ElOption label="回测中" value="backtesting" />
                  <ElOption label="草稿" value="draft" />
                </ElSelect>
              </ElCol>
            </ElRow>
          </ElCol>
          <ElCol>
            <ElButton type="primary" @click="handleCreate">
              <Plus class="w-4 h-4 mr-1" />
              新建策略
            </ElButton>
          </ElCol>
        </ElRow>
      </template>

      <!-- 策略表格 -->
      <ElTable :data="strategies" stripe>
        <ElTableColumn prop="name" label="策略名称" min-width="180">
          <template #default="{ row }">
            <div>
              <div class="font-medium">{{ row.name }}</div>
              <div class="text-xs text-gray-400 mt-1">{{ row.description }}</div>
            </div>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="status" label="状态" width="100" align="center">
          <template #default="{ row }">
            <ElTag :type="getStatusType(row.status)">{{ getStatusLabel(row.status) }}</ElTag>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="totalReturn" label="总收益率" width="100" align="right">
          <template #default="{ row }">
            <span :class="row.totalReturn >= 0 ? 'text-green-500' : 'text-red-500'">
              {{ row.totalReturn >= 0 ? '+' : '' }}{{ row.totalReturn }}%
            </span>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="sharpeRatio" label="夏普比率" width="100" align="right" />
        <ElTableColumn prop="maxDrawdown" label="最大回撤" width="100" align="right">
          <template #default="{ row }">
            <span class="text-red-500">{{ row.maxDrawdown }}%</span>
          </template>
        </ElTableColumn>
        <ElTableColumn prop="winRate" label="胜率" width="80" align="right">
          <template #default="{ row }">{{ row.winRate }}%</template>
        </ElTableColumn>
        <ElTableColumn prop="updatedAt" label="更新时间" width="110" />
        <ElTableColumn label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <ElButton link type="primary" size="small" @click="handleEdit(row)">
              <Edit class="w-4 h-4 mr-1" />编辑
            </ElButton>
            <ElButton link type="success" size="small" @click="handleRun(row)" v-if="row.status !== 'running'">
              <Play class="w-4 h-4 mr-1" />运行
            </ElButton>
            <ElButton link type="warning" size="small" @click="handlePause(row)" v-if="row.status === 'running'">
              <Pause class="w-4 h-4 mr-1" />暂停
            </ElButton>
            <ElButton link type="danger" size="small" @click="handleDelete(row)">
              <Trash2 class="w-4 h-4" />
            </ElButton>
          </template>
        </ElTableColumn>
      </ElTable>
    </ElCard>
  </div>
</template>

<style scoped>
.strategy-list {
  min-height: 100%;
}
</style>
