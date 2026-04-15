<script lang="ts" setup>
import { ref, onMounted } from 'vue';
import { Card, Col, Row, Statistic, Progress, Table, Tag } from 'element-plus';
import { TrendingUp, TrendingDown, BarChart3, Activity, Target, DollarSign } from 'lucide-vue-next';

interface StatItem {
  label: string;
  value: string | number;
  prefix?: string;
  suffix?: string;
  trend?: 'up' | 'down';
  color?: string;
}

const stats = ref<StatItem[]>([
  { label: '策略总数', value: 12, color: '#409eff' },
  { label: '总收益', value: '+15.6%', trend: 'up', color: '#67c23a' },
  { label: '夏普比率', value: 1.85, color: '#e6a23c' },
  { label: '最大回撤', value: '-8.2%', trend: 'down', color: '#f56c6c' },
]);

const recentStrategies = ref([
  { id: 1, name: '双均线策略', status: '运行中', return: '+12.3%', sharpe: 1.8 },
  { id: 2, name: 'RSI均值回归', status: '已暂停', return: '+8.7%', sharpe: 1.2 },
  { id: 3, name: '动量突破策略', status: '回测中', return: '-', sharpe: '-' },
]);

const getStatusType = (status: string) => {
  switch (status) {
    case '运行中': return 'success';
    case '已暂停': return 'warning';
    case '回测中': return 'info';
    default: return '';
  }
};

onMounted(() => {
  // 可以在此加载实际数据
});
</script>

<template>
  <div class="quant-dashboard p-4">
    <!-- 统计卡片 -->
    <Row :gutter="16" class="mb-4">
      <Col :span="6" v-for="stat in stats" :key="stat.label">
        <Card shadow="hover">
          <Statistic
            :value="stat.value"
            :title="stat.label"
            :value-style="{ color: stat.color }"
          >
            <template #prefix>
              <TrendingUp v-if="stat.trend === 'up'" class="w-4 h-4" />
              <TrendingDown v-if="stat.trend === 'down'" class="w-4 h-4" />
            </template>
          </Statistic>
        </Card>
      </Col>
    </Row>

    <!-- 图表区域 -->
    <Row :gutter="16" class="mb-4">
      <Col :span="16">
        <Card shadow="hover" header="净值曲线">
          <div class="h-64 flex items-center justify-center text-gray-400">
            📈 图表区域（集成 ECharts）
          </div>
        </Card>
      </Col>
      <Col :span="8">
        <Card shadow="hover" header="收益分布">
          <div class="h-64 flex items-center justify-center text-gray-400">
            📊 直方图区域
          </div>
        </Card>
      </Col>
    </Row>

    <!-- 最近策略 -->
    <Card shadow="hover" header="最近策略">
      <el-table :data="recentStrategies" stripe>
        <el-table-column prop="name" label="策略名称" width="180" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <Tag :type="getStatusType(row.status)">{{ row.status }}</Tag>
          </template>
        </el-table-column>
        <el-table-column prop="return" label="收益率" width="100" />
        <el-table-column prop="sharpe" label="夏普比率" width="100" />
        <el-table-column label="操作" width="150">
          <template #default>
            <el-button link type="primary">查看</el-button>
            <el-button link type="primary">编辑</el-button>
          </template>
        </el-table-column>
      </el-table>
    </Card>
  </div>
</template>

<style scoped>
.quant-dashboard {
  min-height: 100%;
}
</style>
