<!-- 量化策略管理页面 -->
<template>
  <div class="quant-strategy-container">
    <!-- 页面头部 -->
    <div class="page-header">
      <div class="header-left">
        <h2>量化策略管理</h2>
        <p>创建、管理和测试您的交易策略</p>
      </div>
      <div class="header-right">
        <el-button type="primary" @click="showCreateDialog = true">
          <el-icon><Plus /></el-icon>
          创建策略
        </el-button>
      </div>
    </div>

    <!-- 策略列表 -->
    <el-card class="strategy-list-card">
      <template #header>
        <div class="card-header">
          <span>我的策略</span>
          <div class="header-actions">
            <el-input
              v-model="searchText"
              placeholder="搜索策略..."
              style="width: 200px"
              clearable
            >
              <template #prefix>
                <el-icon><Search /></el-icon>
              </template>
            </el-input>
            <el-button @click="loadStrategies">
              <el-icon><Refresh /></el-icon>
              刷新
            </el-button>
          </div>
        </div>
      </template>

      <el-table
        :data="filteredStrategies"
        v-loading="loading"
        style="width: 100%"
      >
        <el-table-column prop="name" label="策略名称" min-width="150">
          <template #default="{ row }">
            <div class="strategy-name">
              <span class="name">{{ row.name }}</span>
              <el-tag v-if="row.description" size="small" type="info">
                {{ row.description.substring(0, 20) }}...
              </el-tag>
            </div>
          </template>
        </el-table-column>
        
        <el-table-column prop="created_at" label="创建时间" width="180">
          <template #default="{ row }">
            {{ formatDate(row.created_at) }}
          </template>
        </el-table-column>
        
        <el-table-column label="性能指标" width="200">
          <template #default="{ row }">
            <div class="metrics-summary" v-if="row.latest_backtest">
              <div class="metric-item">
                <span class="label">收益率:</span>
                <span class="value" :class="getReturnClass(row.latest_backtest.total_return)">
                  {{ row.latest_backtest.total_return }}%
                </span>
              </div>
              <div class="metric-item">
                <span class="label">夏普比率:</span>
                <span class="value">{{ row.latest_backtest.sharpe_ratio }}</span>
              </div>
            </div>
            <span v-else class="no-data">尚未回测</span>
          </template>
        </el-table-column>
        
        <el-table-column label="操作" width="250">
          <template #default="{ row }">
            <el-button size="small" @click="editStrategy(row)">编辑</el-button>
            <el-button size="small" type="primary" @click="runBacktest(row)">回测</el-button>
            <el-button size="small" type="success" @click="viewResults(row)">结果</el-button>
            <el-dropdown @command="(cmd) => handleAction(cmd, row)">
              <el-button size="small">
                更多<el-icon class="el-icon--right"><arrow-down /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="validate">验证代码</el-dropdown-item>
                  <el-dropdown-item command="duplicate">复制策略</el-dropdown-item>
                  <el-dropdown-item command="delete" divided>删除</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 创建/编辑策略对话框 -->
    <el-dialog
      v-model="showCreateDialog"
      :title="editingStrategy ? '编辑策略' : '创建策略'"
      width="80%"
      class="strategy-dialog"
    >
      <el-form :model="strategyForm" :rules="strategyRules" ref="strategyFormRef">
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="策略名称" prop="name">
              <el-input v-model="strategyForm.name" placeholder="输入策略名称" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="策略描述" prop="description">
              <el-input
                v-model="strategyForm.description"
                placeholder="简单描述策略逻辑"
              />
            </el-form-item>
          </el-col>
        </el-row>
        
        <el-form-item label="策略代码" prop="code">
          <div class="code-editor-container">
            <el-button-group class="editor-toolbar">
              <el-button size="small" @click="insertTemplate('ma')">均线策略模板</el-button>
              <el-button size="small" @click="insertTemplate('rsi')">RSI策略模板</el-button>
              <el-button size="small" @click="insertTemplate('macd')">MACD策略模板</el-button>
              <el-button size="small" @click="validateCurrentCode()">验证代码</el-button>
            </el-button-group>
            <el-input
              v-model="strategyForm.code"
              type="textarea"
              :rows="20"
              placeholder="在此输入您的策略代码..."
              class="code-editor"
            />
          </div>
        </el-form-item>
      </el-form>
      
      <template #footer>
        <span class="dialog-footer">
          <el-button @click="showCreateDialog = false">取消</el-button>
          <el-button type="primary" @click="saveStrategy" :loading="saving">
            {{ editingStrategy ? '更新' : '创建' }}
          </el-button>
        </span>
      </template>
    </el-dialog>

    <!-- 回测配置对话框 -->
    <el-dialog v-model="showBacktestDialog" title="策略回测" width="60%">
      <el-form :model="backtestForm" :rules="backtestRules" ref="backtestFormRef">
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="交易标的" prop="symbol">
              <el-select v-model="backtestForm.symbol" filterable>
                <el-option label="苹果公司 (AAPL)" value="AAPL" />
                <el-option label="微软 (MSFT)" value="MSFT" />
                <el-option label="谷歌 (GOOGL)" value="GOOGL" />
                <el-option label="亚马逊 (AMZN)" value="AMZN" />
                <el-option label="特斯拉 (TSLA)" value="TSLA" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="数据源" prop="data_source">
              <el-select v-model="backtestForm.data_source">
                <el-option label="Yahoo Finance" value="yahoo" />
                <el-option label="加密货币 (Binance)" value="crypto" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        
        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item label="开始日期" prop="start_date">
              <el-date-picker
                v-model="backtestForm.start_date"
                type="date"
                placeholder="选择开始日期"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="结束日期" prop="end_date">
              <el-date-picker
                v-model="backtestForm.end_date"
                type="date"
                placeholder="选择结束日期"
                style="width: 100%"
              />
            </el-form-item>
          </el-col>
        </el-row>
        
        <el-form-item label="初始资金" prop="initial_capital">
          <el-input-number
            v-model="backtestForm.initial_capital"
            :min="10000"
            :max="10000000"
            :step="10000"
            style="width: 100%"
          />
        </el-form-item>
      </el-form>
      
      <template #footer>
        <span class="dialog-footer">
          <el-button @click="showBacktestDialog = false">取消</el-button>
          <el-button type="primary" @click="executeBacktest" :loading="backtesting">
            开始回测
          </el-button>
        </span>
      </template>
    </el-dialog>

    <!-- 回测结果页面 -->
    <BacktestResults
      v-if="showResultsDialog"
      :strategy="selectedStrategy"
      :results="backtestResults"
      @close="showResultsDialog = false"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search, Refresh, ArrowDown } from '@element-plus/icons-vue'
