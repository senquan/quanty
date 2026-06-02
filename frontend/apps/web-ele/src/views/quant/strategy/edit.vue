<script lang="ts" setup>
import { computed, onMounted, reactive, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';

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

import { ArrowLeft, Code, Info, Save, Settings } from '@lucide/vue';

import {
  createStrategyApi,
  getStrategyApi,
  updateStrategyApi,
  validateStrategyApi,
} from '#/api/quant';
import MonacoEditor from '#/components/MonacoEditor/index.vue';

const router = useRouter();
const route = useRoute();

const strategyId = computed(() => {
  const id = route.query.id;
  if (!id || Array.isArray(id)) return null;
  const parsed = Number(id);
  return Number.isFinite(parsed) ? parsed : null;
});

const isEdit = computed(() => strategyId.value !== null);
const pageLoading = ref(false);
const saving = ref(false);
const validating = ref(false);

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

async function loadStrategy() {
  if (!strategyId.value) return;
  pageLoading.value = true;
  try {
    const strategy = await getStrategyApi(strategyId.value);
    form.name = strategy.name;
    form.description = strategy.description ?? '';
    form.code = strategy.code;
  } catch {
    ElMessage.error('加载策略失败');
    router.push('/quant/strategy');
  } finally {
    pageLoading.value = false;
  }
}

onMounted(() => {
  if (strategyId.value) {
    loadStrategy();
  }
});

const handleBack = () => {
  router.push('/quant/strategy');
};

const handleSave = async () => {
  const name = form.name.trim();
  if (!name) {
    ElMessage.warning('请输入策略名称');
    return;
  }
  if (!form.code.trim()) {
    ElMessage.warning('请输入策略代码');
    return;
  }

  const payload = {
    name,
    description: form.description.trim(),
    code: form.code,
  };

  saving.value = true;
  try {
    if (isEdit.value && strategyId.value) {
      await updateStrategyApi(strategyId.value, payload);
      ElMessage.success('策略已更新');
    } else {
      await createStrategyApi(payload);
      ElMessage.success('策略已创建');
    }
    router.push('/quant/strategy');
  } catch {
    ElMessage.error(isEdit.value ? '更新失败' : '创建失败');
  } finally {
    saving.value = false;
  }
};

const handleValidate = async () => {
  if (!form.code.trim()) {
    ElMessage.warning('请先输入策略代码');
    return;
  }

  validating.value = true;
  try {
    const result = await validateStrategyApi(form.code);
    if (result.valid) {
      const warnMsg =
        result.warnings.length > 0 ? `（${result.warnings.join('；')}）` : '';
      ElMessage.success(`代码验证通过${warnMsg}`);
    } else {
      ElMessage.error(`验证失败：${result.errors.join('，')}`);
    }
  } catch {
    ElMessage.error('验证请求失败');
  } finally {
    validating.value = false;
  }
};

const handleApplyTemplate = (template: (typeof strategyTemplates)[0]) => {
  form.code = template.code;
  ElMessage.success(`已应用模板: ${template.name}`);
};
</script>

<template>
  <div v-loading="pageLoading" class="strategy-edit p-4">
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
            <ElButton :loading="validating" @click="handleValidate">
              <Code class="w-4 h-4 mr-1" />
              验证代码
            </ElButton>
          </ElCol>
          <ElCol>
            <ElButton type="primary" :loading="saving" @click="handleSave">
              <Save class="w-4 h-4 mr-1" />
              {{ isEdit ? '保存修改' : '创建策略' }}
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
