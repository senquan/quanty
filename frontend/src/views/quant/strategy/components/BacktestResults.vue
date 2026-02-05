<!-- 回测结果展示组件 -->
<template>
  <el-dialog
    v-model="visible"
    :title="`策略回测结果 - ${strategy?.name}`"
    width="90%"
    class="backtest-results-dialog"
    :before-close="handleClose"
  >
    <div class="results-container" v-loading="loading">
      <!-- 关键指标卡片 -->
      <div class="metrics-grid">
        <el-card class="metric-card">
          <div class="metric-content">
            <div class="metric-label">总收益率</div>
            <div class="metric-value" :class="getReturnClass(results?.total_return)">
              {{ results?.total_return }}%
            </div>
          </div>
        </el-card>
        
        <el-card class="metric-card">
          <div class="metric-content">
            <div class="metric-label">夏普比率</div>
            <div class="metric-value">{{ results?.sharpe_ratio }}</div>
          </div>
        </el-card>
        
        <el-card class="metric-card">
          <div class="metric-content">
            <div class="metric-label">最大回撤</div>
            <div class="metric-value negative-return">{{ results?.max_drawdown }}%</div>
          </div>
        </el-card>
        
        <el-card class="metric-card">
          <div class="metric-content">
            <div class="metric-label">胜率</div>
            <div class="metric-value">{{ results?.win_rate }}%</div>
          </div>
        </el-card>
        
        <el-card class="metric-card">
          <div class="metric-content">
            <div class="metric-label">交易次数</div>
            <div class="metric-value">{{ results?.total_trades }}</div>
          </div>
        </el-card>
        
        <el-card class="metric-card">
          <div class="metric-content">
            <div class="metric-label">最终资金</div>
            <div class="metric-value">¥{{ formatNumber(results?.final_capital) }}</div>
          </div>
        </el-card>
      </div>

      <!-- 图表区域 -->
      <el-row :gutter="24" class="charts-section">
        <el-col :span="16">
          <el-card title="净值曲线">
            <template #header>
              <div class="card-header">
                <span>净值曲线</span>
                <el-radio-group v-model="chartType" size="small">
                  <el-radio-button label="portfolio">组合价值</el-radio-button>
                  <el-radio-button label="drawdown">回撤分析</el-radio-button>
                </el-radio-group>
              </div>
            </template>
            <div ref="portfolioChartRef" class="chart-container"></div>
          </el-card>
        </el-col>
        
        <el-col :span="8">
          <el-card title="收益分布">
            <div ref="distributionChartRef" class="chart-container"></div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 交易记录 -->
      <el-card title="交易记录" class="trades-card">
        <el-table :data="results?.trades || []" style="width: 100%" max-height="400">
          <el-table-column prop="type" label="类型" width="80">
            <template #default="{ row }">
              <el-tag :type="row.type === 'buy' ? 'success' : 'danger'">
                {{ row.type === 'buy' ? '买入' : '卖出' }}
              </el-tag>
            </template>
          </el-table-column>
          
          <el-table-column prop="price" label="价格" width="120">
            <template #default="{ row }">
              ¥{{ row.price.toFixed(2) }}
            </template>
          </el-table-column>
          
          <el-table-column prop="quantity" label="数量" width="100" />
          
          <el-table-column prop="amount" label="金额" width="120">
            <template #default="{ row }">
              ¥{{ formatNumber(row.price * row.quantity) }}
            </template>
          </el-table-column>
          
          <el-table-column prop="timestamp" label="时间" width="180">
            <template #default="{ row }">
              {{ formatDateTime(row.timestamp) }}
            </template>
          </el-table-column>
          
          <el-table-column label="盈亏" width="120">
            <template #default="{ row, $index }">
              <span v-if="row.type === 'sell' && $index > 0" :class="getPnLClass(row, $index)">
                {{ calculatePnL(row, $index) }}
              </span>
              <span v-else>-</span>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- 详细指标 -->
      <el-card title="详细性能指标" class="detailed-metrics">
        <el-row :gutter="24">
          <el-col :span="12">
            <h4>收益指标</h4>
            <el-descriptions :column="1" size="small">
              <el-descriptions-item label="年化收益率">
                {{ results?.annualized_return }}%
              </el-descriptions-item>
              <el-descriptions-item label="最佳单日收益">
                {{ results?.best_day }}%
              </el-descriptions-item>
              <el-descriptions-item label="最差单日收益">
                {{ results?.worst_day }}%
              </el-descriptions-item>
              <el-descriptions-item label="月均收益">
                {{ results?.monthly_avg_return }}%
              </el-descriptions-item>
            </el-descriptions>
          </el-col>
          
          <el-col :span="12">
            <h4>风险指标</h4>
            <el-descriptions :column="1" size="small">
              <el-descriptions-item label="年化波动率">
                {{ results?.volatility }}%
              </el-descriptions-item>
              <el-descriptions-item label="下行波动率">
                {{ results?.downside_volatility }}%
              </el-descriptions-item>
              <el-descriptions-item label="VaR (95%)">
                {{ results?.var_95 }}%
              </el-descriptions-item>
              <el-descriptions-item label="CVaR (95%)">
                {{ results?.cvar_95 }}%
              </el-descriptions-item>
            </el-descriptions>
          </el-col>
        </el-row>
      </el-card>
    </div>
    
    <template #footer>
      <div class="dialog-footer">
        <el-button @click="exportResults">导出报告</el-button>
        <el-button @click="handleClose">关闭</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  strategy: Object,
  results: Object,
  visible: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['close'])