import { quantApi } from '@/api/quant'
import BacktestResults from './components/BacktestResults.vue'

defineOptions({ name: 'QuantStrategy' })

// 数据状态
const strategies = ref([])
const loading = ref(false)
const searchText = ref('')
const saving = ref(false)
const backtesting = ref(false)

// 对话框状态
const showCreateDialog = ref(false)
const showBacktestDialog = ref(false)
const showResultsDialog = ref(false)
const editingStrategy = ref(null)
const selectedStrategy = ref(null)

// 表单数据
const strategyForm = ref({
  name: '',
  description: '',
  code: ''
})

const backtestForm = ref({
  strategy_id: null,
  symbol: 'AAPL',
  data_source: 'yahoo',
  start_date: null,
  end_date: null,
  initial_capital: 100000
})

const backtestResults = ref(null)

// 表单引用
const strategyFormRef = ref(null)
const backtestFormRef = ref(null)

// 表单验证规则
const strategyRules = {
  name: [{ required: true, message: '请输入策略名称', trigger: 'blur' }],
  code: [{ required: true, message: '请输入策略代码', trigger: 'blur' }]
}

const backtestRules = {
  symbol: [{ required: true, message: '请选择交易标的', trigger: 'change' }],
  start_date: [{ required: true, message: '请选择开始日期', trigger: 'change' }],
  end_date: [{ required: true, message: '请选择结束日期', trigger: 'change' }],
  initial_capital: [{ required: true, message: '请输入初始资金', trigger: 'blur' }]
}

