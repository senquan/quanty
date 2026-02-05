import request from '@/utils/request'

export const quantApi = {
  // 策略管理
  getStrategies(params = {}) {
    return request({
      url: '/api/v1/quant/strategies',
      method: 'get',
      params
    })
  },

  getStrategy(id: number) {
    return request({
      url: `/api/v1/quant/strategies/${id}`,
      method: 'get'
    })
  },

  createStrategy(data: any) {
    return request({
      url: '/api/v1/quant/strategies',
      method: 'post',
      data
    })
  },

  updateStrategy(id: number, data: any) {
    return request({
      url: `/api/v1/quant/strategies/${id}`,
      method: 'put',
      data
    })
  },

  deleteStrategy(id: number) {
    return request({
      url: `/api/v1/quant/strategies/${id}`,
      method: 'delete'
    })
  },

  // 验证策略
  validateStrategy(code: string) {
    return request({
      url: '/api/v1/quant/validate-strategy',
      method: 'post',
      data: { strategy_code: code }
    })
  },

  // 市场数据
  getMarketData(params: any) {
    return request({
      url: '/api/v1/quant/market-data',
      method: 'get',
      params
    })
  },

  // 回测
  runBacktest(data: any) {
    return request({
      url: '/api/v1/quant/backtest',
      method: 'post',
      data
    })
  },

  getBacktestHistory(strategyId: number) {
    return request({
      url: `/api/v1/quant/backtest-history/${strategyId}`,
      method: 'get'
    })
  }
}