const chartType = ref('portfolio')
const loading = ref(false)
const portfolioChartRef = ref(null)
const distributionChartRef = ref(null)

let portfolioChart = null
let distributionChart = null

const visible = computed({
  get: () => props.visible,
  set: (val) => emit('close')
})

watch(() => props.visible, (newVal) => {
  if (newVal) {
    nextTick(() => {
      initCharts()
    })
  }
})

watch(chartType, () => {
  updatePortfolioChart()
})

const initCharts = () => {
  if (!portfolioChartRef.value || !distributionChartRef.value) return
  
  portfolioChart = echarts.init(portfolioChartRef.value)
  distributionChart = echarts.init(distributionChartRef.value)
  
  updatePortfolioChart()
  updateDistributionChart()
  
  window.addEventListener('resize', () => {
    portfolioChart?.resize()
    distributionChart?.resize()
  })
}

const updatePortfolioChart = () => {
  if (!portfolioChart || !props.results) return
  
  const data = props.results
  
  if (chartType.value === 'portfolio') {
    const option = {
      title: {
        text: '组合价值曲线',
        left: 'center'
      },
      tooltip: {
        trigger: 'axis',
        formatter: (params) => {
          const date = new Date(params[0].axisValue).toLocaleDateString()
          const value = params[0].value
          return `${date}<br/>组合价值: ¥${formatNumber(value)}`
        }
      },
      xAxis: {
        type: 'category',
        data: generateDates(data.portfolio_values?.length || 0)
      },
      yAxis: {
        type: 'value',
        name: '组合价值 (¥)',
        axisLabel: {
          formatter: (value) => `¥${formatNumber(value)}`
        }
      },
      series: [{
        data: data.portfolio_values || [],
        type: 'line',
        smooth: true,
        itemStyle: {
          color: '#10b981'
        },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(16, 185, 129, 0.3)' },
              { offset: 1, color: 'rgba(16, 185, 129, 0.1)' }
            ]
          }
        }
      }]
    }
    portfolioChart.setOption(option)
  } else {
    // 回撤分析图表
    const drawdowns = calculateDrawdowns(data.portfolio_values || [])
    const option = {
      title: {
        text: '回撤分析',
        left: 'center'
      },
      tooltip: {
        trigger: 'axis',
        formatter: (params) => {
          const date = new Date(params[0].axisValue).toLocaleDateString()
          const value = params[0].value
          return `${date}<br/>回撤: ${value.toFixed(2)}%`
        }
      },
      xAxis: {
        type: 'category',
        data: generateDates(drawdowns.length)
      },
      yAxis: {
        type: 'value',
        name: '回撤 (%)',
        axisLabel: {
          formatter: (value) => `${value.toFixed(2)}%`
        }
      },
      series: [{
        data: drawdowns,
        type: 'line',
        smooth: true,
        itemStyle: {
          color: '#ef4444'
        },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(239, 68, 68, 0.3)' },
              { offset: 1, color: 'rgba(239, 68, 68, 0.1)' }
            ]
          }
        }
      }]
    }
    portfolioChart.setOption(option)
  }
}

const updateDistributionChart = () => {
  if (!distributionChart || !props.results) return
  
  const returns = props.results.daily_returns || []
  const bins = 20
  const min = Math.min(...returns)
  const max = Math.max(...returns)
  const binWidth = (max - min) / bins
  
  const histogram = Array(bins).fill(0)
  const labels = []
  
  for (let i = 0; i < bins; i++) {
    const binMin = min + i * binWidth
    const binMax = binMin + binWidth
    labels.push(`${(binMin * 100).toFixed(1)}%`)
    
    histogram[i] = returns.filter(r => r >= binMin && r < binMax).length
  }
  
  const option = {
    title: {
      text: '日收益率分布',
      left: 'center'
    },
    tooltip: {
      trigger: 'axis',
      formatter: (params) => {
        const range = params[0].axisValue
        const count = params[0].value
        return `收益率区间: ${range}<br/>天数: ${count}`
      }
    },
    xAxis: {
      type: 'category',
      data: labels
    },
    yAxis: {
      type: 'value',
      name: '天数'
    },
    series: [{
      data: histogram,
      type: 'bar',
      itemStyle: {
        color: {
          type: 'linear',
          x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: '#3b82f6' },
            { offset: 1, color: '#1d4ed8' }
          ]
        }
      }
    }]
  }
  
  distributionChart.setOption(option)
}