// 计算属性
const filteredStrategies = computed(() => {
  if (!searchText.value) return strategies.value
  return strategies.value.filter(strategy =>
    strategy.name.toLowerCase().includes(searchText.value.toLowerCase()) ||
    strategy.description?.toLowerCase().includes(searchText.value.toLowerCase())
  )
})

// 方法
const loadStrategies = async () => {
  try {
    loading.value = true
    const response = await quantApi.getStrategies()
    strategies.value = response.data
  } catch (error) {
    ElMessage.error('加载策略列表失败')
  } finally {
    loading.value = false
  }
}

const editStrategy = (strategy) => {
  editingStrategy.value = strategy
  strategyForm.value = {
    name: strategy.name,
    description: strategy.description || '',
    code: strategy.code
  }
  showCreateDialog.value = true
}

const saveStrategy = async () => {
  try {
    const valid = await strategyFormRef.value.validate()
    if (!valid) return
    
    saving.value = true
    if (editingStrategy.value) {
      await quantApi.updateStrategy(editingStrategy.value.id, strategyForm.value)
      ElMessage.success('策略更新成功')
    } else {
      await quantApi.createStrategy(strategyForm.value)
      ElMessage.success('策略创建成功')
    }
    
    showCreateDialog.value = false
    resetStrategyForm()
    loadStrategies()
  } catch (error) {
    ElMessage.error('保存策略失败')
  } finally {
    saving.value = false
  }
}

const runBacktest = (strategy) => {
  selectedStrategy.value = strategy
  backtestForm.value.strategy_id = strategy.id
  // 设置默认日期范围
  const end = new Date()
  const start = new Date()
  start.setFullYear(start.getFullYear() - 1)
  backtestForm.value.start_date = start
  backtestForm.value.end_date = end
  showBacktestDialog.value = true
}

const executeBacktest = async () => {
  try {
    const valid = await backtestFormRef.value.validate()
    if (!valid) return
    
    backtesting.value = true
    const response = await quantApi.runBacktest(backtestForm.value)
    backtestResults.value = response.data
    
    showBacktestDialog.value = false
    showResultsDialog.value = true
    
    ElMessage.success('回测完成')
  } catch (error) {
    ElMessage.error('回测执行失败')
  } finally {
    backtesting.value = false
  }
}

const viewResults = (strategy) => {
  selectedStrategy.value = strategy
  // 这里可以加载历史回测结果
  showResultsDialog.value = true
}

const handleAction = async (command, strategy) => {
  switch (command) {
    case 'validate':
      await validateStrategy(strategy.code)
      break
    case 'duplicate':
      duplicateStrategy(strategy)
      break
    case 'delete':
      deleteStrategy(strategy)
      break
  }
}

const validateStrategy = async (code) => {
  try {
    const response = await quantApi.validateStrategy(code)
    if (response.data.valid) {
      ElMessage.success('代码验证通过')
    } else {
      ElMessage.warning(`代码验证失败: ${response.data.errors.join(', ')}`)
    }
  } catch (error) {
    ElMessage.error('代码验证失败')
  }
}

const duplicateStrategy = (strategy) => {
  editingStrategy.value = null
  strategyForm.value = {
    name: `${strategy.name} - 副本`,
    description: strategy.description,
    code: strategy.code
  }
  showCreateDialog.value = true
}

const deleteStrategy = async (strategy) => {
  try {
    await ElMessageBox.confirm(
      `确定要删除策略 "${strategy.name}" 吗？此操作不可恢复。`,
      '确认删除',
      {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning'
      }
    )
    
    await quantApi.deleteStrategy(strategy.id)
    ElMessage.success('策略删除成功')
    loadStrategies()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除策略失败')
    }
  }
}

