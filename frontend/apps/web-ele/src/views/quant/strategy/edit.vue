<script lang="ts" setup>
import { ref, reactive, computed } from 'vue';
import { useRouter, useRoute } from 'vue-router';
import { Card, Form, FormItem, Input, Select, Button, Row, Col, Tabs, TabPane, Message } from 'element-plus';
import { Save, Play, ArrowLeft, Code, Settings, Info } from 'lucide-vue-next';

const router = useRouter();
const route = useRoute();

const isEdit = computed(() => !!route.query.id);

interface StrategyForm {
  name: string;
  description: string;
  code: string;
  dataSource: string;
  symbol: string;
  timeframe: string;
}

const form = reactive<StrategyForm>({
  name: '',
  description: '',
  code: `# 策略代码模板
# 使用 data 访问行情数据，context 访问账户信息

def on_data(data, context):
    """
    data: 包含 open, high, low, close, volume 等字段
    context: 包含账户信息、持仓信息等
    """
    close = data['close']
    
    # 在这里编写您的交易逻辑
    # buy(price, quantity) - 买入
    # sell(price, quantity) - 卖出
    # get_position() - 获取当前持仓
    
    pass
`,
  dataSource: 'yahoo',
  symbol: 'AAPL',
  timeframe: '1d',
});

const strategyTemplates = [
  {
    name: '双均线交叉策略',
    code: `# 双均线交叉策略
def on_data(data, context):
    import pandas as pd
    
    close = data['close']
    sma_20 = close.rolling(window=20).mean()
    sma_50 = close.rolling(window=50).mean()
    
    for i in range(50, len(close)):
        # 金叉买入
        if sma_20.iloc[i-1] <= sma_50.iloc[i-1] and sma_20.iloc[i] > sma_50.iloc[i]:
            buy(close.iloc[i])
        
        # 死叉卖出
        elif sma_20.iloc[i-1] >= sma_50.iloc[i-1] and sma_20.iloc[i] < sma_50.iloc[i]:
            position = get_position()
            if position > 0:
                sell(close.iloc[i], position)
`,
  },
  {
    name: 'RSI均值回归策略',
    code: `# RSI均值回归策略
def on_data(data, context):
    close = data['close']
    
    # 计算RSI
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    for i in range(14, len(close)):
        # RSI < 30 超卖，买入
        if rsi.iloc[i] < 30 and rsi.iloc[i-1] >= 30:
            buy(close.iloc[i])
        
        # RSI > 70 超买，卖出
        elif rsi.iloc[i] > 70 and rsi.iloc[i-1] <= 70:
            position = get_position()
            if position > 0:
                sell(close.iloc[i], position)
`,
  },
];

const activeTab = ref('code');

const handleBack = () => {
  router.push('/quant/strategy');
};

const handleSave = () => {
  console.log('保存策略:', form);
  Message.success('策略已保存');
};

const handleValidate = () => {
  console.log('验证策略代码...');
  Message.info('正在验证策略代码...');
};

const handleApplyTemplate = (template: typeof strategyTemplates[0]) => {
  form.code = template.code;
  Message.success(`已应用模板: ${template.name}`);
};
</script>

<template>
  <div class="strategy-edit p-4">
    <!-- 顶部操作栏 -->
    <Row justify="space-between" align="middle" class="mb-4">
      <Col>
        <Button @click="handleBack">
          <ArrowLeft class="w-4 h-4 mr-1" />
          返回列表
        </Button>
      </Col>
      <Col>
        <Row :gutter="8">
          <Col>
            <Button @click="handleValidate">
              <Code class="w-4 h-4 mr-1" />
              验证代码
            </Button>
          </Col>
          <Col>
            <Button type="primary" @click="handleSave">
              <Save class="w-4 h-4 mr-1" />
              保存策略
            </Button>
          </Col>
        </Row>
      </Col>
    </Row>

    <Row :gutter="16">
      <!-- 左侧：基本信息和配置 -->
      <Col :span="6">
        <Card shadow="never" class="mb-4">
          <template #header>
            <Row align="middle" :gutter="4">
              <Col><Info class="w-4 h-4" /></Col>
              <Col>基本信息</Col>
            </Row>
          </template>
          <Form :model="form" label-position="top">
            <FormItem label="策略名称" required>
              <Input v-model="form.name" placeholder="请输入策略名称" />
            </FormItem>
            <FormItem label="策略描述">
              <Input v-model="form.description" type="textarea" :rows="3" placeholder="请输入策略描述" />
            </FormItem>
          </Form>
        </Card>

        <Card shadow="never" class="mb-4">
          <template #header>
            <Row align="middle" :gutter="4">
              <Col><Settings class="w-4 h-4" /></Col>
              <Col>回测配置</Col>
            </Row>
          </template>
          <Form :model="form" label-position="top">
            <FormItem label="数据源">
              <Select v-model="form.dataSource" style="width: 100%">
                <el-option label="Yahoo Finance" value="yahoo" />
                <el-option label="CCXT (加密货币)" value="ccxt" />
              </Select>
            </FormItem>
            <FormItem label="标的代码">
              <Input v-model="form.symbol" placeholder="如: AAPL, BTC/USDT" />
            </FormItem>
            <FormItem label="时间周期">
              <Select v-model="form.timeframe" style="width: 100%">
                <el-option label="1分钟" value="1m" />
                <el-option label="5分钟" value="5m" />
                <el-option label="15分钟" value="15m" />
                <el-option label="1小时" value="1h" />
                <el-option label="1天" value="1d" />
                <el-option label="1周" value="1w" />
              </Select>
            </FormItem>
          </Form>
        </Card>

        <Card shadow="never">
          <template #header>策略模板</template>
          <div class="space-y-2">
            <Button
              v-for="template in strategyTemplates"
              :key="template.name"
              link
              type="primary"
              @click="handleApplyTemplate(template)"
              class="block w-full text-left"
            >
              {{ template.name }}
            </Button>
          </div>
        </Card>
      </Col>

      <!-- 右侧：代码编辑器 -->
      <Col :span="18">
        <Card shadow="never">
          <Tabs v-model="activeTab">
            <TabPane label="策略代码" name="code">
              <Input
                v-model="form.code"
                type="textarea"
                :rows="30"
                placeholder="请输入策略代码"
                class="code-editor"
                style="font-family: 'JetBrains Mono', 'Fira Code', monospace;"
              />
            </TabPane>
            <TabPane label="回测结果" name="result">
              <div class="h-96 flex items-center justify-center text-gray-400">
                请先运行回测以查看结果
              </div>
            </TabPane>
          </Tabs>
        </Card>
      </Col>
    </Row>
  </div>
</template>

<style scoped>
.strategy-edit {
  min-height: 100%;
}

.code-editor :deep(.el-textarea__inner) {
  font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
  font-size: 14px;
  line-height: 1.5;
  tab-size: 4;
}
</style>
