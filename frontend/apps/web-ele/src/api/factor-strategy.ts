/**
 * 因子选股策略 API 接口层
 *
 * 对接主后端（:8000）/api/v1/factor-strategies 系列路由，由主后端代理转发到
 * 已登记的 data-cleaner 实例，前端无需感知清洗服务地址与 X-API-Key。
 */

import { requestClient } from '#/api/request';
import type { FactorDefinition } from '#/api/factor-library';

// ============ 类型定义 ============

/** 因子权重模式 */
export type WeightMode = 'auto_ir' | 'manual';

/** 中性化模式 */
export type NeutralizeMode = 'industry' | 'standardize';

/** 标的股票池（板块过滤，可多选） */
export type UniverseType = 'bj' | 'cyb' | 'kcb' | 'main';

/** 调仓周期 */
export interface RebalanceConfig {
  freq: 'weekly' | 'monthly' | 'every_n_days';
  every_n_days?: number | null;
}

/** 过滤器 */
export interface FilterConfig {
  exclude_st?: boolean;
  min_list_days?: number;
  /** 排除停牌（trading_status.suspended=1 或当日无 bar） */
  exclude_suspended?: boolean;
  /** 买入侧：排除涨停（close >= limit_up，买不进） */
  exclude_limit_up?: boolean;
  /** 卖出侧：排除跌停（close <= limit_down，避免接飞刀） */
  exclude_limit_down?: boolean;
  /** 总市值下限（亿元）；total_mv < min_cap×1e5(千元) 剔除 */
  min_cap?: number | null;
  /** 第一层硬性阈值筛选规则（配置驱动，见 three-layer-strategy-design §2） */
  hard_rules?: HardRule[];
}

// ============ 第一层 hard_rules 类型 ============

/** hard_rules 比较算子 */
export type HardRuleOp = '<=' | '>=' | '<' | '>' | '==' | '!=';

/** hard_rules 角色（均参与通过判定；role 仅决定计分归属，见设计 §2.1） */
export type HardRuleRole = 'core' | 'liquidity' | 'risk';

/** 动态阈值（一期仅 quantile：取全市场该因子分位作阈值） */
export interface HardRuleDynamic {
  mode: 'quantile';
  quantile: number;
}

/** 第一层硬性阈值规则 */
export interface HardRule {
  factor: string;
  op: HardRuleOp;
  /** 固定阈值（与 dynamic 二选一） */
  value?: number | null;
  role: HardRuleRole;
  /** 动态阈值；为空表示用固定 value */
  dynamic?: HardRuleDynamic | null;
}

/** 因子策略配置（存于 strategy.config JSONB） */
export interface FactorStrategyConfig {
  factor_codes: string[];
  weights: Record<string, number>;
  weight_mode: WeightMode;
  neutralize: NeutralizeMode;
  top_n: number;
  rebalance: RebalanceConfig;
  trade_time: string;
  initial_capital: number;
  filters: FilterConfig;
  lookback_days?: number;
  /** 标的股票池（板块多选，空数组 = 全市场） */
  universe?: UniverseType[];
  /** 自选股代码列表（与所选板块取并集，跨板块生效） */
  custom_codes?: string[];
  is_active?: boolean;
}

/** 策略列表项 */
export interface FactorStrategy {
  id: number;
  name: string;
  description: string | null;
  config: FactorStrategyConfig;
  is_active: boolean;
  owner: string | null;
  created_at: string;
  updated_at: string;
}

/** 回测指标 */
export interface BacktestMetrics {
  totalReturn: number;
  annualReturn: number;
  sharpe: number;
  maxDrawdown: number;
  winRate: number;
  turnover: number;
  finalCapital: number;
  days: number;
  rebalances: number;
}

/** 回测净值点 */
export interface NavPoint {
  date: string;
  value: number;
}

/** 回测调仓期持仓 */
export interface RebalanceHolding {
  symbol: string;
  score: number;
  weight: number;
  industry?: string;
  z_scores?: Record<string, number>;
}

/** 回测调仓期 */
export interface RebalancePeriod {
  date: string;
  tradeDate: string;
  weights: Record<string, number>;
  holdings: RebalanceHolding[];
}

