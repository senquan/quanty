import request from '@/utils/request'

export const tradingApi = {
  // 账户管理
  getAccount() {
    return request({
      url: '/api/v1/trading/account',
      method: 'get'
    })
  },

  // 持仓管理
  getPositions() {
    return request({
      url: '/api/v1/trading/positions',
      method: 'get'
    })
  },

  // 订单管理
  getOrders() {
    return request({
      url: '/api/v1/trading/orders',
      method: 'get'
    })
  },

  placeOrder(data: any) {
    return request({
      url: '/api/v1/trading/orders',
      method: 'post',
      data
    })
  },

  cancelOrder(orderId: string) {
    return request({
      url: `/api/v1/trading/orders/${orderId}`,
      method: 'delete'
    })
  },

  getOrder(orderId: string) {
    return request({
      url: `/api/v1/trading/orders/${orderId}`,
      method: 'get'
    })
  },

  // 行情数据
  getMarketQuotes(symbols?: string) {
    return request({
      url: '/api/v1/trading/market/quotes',
      method: 'get',
      params: { symbols }
    })
  },

  getMarketPrice(symbol: string) {
    return request({
      url: `/api/v1/trading/market/price/${symbol}`,
      method: 'get'
    })
  },

  // 交易历史
  getTradeHistory(params?: any) {
    return request({
      url: '/api/v1/trading/trade-history',
      method: 'get',
      params
    })
  },

  // 报表
  getDailyReport() {
    return request({
      url: '/api/v1/trading/daily-report',
      method: 'get'
    })
  },

  // 可交易标的
  getAvailableSymbols() {
    return request({
      url: '/api/v1/trading/available-symbols',
      method: 'get'
    })
  },

  // 风险设置
  getRiskSettings() {
    return request({
      url: '/api/v1/trading/risk-settings',
      method: 'get'
    })
  }
}