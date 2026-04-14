/**
 * 量化交易 API 接口层
 * 对接后端 /api/v1/quant/ 下的所有端点
 */

import { requestClient } from '#/api/request';

// ============ 类型定义 ============

/** 策略 */
export interface Strategy {
  id: number;
  name: string;
  description: string;
  code: string;
  user_id: number;
  created_at: string;
  updated_at: string;
}

/** 策略创建请求 */
export interface StrategyCreate {
  name: string;
  description: string;
  code: string;
}

/** 回测请求 */
export interface BacktestRequest {
  strategy_id: number;
  symbol: string;
  data_source: string;
  start_date: string;
  end_date: string;
  initial_capital: number;
}

/** 交易记录 */
export interface TradeInfo {
  date: string;
  action: 'buy' | 'sell';
  price: number;
  quantity: number;
  value: number;
  commission: number;
}

/** 回测结果 */
export interface BacktestResult {
  strategy_id: number;
  strategy_name: string;
  symbol: string;
  start_date: string;
  end_date: string;
  initial_capital: number;
  final_value: number;
  total_return: number;
  annualized_return: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  max_drawdown: number;
  volatility: number;
  win_rate: number;
  profit_factor: number;
  total_trades: number;
  trades: TradeInfo[];
  equity_curve: Array<{ date: string; value: number }>;
  created_at: string;
}

/** 验证结果 */
export interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
  has_buy_function: boolean;
  has_sell_function: boolean;
}

/** 市场数据响应 */
export interface MarketDataResponse {
  symbol: string;
  data_source: string;
  data: Array<{
    date: string;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  }>;
}

// ============ API 接口 ============

/** 获取策略列表 */
export async function getStrategiesApi(params?: { skip?: number; limit?: number }) {
  return requestClient.get<Strategy[]>('/quant/strategies', { params });
}

/** 获取单个策略详情 */
export async function getStrategyApi(id: number) {
  return requestClient.get<Strategy>(`/quant/strategies/${id}`);
}

/** 创建策略 */
export async function createStrategyApi(data: StrategyCreate) {
  return requestClient.post<Strategy>('/quant/strategies', data);
}

/** 更新策略 */
export async function updateStrategyApi(id: number, data: Partial<StrategyCreate>) {
  return requestClient.put<Strategy>(`/quant/strategies/${id}`, data);
}

/** 删除策略 */
export async function deleteStrategyApi(id: number) {
  return requestClient.delete(`/quant/strategies/${id}`);
}

/** 运行回测 */
export async function runBacktestApi(data: BacktestRequest) {
  return requestClient.post<BacktestResult>('/quant/backtest', data);
}

/** 获取回测历史 */
export async function getBacktestHistoryApi(strategyId: number) {
  return requestClient.get<BacktestResult[]>(`/quant/backtest-history/${strategyId}`);
}

/** 验证策略代码 */
export async function validateStrategyApi(code: string) {
  return requestClient.post<ValidationResult>('/quant/validate-strategy', { code });
}

/** 获取市场数据 */
export async function getMarketDataApi(params: {
  symbol: string;
  data_source: string;
  start_date: string;
  end_date: string;
}) {
  return requestClient.get<MarketDataResponse>('/quant/market-data', { params });
}
