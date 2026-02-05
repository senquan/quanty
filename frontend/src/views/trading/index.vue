<!-- 模拟交易页面 -->
<template>
  <div class="trading-container">
    <!-- 页面头部 -->
    <div class="page-header">
      <div class="header-left">
        <h2>模拟交易</h2>
        <el-tag type="warning">模拟环境</el-tag>
      </div>
      <div class="header-right">
        <el-button @click="refreshData">
          <el-icon><Refresh /></el-icon>
          刷新数据
        </el-button>
      </div>
    </div>

    <!-- 账户概览 -->
    <el-row :gutter="20" class="account-overview">
      <el-col :span="6">
        <el-card class="account-card">
          <div class="account-item">
            <span class="label">总资产</span>
            <span class="value large">¥{{ formatNumber(account?.total_assets) }}</span>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card class="account-card">
          <div class="account-item">
            <span class="label">可用现金</span>
            <span class="value">¥{{ formatNumber(account?.cash_balance) }}</span>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card class="account-card">
          <div class="account-item">
            <span class="label">冻结资金</span>
            <span class="value">¥{{ formatNumber(account?.frozen_cash) }}</span>
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card class="account-card">
          <div class="account-item">
            <span class="label">今日盈亏</span>
            <span class="value" :class="getPnLClass(account?.daily_pnl)">
              {{ account?.daily_pnl >= 0 ? '+' : '' }}{{ formatNumber(account?.daily_pnl) }}
            </span>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 主要内容区 -->
    <el-row :gutter="20">
      <!-- 左侧：行情和下单 -->
      <el-col :span="14">
        <!-- 行情面板 -->
        <el-card class="market-card">
          <template #header>
            <div class="card-header">
              <span>实时行情</span>
              <el-button size="small" @click="loadMarketData">刷新行情</el-button>
            </div>
          </template>
          
          <el-table :data="marketData" style="width: 100%">
            <el-table-column prop="name" label="名称" width="120" />
            <el-table-column prop="symbol" label="代码" width="100" />
            <el-table-column label="价格" width="100">
              <template #default="{ row }">
                <span class="price">¥{{ row.price }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="100">
              <template #default="{ row }">
                <el-button size="small" type="primary" @click="selectSymbol(row)">
                  交易
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <!-- 下单面板 -->
        <el-card class="order-card">
          <template #header>
            <div class="card-header">
              <span>下单 ({{ selectedSymbol || '请选择标的' }})</span>
            </div>
          </template>
          
          <el-form :model="orderForm" :rules="orderRules" ref="orderFormRef" label-width="80px">
            <el-row :gutter="20">
              <el-col :span="12">
                <el-form-item label="股票代码" prop="symbol">
                  <el-input v-model="orderForm.symbol" placeholder="如 600519.SH" />
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="订单类型" prop="order_type">
                  <el-select v-model="orderForm.order_type" style="width: 100%">
                    <el-option label="市价单" value="MARKET" />
                    <el-option label="限价单" value="LIMIT" />
                  </el-select>
                </el-form-item>
              </el-col>
            </el-row>
            
            <el-row :gutter="20">
              <el-col :span="12">
                <el-form-item label="买卖方向" prop="side">
                  <el-radio-group v-model="orderForm.side">
                    <el-radio-button label="BUY">买入</el-radio-button>
                    <el-radio-button label="SELL">卖出</el-radio-button>
                  </el-radio-group>
                </el-form-item>
              </el-col>
              <el-col :span="12">
                <el-form-item label="价格" prop="price" v-if="orderForm.order_type === 'LIMIT'">
                  <el-input-number v-model="orderForm.price" :min="0.01" :precision="2" style="width: 100%" />
                </el-form-item>
              </el-col>
            </el-row>
            
            <el-form-item label="数量" prop="quantity">
              <el-input-number v-model="orderForm.quantity" :min="100" :step="100" style="width: 200px" />
              <span class="hint">* 必须是100的整数倍</span>
            </el-form-item>
            
            <el-form-item label="预估金额">
              <span class="estimated-amount">¥{{ calculateEstimatedAmount() }}</span>
            </el-form-item>
            
            <el-form-item>
              <el-button type="primary" @click="submitOrder" :loading="submitting">
                {{ orderForm.side === 'BUY' ? '买入' : '卖出' }} {{ orderForm.symbol || '股票' }}
              </el-button>
              <el-button @click="resetOrderForm">重置</el-button>
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>

      <!-- 右侧：持仓和订单 -->
      <el-col :span="10">
        <!-- 持仓列表 -->
        <el-card class="position-card">
          <template #header>
            <div class="card-header">
              <span>当前持仓 ({{ positions.length }})</span>
            </div>
          </template>
          
          <el-table :data="positions" style="width: 100%" max-height="300">
            <el-table-column prop="symbol" label="代码" width="100" />
            <el-table-column prop="quantity" label="数量" width="80" />
            <el-table-column prop="avg_price" label="成本价" width="80">
              <template #default="{ row }">
                ¥{{ row.avg_price.toFixed(2) }}
              </template>
            </el-table-column>
            <el-table-column prop="market_value" label="市值" width="100">
              <template #default="{ row }">
                ¥{{ formatNumber(row.market_value) }}
              </template>
            </el-table-column>
            <el-table-column prop="unrealized_pnl" label="浮动盈亏" width="100">
              <template #default="{ row }">
                <span :class="getPnLClass(row.unrealized_pnl)">
                  {{ row.unrealized_pnl >= 0 ? '+' : '' }}{{ formatNumber(row.unrealized_pnl) }}
                </span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>

        <!-- 订单列表 -->
        <el-card class="orders-card">
          <template #header>
            <div class="card-header">
              <span>今日订单 ({{ orders.length }})</span>
              <el-button size="small" @click="loadOrders">刷新</el-button>
            </div>
          </template>
          
          <el-table :data="orders" style="width: 100%" max-height="300">
            <el-table-column prop="order_id" label="订单号" width="180" show-overflow-tooltip />
            <el-table-column prop="symbol" label="代码" width="90" />
            <el-table-column prop="side" label="方向" width="60">
              <template #default="{ row }">
                <el-tag :type="row.side === 'BUY' ? 'success' : 'danger'" size="small">
                  {{ row.side === 'BUY' ? '买' : '卖' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="quantity" label="数量" width="70" />
            <el-table-column prop="price" label="价格" width="80">
              <template #default="{ row }">
                ¥{{ row.price?.toFixed(2) || '市价' }}
              </template>
            </el-table-column>
            <el-table-column prop="status" label="状态" width="80">
              <template #default="{ row }">
                <el-tag :type="getStatusType(row.status)" size="small">
                  {{ getStatusText(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="60">
              <template #default="{ row }">
                <el-button 
                  v-if="row.status === 'PENDING'" 
                  size="small" 
                  type="danger" 
                  @click="cancelOrder(row.order_id)"
                >
                  撤单
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <!-- 交易历史和报表 -->
    <el-row :gutter="20" class="bottom-section">
      <el-col :span="12">
        <el-card class="history-card">
          <template #header>
            <span>交易历史</span>
          </template>
          
          <el-table :data="tradeHistory" style="width: 100%">
            <el-table-column prop="trade_time" label="时间" width="180" />
            <el-table-column prop="symbol" label="代码" width="90" />
            <el-table-column prop="side" label="方向" width="60">
              <template #default="{ row }">
                <el-tag :type="row.side === 'BUY' ? 'success' : 'danger'" size="small">
                  {{ row.side === 'BUY' ? '买' : '卖' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="price" label="成交价" width="80">
              <template #default="{ row }">
                ¥{{ row.price.toFixed(2) }}
              </template>
            </el-table-column>
            <el-table-column prop="quantity" label="数量" width="70" />
            <el-table-column prop="amount" label="成交金额" width="120">
              <template #default="{ row }">
                ¥{{ formatNumber(row.amount) }}
              </template>
            </el-table-column>
            <el-table-column prop="commission" label="手续费" width="100">
              <template #default="{ row }">
                ¥{{ row.commission.toFixed(2) }}
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      
      <el-col :span="12">
        <el-card class="report-card">
          <template #header>
            <span>今日报表</span>
          </template>
          
          <div class="report-content" v-if="dailyReport">
            <el-descriptions :column="2" border>
              <el-descriptions-item label="日期">
                {{ dailyReport.date }}
              </el-descriptions-item>
              <el-descriptions-item label="账户">
                {{ dailyReport.account_id }}
              </el-descriptions-item>
              <el-descriptions-item label="期初资金">
                ¥{{ formatNumber(dailyReport.opening_balance) }}
              </el-descriptions-item>
              <el-descriptions-item label="期末资金">
                ¥{{ formatNumber(dailyReport.closing_balance) }}
              </el-descriptions-item>
              <el-descriptions-item label="今日盈亏">
                <span :class="getPnLClass(dailyReport.daily_pnl)">
                  {{ dailyReport.daily_pnl >= 0 ? '+' : '' }}{{ formatNumber(dailyReport.daily_pnl) }}
                  ({{ dailyReport.daily_pnl_pct.toFixed(2) }}%)
                </span>
              </el-descriptions-item>
              <el-descriptions-item label="手续费">
                ¥{{ dailyReport.total_commission.toFixed(2) }}
              </el-descriptions-item>
              <el-descriptions-item label="下单次数">
                {{ dailyReport.order_count }}
              </el-descriptions-item>
              <el-descriptions-item label="成交次数">
                {{ dailyReport.filled_order_count }}
              </el-descriptions-item>
            </el-descriptions>
          </div>
        </el-card>

        <!-- 风险设置 -->
        <el-card class="risk-card">
          <template #header>
            <span>风险控制设置</span>
          </template>
          
          <div class="risk-settings">
            <div class="risk-item">
              <span class="label">单只股票最大仓位:</span>
              <span class="value">{{ (riskSettings.max_position_pct * 100).toFixed(0) }}%</span>
            </div>
            <div class="risk-item">
              <span class="label">单笔最大订单金额:</span>
              <span class="value">¥{{ formatNumber(riskSettings.max_order_value) }}</span>
            </div>
            <div class="risk-item">
              <span class="label">日最大亏损限制:</span>
              <span class="value">{{ (riskSettings.max_daily_loss * 100).toFixed(0) }}%</span>
            </div>
            <div class="risk-item">
              <span class="label">最低现金余额:</span>
              <span class="value">¥{{ formatNumber(riskSettings.min_cash_balance) }}</span>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { tradingApi } from '@/api/trading'

defineOptions({ name: 'Trading' })

// 数据状态
const account = ref(null)
const positions = ref([])
const orders = ref([])
const marketData = ref([])
const tradeHistory = ref([])
const dailyReport = ref(null)
const riskSettings = ref({})
const submitting = ref(false)

// 表单数据
const selectedSymbol = ref('')
const orderForm = ref({
  symbol: '',
  order_type: 'MARKET',
  side: 'BUY',
  quantity: 100,
  price: null
})

// 表单引用
const orderFormRef = ref(null)

// 表单验证规则
const orderRules = {
  symbol: [{ required: true, message: '请输入股票代码', trigger: 'blur' }],
  order_type: [{ required: true, message: '请选择订单类型', trigger: 'change' }],
  side: [{ required: true, message: '请选择买卖方向', trigger: 'change' }],
  quantity: [
    { required: true, message: '请输入数量', trigger: 'blur' },
    { validator: (rule, value, callback) => {
      if (value % 100 !== 0) {
        callback(new Error('数量必须是100的整数倍'))
      } else {
        callback()
      }
    }, trigger: 'blur' }
  ],
  price: [{ required: true, message: '请输入价格', trigger: 'blur' }]
}

// 计算预估金额
const calculateEstimatedAmount = computed(() => {
  if (!orderForm.value.price && orderForm.value.order_type === 'LIMIT') {
    return 0
  }
  return orderForm.value.price * orderForm.value.quantity
})

// 加载账户数据
const loadAccount = async () => {
  try {
    const response = await tradingApi.getAccount()
    account.value = response.data
  } catch (error) {
    console.error('加载账户数据失败:', error)
  }
}

// 加载持仓
const loadPositions = async () => {
  try {
    const response = await tradingApi.getPositions()
    positions.value = response.data
  } catch (error) {
    console.error('加载持仓失败:', error)
  }
}

// 加载订单
const loadOrders = async () => {
  try {
    const response = await tradingApi.getOrders()
    orders.value = response.data
  } catch (error) {
    console.error('加载订单失败:', error)
  }
}

// 加载行情
const loadMarketData = async () => {
  try {
    const response = await tradingApi.getMarketQuotes()
    marketData.value = response.data
  } catch (error) {
    console.error('加载行情失败:', error)
  }
}

// 加载交易历史
const loadTradeHistory = async () => {
  try {
    const response = await tradingApi.getTradeHistory()
    tradeHistory.value = response.data
  } catch (error) {
    console.error('加载交易历史失败:', error)
  }
}

// 加载日报表
const loadDailyReport = async () => {
  try {
    const response = await tradingApi.getDailyReport()
    dailyReport.value = response.data
  } catch (error) {
    console.error('加载日报表失败:', error)
  }
}

// 加载风险设置
const loadRiskSettings = async () => {
  try {
    const response = await tradingApi.getRiskSettings()
    riskSettings.value = response.data
  } catch (error) {
    console.error('加载风险设置失败:', error)
  }
}

// 刷新所有数据
const refreshData = () => {
  loadAccount()
  loadPositions()
  loadOrders()
  loadTradeHistory()
  loadDailyReport()
  ElMessage.success('数据已刷新')
}

// 选择标的
const selectSymbol = (row) => {
  selectedSymbol.value = row.symbol
  orderForm.value.symbol = row.symbol
  if (orderForm.value.order_type === 'MARKET') {
    // 市价单时使用当前行情价格作为参考
    orderForm.value.price = row.price
  }
}

// 提交订单
const submitOrder = async () => {
  try {
    const valid = await orderFormRef.value.validate()
    if (!valid) return
    
    submitting.value = true
    
    await ElMessageBox.confirm(
      `确认${orderForm.value.side === 'BUY' ? '买入' : '卖出'} ${orderForm.value.symbol} ${orderForm.value.quantity}股 @ ¥${orderForm.value.price || '市价'}?`,
      '确认下单',
      { confirmButtonText: '确认', cancelButtonText: '取消', type: 'warning' }
    )
    
    await tradingApi.placeOrder({
      symbol: orderForm.value.symbol,
      order_type: orderForm.value.order_type,
      side: orderForm.value.side,
      quantity: orderForm.value.quantity,
      price: orderForm.value.price
    })
    
    ElMessage.success('订单提交成功')
    resetOrderForm()
    refreshData()
    
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('订单提交失败')
    }
  } finally {
    submitting.value = false
  }
}

// 撤单
const cancelOrder = async (orderId) => {
  try {
    await ElMessageBox.confirm('确定要撤销该订单吗？', '确认撤单', {
      confirmButtonText: '确认',
      cancelButtonText: '取消',
      type: 'warning'
    })
    
    await tradingApi.cancelOrder(orderId)
    ElMessage.success('撤单成功')
    loadOrders()
    
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('撤单失败')
    }
  }
}

// 重置表单
const resetOrderForm = () => {
  orderForm.value = {
    symbol: '',
    order_type: 'MARKET',
    side: 'BUY',
    quantity: 100,
    price: null
  }
  selectedSymbol.value = ''
}

// 格式化数字
const formatNumber = (num: number) => {
  if (!num) return '0.00'
  return num.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

// 获取盈亏样式
const getPnLClass = (value: number) => {
  if (!value) return ''
  return value >= 0 ? 'positive' : 'negative'
}

// 获取订单状态类型
const getStatusType = (status: string) => {
  const statusMap = {
    'PENDING': 'warning',
    'FILLED': 'success',
    'CANCELLED': 'info',
    'REJECTED': 'danger'
  }
  return statusMap[status] || 'info'
}

// 获取订单状态文本
const getStatusText = (status: string) => {
  const statusMap = {
    'PENDING': '待成交',
    'FILLED': '已成交',
    'CANCELLED': '已撤销',
    'REJECTED': '已拒绝'
  }
  return statusMap[status] || status
}

// 生命周期
onMounted(() => {
  loadAccount()
  loadPositions()
  loadOrders()
  loadMarketData()
  loadTradeHistory()
  loadDailyReport()
  loadRiskSettings()
})
</script>

<style lang="scss" scoped>
.trading-container {
  .page-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 24px;
    
    .header-left {
      display: flex;
      align-items: center;
      gap: 12px;
      
      h2 {
        margin: 0;
      }
    }
  }
  
  .account-overview {
    margin-bottom: 24px;
    
    .account-card {
      .account-item {
        display: flex;
        flex-direction: column;
        align-items: center;
        
        .label {
          font-size: 14px;
          color: #6b7280;
          margin-bottom: 8px;
        }
        
        .value {
          font-size: 20px;
          font-weight: bold;
          color: #1f2937;
          
          &.large {
            font-size: 28px;
          }
          
          &.positive {
            color: #10b981;
          }
          
          &.negative {
            color: #ef4444;
          }
        }
      }
    }
  }
  
  .market-card, .order-card, .position-card, .orders-card {
    margin-bottom: 20px;
    
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    
    .price {
      font-weight: bold;
      color: #1f2937;
    }
  }
  
  .order-card {
    .hint {
      margin-left: 12px;
      font-size: 12px;
      color: #9ca3af;
    }
    
    .estimated-amount {
      font-size: 18px;
      font-weight: bold;
      color: #3b82f6;
    }
  }
  
  .bottom-section {
    .history-card, .report-card, .risk-card {
      margin-bottom: 20px;
    }
    
    .report-content {
      padding: 16px;
    }
    
    .risk-settings {
      .risk-item {
        display: flex;
        justify-content: space-between;
        padding: 12px 0;
        border-bottom: 1px solid #f0f0f0;
        
        &:last-child {
          border-bottom: none;
        }
        
        .label {
          color: #6b7280;
        }
        
        .value {
          font-weight: bold;
          color: #1f2937;
        }
      }
    }
  }
  
  .positive {
    color: #10b981;
  }
  
  .negative {
    color: #ef4444;
  }
}
</style>