const calculateDrawdowns = (portfolioValues) => {
  if (portfolioValues.length < 2) return []
  
  const peak = portfolioValues[0]
  const drawdowns = []
  
  for (let i = 0; i < portfolioValues.length; i++) {
    let currentPeak = portfolioValues[i]
    for (let j = 0; j <= i; j++) {
      currentPeak = Math.max(currentPeak, portfolioValues[j])
    }
    
    const drawdown = ((currentPeak - portfolioValues[i]) / currentPeak) * 100
    drawdowns.push(drawdown)
  }
  
  return drawdowns
}

const generateDates = (length) => {
  const dates = []
  const endDate = new Date()
  
  for (let i = length - 1; i >= 0; i--) {
    const date = new Date(endDate)
    date.setDate(date.getDate() - i)
    dates.push(date.toISOString())
  }
  
  return dates
}

const calculatePnL = (sellTrade, index) => {
  const trades = props.results?.trades || []
  
  // 找到对应的买入交易
  let buyTrade = null
  let position = 0
  
  for (let i = 0; i < index; i++) {
    const trade = trades[i]
    if (trade.type === 'buy') {
      position += trade.quantity
      if (!buyTrade || trade.timestamp > buyTrade.timestamp) {
        buyTrade = trade
      }
    } else if (trade.type === 'sell') {
      position -= trade.quantity
      if (position <= 0) {
        buyTrade = null
      }
    }
  }
  
  if (!buyTrade) return '¥0'
  
  const pnl = (sellTrade.price - buyTrade.price) * sellTrade.quantity
  return `¥${formatNumber(Math.abs(pnl))}`
}

const getPnLClass = (sellTrade, index) => {
  const trades = props.results?.trades || []
  
  // 计算实际盈亏
  let buyTrade = null
  let position = 0
  
  for (let i = 0; i < index; i++) {
    const trade = trades[i]
    if (trade.type === 'buy') {
      position += trade.quantity
      if (!buyTrade || trade.timestamp > buyTrade.timestamp) {
        buyTrade = trade
      }
    } else if (trade.type === 'sell') {
      position -= trade.quantity
      if (position <= 0) {
        buyTrade = null
      }
    }
  }
  
  if (!buyTrade) return ''
  
  const pnl = (sellTrade.price - buyTrade.price) * sellTrade.quantity
  return pnl >= 0 ? 'positive-return' : 'negative-return'
}

const formatNumber = (num) => {
  if (!num) return '0'
  return num.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

const formatDateTime = (timestamp) => {
  return new Date(timestamp).toLocaleString('zh-CN')
}

const getReturnClass = (value) => {
  return value >= 0 ? 'positive-return' : 'negative-return'
}

const exportResults = () => {
  // 导出回测报告功能
  const report = {
    strategy: props.strategy,
    results: props.results,
    exportTime: new Date().toISOString()
  }
  
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `backtest-report-${props.strategy?.name}-${Date.now()}.json`
  a.click()
  URL.revokeObjectURL(url)
}

const handleClose = () => {
  emit('close')
}

onMounted(() => {
  if (props.visible) {
    nextTick(() => {
      initCharts()
    })
  }
})
</script>

<style lang="scss" scoped>
.backtest-results-dialog {
  .results-container {
    .metrics-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
      
      .metric-card {
        .metric-content {
          text-align: center;
          padding: 16px;
          
          .metric-label {
            font-size: 14px;
            color: #6b7280;
            margin-bottom: 8px;
          }
          
          .metric-value {
            font-size: 24px;
            font-weight: bold;
            color: #1f2937;
            
            &.positive-return {
              color: #10b981;
            }
            
            &.negative-return {
              color: #ef4444;
            }
          }
        }
      }
    }
    
    .charts-section {
      margin-bottom: 24px;
      
      .chart-container {
        height: 300px;
        width: 100%;
      }
      
      .card-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
      }
    }
    
    .trades-card {
      margin-bottom: 24px;
    }
    
    .detailed-metrics {
      h4 {
        margin-bottom: 16px;
        color: #1f2937;
      }
    }
  }
  
  .dialog-footer {
    display: flex;
    justify-content: flex-end;
    gap: 12px;
  }
}
</style>