const insertTemplate = (type) => {
  const templates = {
    ma: `# 均线策略模板
import numpy as np

# 获取技术指标
sma_20 = data['sma_20']
sma_50 = data['sma_50']
close = data['close']

# 生成交易信号
for i in range(50, len(data)):
    current_price = close.iloc[i]
    
    # 金叉买入信号
    if sma_20.iloc[i-1] <= sma_50.iloc[i-1] and sma_20.iloc[i] > sma_50.iloc[i]:
        buy(current_price)
    
    # 死叉卖出信号
    elif sma_20.iloc[i-1] >= sma_50.iloc[i-1] and sma_20.iloc[i] < sma_50.iloc[i]:
        position = get_position()
        if position > 0:
            sell(current_price)`,

    rsi: `# RSI策略模板
import numpy as np

# 获取技术指标
rsi = data['rsi']
close = data['close']

# 生成交易信号
for i in range(20, len(data)):
    current_price = close.iloc[i]
    current_rsi = rsi.iloc[i]
    
    # RSI超卖买入信号
    if current_rsi < 30 and rsi.iloc[i-1] >= 30:
        buy(current_price)
    
    # RSI超买卖出信号
    elif current_rsi > 70 and rsi.iloc[i-1] <= 70:
        position = get_position()
        if position > 0:
            sell(current_price)`,

    macd: `# MACD策略模板
import numpy as np

# 获取技术指标
macd = data['macd']
macd_signal = data['macd_signal']
close = data['close']

# 生成交易信号
for i in range(10, len(data)):
    current_price = close.iloc[i]
    
    # MACD金叉买入
    if macd.iloc[i-1] <= macd_signal.iloc[i-1] and macd.iloc[i] > macd_signal.iloc[i]:
        buy(current_price)
    
    # MACD死叉卖出
    elif macd.iloc[i-1] >= macd_signal.iloc[i-1] and macd.iloc[i] < macd_signal.iloc[i]:
        position = get_position()
        if position > 0:
            sell(current_price)`
  }
  
  strategyForm.value.code = templates[type] || ''
}

const validateCurrentCode = async () => {
  if (!strategyForm.value.code) {
    ElMessage.warning('请先输入策略代码')
    return
  }
  
  await validateStrategy(strategyForm.value.code)
}

const resetStrategyForm = () => {
  strategyForm.value = {
    name: '',
    description: '',
    code: ''
  }
  editingStrategy.value = null
}

const formatDate = (dateString) => {
  return new Date(dateString).toLocaleDateString('zh-CN')
}

const getReturnClass = (returnValue) => {
  return returnValue >= 0 ? 'positive-return' : 'negative-return'
}

// 生命周期
onMounted(() => {
  loadStrategies()
})
</script>

<style lang="scss" scoped>
.quant-strategy-container {
  .page-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 24px;
    
    .header-left {
      h2 {
        margin: 0 0 8px 0;
        font-size: 24px;
        color: #1f2937;
      }
      
      p {
        margin: 0;
        color: #6b7280;
      }
    }
  }
  
  .strategy-list-card {
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      
      .header-actions {
        display: flex;
        gap: 12px;
        align-items: center;
      }
    }
    
    .strategy-name {
      .name {
        font-weight: 600;
        margin-right: 8px;
      }
    }
    
    .metrics-summary {
      .metric-item {
        display: flex;
        justify-content: space-between;
        margin-bottom: 4px;
        
        .label {
          font-size: 12px;
          color: #6b7280;
        }
        
        .value {
          font-weight: 600;
          
          &.positive-return {
            color: #10b981;
          }
          
          &.negative-return {
            color: #ef4444;
          }
        }
      }
    }
    
    .no-data {
      color: #9ca3af;
      font-size: 12px;
    }
  }
  
  .strategy-dialog {
    .code-editor-container {
      .editor-toolbar {
        margin-bottom: 12px;
      }
      
      .code-editor {
        :deep(.el-textarea__inner) {
          font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
          font-size: 13px;
          line-height: 1.5;
        }
      }
    }
  }
}
</style>