/** 回测结果 */
export interface BacktestResult {
  backtest_id: number;
  metrics: BacktestMetrics;
  nav: NavPoint[];
  rebalances: RebalancePeriod[];
  warnings: string[];
}

/** 回测历史项 */
export interface BacktestHistoryItem {
  id: number;
  strategy_id: number;
  start_date: string | null;
  end_date: string | null;
  metrics: BacktestMetrics | null;
  created_at: string;
}

/** 回测详情（存于 factor.factor_strategy_backtests，含完整净值与持仓） */
export interface BacktestDetail {
  id: number;
  strategy_id: number;
  start_date: string | null;
  end_date: string | null;
  metrics: BacktestMetrics | null;
  nav: NavPoint[];
  rebalances: RebalancePeriod[];
  warnings: string[];
  created_at: string;
}

/** 执行记录 */
export interface ExecutionRecord {
  id: number;
  strategy_id: number;
  rebalance_date: string;
  trade_date: string | null;
  target_count: number;
  orders_placed: number;
  amount: number;
  status: string;
  detail: {
    orders?: Array<{ order: Record<string, any>; ok: boolean }>;
    target_count_asked?: number;
    error?: string;
  } | null;
  created_at: string;
}

/** 目标持仓预览（/scores） */
export interface TargetPreview {
  date: string;
  weights: Record<string, number>;
  scores: Record<string, number>;
  holdings: RebalanceHolding[];
}

// ============ API 接口 ============

/** 策略列表 */
export async function listFactorStrategiesApi(activeOnly = false) {
  return requestClient.get<FactorStrategy[]>('/factor-strategies', {
    params: activeOnly ? { active_only: true } : {},
  });
}

/** 策略详情 */
export async function getFactorStrategyApi(id: number) {
  return requestClient.get<FactorStrategy>(`/factor-strategies/${id}`);
}

/** 创建策略 */
export async function createFactorStrategyApi(data: {
  name: string;
  description?: string | null;
  config: FactorStrategyConfig;
  is_active?: boolean;
}) {
  return requestClient.post<FactorStrategy>('/factor-strategies', data);
}

/** 更新策略 */
export async function updateFactorStrategyApi(
  id: number,
  data: Partial<{
    name: string;
    description: string | null;
    config: FactorStrategyConfig;
    is_active: boolean;
  }>,
) {
  return requestClient.put<FactorStrategy>(`/factor-strategies/${id}`, data);
}

/** 删除策略 */
export async function deleteFactorStrategyApi(id: number) {
  return requestClient.delete(`/factor-strategies/${id}`);
}

/** 运行回测 */
export async function runBacktestApi(
  id: number,
  params?: { start?: string; end?: string },
) {
  return requestClient.post<BacktestResult>(
    `/factor-strategies/${id}/backtest`,
    params || {},
  );
}

/** 回测历史 */
export async function listBacktestsApi(id: number) {
  return requestClient.get<BacktestHistoryItem[]>(
    `/factor-strategies/${id}/backtests`,
  );
}

/** 回测详情（含净值曲线与逐期持仓） */
export async function getBacktestDetailApi(id: number, bid: number) {
  return requestClient.get<BacktestDetail>(
    `/factor-strategies/${id}/backtests/${bid}`,
  );
}

/** 手动调仓 */
export async function rebalanceStrategyApi(id: number) {
  return requestClient.post<Record<string, any>>(
    `/factor-strategies/${id}/rebalance`,
  );
}

/** 执行记录 */
export async function listExecutionsApi(id: number, limit = 50) {
  return requestClient.get<ExecutionRecord[]>(
    `/factor-strategies/${id}/executions`,
    { params: { limit } },
  );
}

/** 目标持仓预览 */
export async function previewTargetApi(
  config: FactorStrategyConfig,
  asOf?: string,
) {
  return requestClient.post<TargetPreview>('/factor-strategies/scores', {
    config,
    as_of: asOf,
  });
}

/** 因子可用性（哪些因子当前有因子值） */
export async function factorAvailabilityApi() {
  return requestClient.get<Record<string, boolean>>(
    '/factor-strategies/factors/availability',
  );
}

/** 刷新行业分类 */
export async function refreshIndustriesApi() {
  return requestClient.post<{ count: number; source: string }>(
    '/factor-strategies/industries/refresh',
  );
}

export type { FactorDefinition };
