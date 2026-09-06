/**
 * 投资组合 API 接口层
 *
 * 对接主后端（:8000）/api/v1/portfolios 系列路由。
 * 组合为交易域牵头实体：绑定一个策略、独立资金池、独立持仓，
 * 取代原「以策略牵头的模拟盘账户」。
 *
 * requestClient 已配置 responseReturn:'data' + successCode:200，
 * 因此此处直接拿到后端 Response.success(data=...) 中的 data。
 */

import { requestClient } from '#/api/request';

export type TradeMode = 'paper' | 'live';

/** 组合（牵头实体） */
export interface Portfolio {
  id: number;
  name: string;
  strategy_id: number;
  mode: TradeMode;
  broker: string;
  owner_user_id: number | null;
  initial_capital: number;
  cash_balance: number;
  frozen_cash: number;
  auto_rebalance: boolean;
  is_active: boolean;
  description: string | null;
  created_at: string | null;
  updated_at: string | null;
}

/** 组合概览（资金池 / 持仓） */
export interface PortfolioOverview {
  mode: TradeMode;
  broker: string | null;
  account_id: string | null;
  portfolio_id: number | null;
  portfolio_count?: number;
  initial_capital: number;
  total_assets: number;
  market_value: number;
  cash_balance: number;
  frozen_cash: number;
  total_pnl: number;
  total_pnl_pct: number;
  unrealized_pnl: number;
  position_count: number;
}

/** 持仓 */
export interface PortfolioPosition {
  portfolio_id: number;
  symbol: string;
  side: string;
  quantity: number;
  avg_price: number;
  last_price: number;
  prev_close: number | null;
  market_value: number;
  unrealized_pnl: number;
  pnl_percent: number;
  updated_at: string | null;
}

/** 调仓记录 */
export interface RebalanceRecord {
  portfolio_id: number;
  strategy_id: number | null;
  strategy_name: string | null;
  rebalance_date: string | null;
  trade_date: string | null;
  target_count: number | null;
  orders_placed: number | null;
  amount: number | null;
  status: string;
  detail: Record<string, any> | null;
}

/** 组合每日市值与收益快照 */
export interface PortfolioValuePoint {
  portfolio_id: number | null;
  strategy_id: number | null;
  value_date: string;
  cash_balance: number;
  market_value: number;
  total_assets: number;
  daily_return: number | null;
  cumulative_return: number | null;
}

export interface PortfolioCreate {
  name: string;
  strategy_id: number;
  mode: TradeMode;
  initial_capital: number;
  auto_rebalance?: boolean;
  is_active?: boolean;
  description?: string | null;
}

export interface PortfolioUpdate {
  name?: string;
  strategy_id?: number;
  auto_rebalance?: boolean;
  is_active?: boolean;
  description?: string | null;
}

// ============ API 接口 ============

/** 组合列表 */
export async function getPortfoliosApi(params?: { mode?: TradeMode }) {
  return requestClient.get<Portfolio[]>('/portfolios', { params });
}

/** 组合详情 */
export async function getPortfolioApi(id: number) {
  return requestClient.get<Portfolio>(`/portfolios/${id}`);
}

/** 创建组合 */
export async function createPortfolioApi(data: PortfolioCreate) {
  return requestClient.post<Portfolio>('/portfolios', data);
}

/** 更新组合 */
export async function updatePortfolioApi(id: number, data: PortfolioUpdate) {
  return requestClient.put<Portfolio>(`/portfolios/${id}`, data);
}

/** 删除组合 */
export async function deletePortfolioApi(id: number) {
  return requestClient.delete(`/portfolios/${id}`);
}

/** 组合概览 */
export async function getPortfolioOverviewApi(id: number) {
  return requestClient.get<PortfolioOverview>(`/portfolios/${id}/overview`);
}

/** 组合持仓 */
export async function getPortfolioPositionsApi(id: number) {
  return requestClient.get<PortfolioPosition[]>(`/portfolios/${id}/positions`);
}

/** 组合调仓记录 */
export async function getPortfolioRebalancesApi(id: number, limit = 20) {
  return requestClient.get<RebalanceRecord[]>(`/portfolios/${id}/rebalances`, {
    params: { limit },
  });
}

/** 组合每日市值与收益 */
export async function getPortfolioValuesApi(id: number, limit = 120) {
  return requestClient.get<PortfolioValuePoint[]>(`/portfolios/${id}/values`, {
    params: { limit },
  });
}

/** 手动触发该组合调仓 */
export async function triggerPortfolioRebalanceApi(id: number, force = true) {
  return requestClient.post(`/portfolios/${id}/rebalance`, {}, { params: { force } });
}
