<script lang="ts" setup>
import { ref, reactive, computed } from 'vue';
import { useRouter, useRoute } from 'vue-router';

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
} from 'element-plus';

import { Save, ArrowLeft, Code, Settings, Info } from '@lucide/vue';
import MonacoEditor from '#/components/MonacoEditor/index.vue';

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
const monacoEditorRef = ref<InstanceType<typeof MonacoEditor> | null>(null);

const handleBack = () => {
  router.push('/quant/strategy');
};

const handleSave = () => {
  console.log('保存策略:', form);
  ElMessage.success('策略已保存');
};

const handleValidate = () => {
  const code = form.code;
  // Basic Python syntax validation
  const errors: string[] = [];

  // Check for unbalanced parentheses
  let parenCount = 0;
  let bracketCount = 0;
  let braceCount = 0;
  for (const char of code) {
    if (char === '(') parenCount++;
    if (char === ')') parenCount--;
    if (char === '[') bracketCount++;
    if (char === ']') bracketCount--;
    if (char === '{') braceCount++;
    if (char === '}') braceCount--;
  }
  if (parenCount !== 0) errors.push('括号不匹配');
  if (bracketCount !== 0) errors.push('方括号不匹配');
  if (braceCount !== 0) errors.push('花括号不匹配');

  // Check for on_data function
  if (!code.includes('def on_data')) {
    errors.push('缺少 on_data 函数');
  }

  if (errors.length > 0) {
    ElMessage.error(`验证失败: ${errors.join(', ')}`);
  } else {
    ElMessage.success('代码语法验证通过');
  }
};

const handleApplyTemplate = (template: (typeof strategyTemplates)[0]) => {
  form.code = template.code;
  ElMessage.success(`已应用模板: ${template.name}`);
};
</script>

<template>
  <div class="strategy-edit p-4">
    <!-- 顶部操作栏 -->
    <ElRow justify="space-between" align="middle" class="mb-4">
      <ElCol>
        <ElButton @click="handleBack">
          <ArrowLeft class="w-4 h-4 mr-1" />
          返回列表
        </ElButton>
      </ElCol>
      <ElCol>
        <ElRow :gutter="8">
          <ElCol>
            <ElButton @click="handleValidate">
              <Code class="w-4 h-4 mr-1" />
              验证代码
            </ElButton>
          </ElCol>
          <ElCol>
            <ElButton type="primary" @click="handleSave">
              <Save class="w-4 h-4 mr-1" />
              保存策略
            </ElButton>
          </ElCol>
        </ElRow>
      </ElCol>
    </ElRow>

    <ElRow :gutter="16">
      <!-- 左侧：基本信息和配置 -->
      <ElCol :span="6">
        <ElCard shadow="never" class="mb-4">
          <template #header>
            <ElRow align="middle" :gutter="4">
              <ElCol><Info class="w-4 h-4" /></ElCol>
              <ElCol>基本信息</ElCol>
            </ElRow>
          </template>
          <ElForm :model="form" label-position="top">
            <ElFormItem label="策略名称" required>
              <ElInput v-model="form.name" placeholder="请输入策略名称" />
            </ElFormItem>
            <ElFormItem label="策略描述">
              <ElInput
                v-model="form.description"
                type="textarea"
                :rows="3"
                placeholder="请输入策略描述"
              />
            </ElFormItem>
          </ElForm>
        </ElCard>

        <ElCard shadow="never" class="mb-4">
          <template #header>
            <ElRow align="middle" :gutter="4">
              <ElCol><Settings class="w-4 h-4" /></ElCol>
              <ElCol>回测配置</ElCol>
            </ElRow>
          </template>
          <ElForm :model="form" label-position="top">
            <ElFormItem label="数据源">
              <ElSelect v-model="form.dataSource" style="width: 100%">
                <ElOption label="Yahoo Finance" value="yahoo" />
                <ElOption label="CCXT (加密货币)" value="ccxt" />
              </ElSelect>
            </ElFormItem>
            <ElFormItem label="标的代码">
              <ElInput v-model="form.symbol" placeholder="如: AAPL, BTC/USDT" />
            </ElFormItem>
            <ElFormItem label="时间周期">
              <ElSelect v-model="form.timeframe" style="width: 100%">
                <ElOption label="1分钟" value="1m" />
                <ElOption label="5分钟" value="5m" />
                <ElOption label="15分钟" value="15m" />
                <ElOption label="1小时" value="1h" />
                <ElOption label="1天" value="1d" />
                <ElOption label="1周" value="1w" />
              </ElSelect>
            </ElFormItem>
          </ElForm>
        </ElCard>

        <ElCard shadow="never">
          <template #header>策略模板</template>
          <div class="space-y-2">
            <ElButton
              v-for="template in strategyTemplates"
              :key="template.name"
              link
              type="primary"
              class="block w-full text-left"
              @click="handleApplyTemplate(template)"
            >
              {{ template.name }}
            </ElButton>
          </div>
        </ElCard>
      </ElCol>

      <!-- 右侧：代码编辑器 -->
      <ElCol :span="18">
        <ElCard shadow="never">
          <ElTabs v-model="activeTab">
            <ElTabPane label="策略代码" name="code">
              <MonacoEditor
                ref="monacoEditorRef"
                v-model="form.code"
                language="python"
                theme="vs-dark"
                height="600px"
                :minimap="true"
                :font-size="14"
                word-wrap="on"
              />
            </ElTabPane>
            <ElTabPane label="回测结果" name="result">
              <div class="h-96 flex items-center justify-center text-gray-400">
                请先运行回测以查看结果
              </div>
            </ElTabPane>
          </ElTabs>
        </ElCard>
      </ElCol>
    </ElRow>
  </div>
</template>

<style scoped>
.strategy-edit {
  min-height: 100%;
}
</style>
