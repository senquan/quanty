/**
 * 交易 API 接口层（模拟盘 / 实盘）
 *
 * 对接主后端（:8000）/api/v1/trading 系列路由。
 * 模拟盘(paper) 与实盘(live) 共用同一套接口，通过 `mode` 参数区分：
 * - paper：内存撮合，持仓与成交落库，进程重启后从 DB 回灌；
 * - live ：券商适配器，默认 BROKER_DRY_RUN=true 不发起真实请求。
 *
 * requestClient 已配置 responseReturn:'data' + successCode:200，
 * 因此此处直接拿到后端 Response.success(data=...) 中的 data。
 */

import { requestClient } from '#/api/request';

// ============ 类型定义 ============

export type TradeMode = 'paper' | 'live';

/** 单个模式的可用性 */
export interface BrokerMode {
  mode: TradeMode;
  broker: string;
  ready: boolean;
  message: string;
}

export interface ModeInfo {
  default: TradeMode;
  modes: BrokerMode[];
}

/** 概览聚合（/trading/overview） */
export interface TradingOverview {
  mode: TradeMode;
  broker: string;
  account_id: string;
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
export interface Position {
  symbol: string;
  side: string;
  quantity: number;
  avg_price: number;
  last_price: number;
  market_value: number;
  unrealized_pnl: number;
  pnl_percent: number;
  updated_at: string | null;
}

/** 委托订单 */
export interface TradeOrder {
  order_id: string;
  broker_order_id: string | null;
  symbol: string;
  side: string;
  order_type: string;
  quantity: number;
  price: number | null;
  filled_quantity: number;
  /** PENDING / FILLED / CANCELLED / REJECTED */
  status: string;
  message: string | null;
  /** manual（手动下单） / strategy（策略调仓） */
  source: string;
  created_at: string | null;
  filled_at: string | null;
}

/** 成交明细 */
export interface TradeRecord {
  trade_id: number;
  order_id: number;
  symbol: string;
  side: string;
  price: number;
  quantity: number;
  amount: number;
  commission: number;
  trade_time: string | null;
}

/** 调仓记录（聚合各因子策略执行记录） */
export interface RebalanceRecord {
  strategy_id: number;
  strategy_name: string | null;
  rebalance_date: string | null;
  trade_date: string | null;
  target_count: number | null;
  orders_placed: number | null;
  amount: number | null;
  status: string;
  detail: Record<string, any> | null;
}

/** 账户详情（含持仓） */
export interface AccountInfo {
  account_id: string;
  mode: TradeMode;
  broker: string;
  total_assets: number;
  cash_balance: number;
  frozen_cash: number;
  market_value: number;
  total_pnl: number;
  total_pnl_pct: number;
  unrealized_pnl: number;
  positions: Position[];
}

export interface OrderRequest {
  symbol: string;
  side: 'BUY' | 'SELL';
  quantity: number;
  order_type?: 'MARKET' | 'LIMIT';
  price?: number | null;
  mode?: TradeMode;
}

export interface AvailableSymbol {
  symbol: string;
  name: string;
  price: number;
}

export interface RiskSettings {
  max_position_pct: number;
  /** 单笔订单金额上限（占总资产比例） */
  max_order_pct: number;
  /** 按当前总资产换算出的有效上限 = 总资产 × max_order_pct */
  max_order_value: number;
  max_daily_loss: number;
  min_cash_balance: number;
}

// ============ API 接口 ============

/** 各模式可用性 */
export async function getTradingModeApi() {
  return requestClient.get<ModeInfo>('/trading/mode');
}

/** 量化概览 */
export async function getOverviewApi(mode: TradeMode = 'paper') {
  return requestClient.get<TradingOverview>('/trading/overview', {
    params: { mode },
  });
}

/** 账户详情（含持仓） */
export async function getAccountApi(mode: TradeMode = 'paper') {
  return requestClient.get<AccountInfo>('/trading/account', {
    params: { mode },
  });
}

/** 持仓列表 */
export async function getPositionsApi(mode: TradeMode = 'paper') {
  return requestClient.get<Position[]>('/trading/positions', {
    params: { mode },
  });
}

/** 委托列表 */
export async function getOrdersApi(
  mode: TradeMode = 'paper',
  params?: { status?: string; limit?: number },
) {
  return requestClient.get<TradeOrder[]>('/trading/orders', {
    params: { mode, ...(params || {}) },
  });
}

/** 下单 */
export async function createOrderApi(data: OrderRequest) {
  return requestClient.post<TradeOrder>('/trading/orders', data);
}

/** 订单详情 */
export async function getOrderApi(orderId: string, mode: TradeMode = 'paper') {
  return requestClient.get<TradeOrder>(`/trading/orders/${orderId}`, {
    params: { mode },
  });
}

/** 撤单 */
export async function cancelOrderApi(
  orderId: string,
  mode: TradeMode = 'paper',
) {
  return requestClient.delete<TradeOrder>(`/trading/orders/${orderId}`, {
    params: { mode },
  });
}

/** 成交记录 */
export async function getTradesApi(
  mode: TradeMode = 'paper',
  params?: { start?: string; end?: string; limit?: number },
) {
  return requestClient.get<TradeRecord[]>('/trading/trades', {
    params: { mode, ...(params || {}) },
  });
}

/** 调仓记录 */
export async function getRebalancesApi(limit = 20) {
  return requestClient.get<RebalanceRecord[]>('/trading/rebalances', {
    params: { limit },
  });
}

/** 可交易标的（模拟撮合服务内置） */
export async function getAvailableSymbolsApi() {
  return requestClient.get<{ symbols: AvailableSymbol[]; total: number }>(
    '/trading/available-symbols',
  );
}

/** 单个标的价格 */
export async function getMarketPriceApi(symbol: string) {
  return requestClient.get<{ symbol: string; price: number; timestamp: string }>(
    `/trading/market/price/${symbol}`,
  );
}

/** 风控参数 */
export async function getRiskSettingsApi() {
  return requestClient.get<RiskSettings>('/trading/risk-settings');
}
