/**
 * 量化交易 API 接口层
 * 对接后端 /api/v1/quant/ 下的所有端点
 *
 * ⚠️ 回测的计算不在 backend（R3）：A 股行情在 dc 的 `factor.raw_bars`，
 * backend 拿不到，所以 `/quant/backtest` 与 `/quant/backtest/styles`
 * 都是转发 dc。**标的一律是 A 股代码**，没有 yahoo / crypto 数据源选单。
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

/**
 * 口径表 —— **优先用 `/quant/backtest/styles` 拉回来的那一份**。
 *
 * 闸口认哪些值、每个口径最少要多少根 bar，事实来源在 dc 的 `gate.py`。
 * 这里这份只是「服务还没回来」时的兜底，别把它当判断依据：
 * 抄一份静态表，迟早出现「闸口要 240 根、下拉却允许 60 根」的错位。
 */
export const BACKTEST_STYLES = [
  {
    key: 'long',
    label: '长线',
    holding: '持仓以月 / 季度计',
    minBars: 480,
    whyMin: '持仓周期以月计时，两年才够十来个完整的进出；样本再少，胜率与盈亏比就是几笔交易的偶然',
  },
  {
    key: 'swing',
    label: '短线 / 波段',
    holding: '持仓以天 / 周计',
    minBars: 240,
    whyMin: '持仓以周计时，一年约能形成几十笔交易，统计量才开始有意义',
  },
  {
    key: 'intraday',
    label: '做 T（日内回转）',
    holding: '当日进出',
    minBars: 0,
    whyMin: '需分钟级数据，当前暂不支持',
  },
] as const;

export type BacktestStyle = (typeof BACKTEST_STYLES)[number]['key'];

/** 服务返回的口径项（字段与 dc `gate.STYLES` 一致） */
export interface BacktestStyleOption {
  key: string;
  label: string;
  holding: string;
  min_bars: number;
  why_min: string;
}

/** 复权口径 —— 同样以 `/quant/backtest/styles` 为准 */
export const PRICE_FIELDS = [
  {
    key: 'qfq',
    note: '前复权（close）—— 名义价即真实价，整手 / 最低佣金 / 涨跌停判定都对；但锚在最新日，标的每次除权后历史值都会变，跨时间跑结果会漂',
  },
  {
    key: 'hfq',
    note: '后复权（hfq_close 反推）—— 锚在最早日，历史值永不改变，结果可复现；但名义价不是真实价，整手与最低 5 元佣金按名义金额判定',
  },
] as const;

export type PriceField = (typeof PRICE_FIELDS)[number]['key'];

export interface PriceFieldOption {
  key: string;
  note: string;
}

/** `/quant/backtest/styles` 的返回（转发 dc，事实来源在 dc 的 gate.py） */
export interface BacktestStyleCatalog {
  data_source: string;
  symbol_hints: string;
  styles: BacktestStyleOption[];
  price_fields: PriceFieldOption[];
}

/** 回测请求 */
export interface BacktestRequest {
  strategy_id: number;
  /** A 股代码，带后缀（600519.SH）；纯数字 600519 也能认 */
  symbol: string;
  start_date: string;
  end_date: string;
  initial_capital: number;
  /** 口径：long / swing / intraday，默认 swing */
  style?: BacktestStyle;
  /** 声明策略会做空；A 股下只能为 false，闸口会拦 */
  allow_short?: boolean;
  /** false = 不套用任何市场规则，仅供纯算法验证 */
  apply_market_rules?: boolean;
  /** 复权口径：qfq / hfq，默认 qfq */
  price_field?: PriceField;
}

/** 闸口拒绝（HTTP 422）—— reason 说为什么不成立，remedy 说怎么才能跑 */
export interface BacktestRefusal {
  reason: string;
  remedy: string;
}

/** 被市场规则挡下的信号（T+1 / 涨跌停 / 整手 / 停牌 / 资金不足） */
export interface RejectedSignal {
  bar: number;
  type: 'buy' | 'sell';
  price: number;
  quantity: number;
  reason: string;
}

/**
 * 数据侧元信息 —— 这次回测到底吃进去多少根 bar、用的哪条价格序列。
 * 没有它，「-15.41%」这个数字没有任何语境：区间可能被截断、口径可能是后复权。
 */
export interface BacktestDataMeta {
  symbol?: string;
  price_field: string;
  requested_price_field?: string;
  /** hfq 下的缩放系数 k（qfq 为 1.0） */
  hfq_factor?: number | null;
  bars: number;
  first_date: string;
  last_date: string;
}

/**
 * 回测成交记录 —— 与后端 `schemas.quant.TradeInfo` 一致。
 * 注意后端字段是 `type` / `timestamp`，不是 `action` / `date`。
 */
export interface BacktestTrade {
  type: 'buy' | 'sell';
  price: number;
  quantity: number;
  timestamp: string;
}

/** 闸口放行时给出的计划摘要 */
export interface BacktestPlan {
  symbol: string;
  market: string;
  market_label: string;
  style: string;
  start: string;
  end: string;
  interval: string;
  initial_capital: number;
  data_source: string;
  allow_short: boolean;
  limits: string[];
  notes: string[];
}

/** 回测结果 —— 与后端 `schemas.quant.BacktestResult` 一致 */
export interface BacktestResult {
  strategy_id: number;
  total_return: number;
  sharpe_ratio: number;
  max_drawdown: number;
  win_rate: number;
  total_trades: number;
  final_capital: number;
  trades: BacktestTrade[];
  daily_returns: number[];
  portfolio_values: number[];
  /** 与 portfolio_values 等长的交易日 */
  portfolio_dates: string[];
  /** 闸口给出的限制说明，必须与数字一起呈现 */
  limits: string[];
  warnings: string[];
  /** 被市场规则挡下的信号（T+1 / 涨跌停 / 整手 / 停牌 / 资金不足） */
  rejections: RejectedSignal[];
  total_fees: number;
  plan: BacktestPlan | null;
  /** 期末仍持有的股数（未平仓部分） */
  final_position: number;
  /** 期末可用现金（final_capital 含持仓市值，这个不含） */
  cash: number;
  /** 实际吃进去的数据 —— bar 数 / 复权口径 / 区间首末日 */
  data: BacktestDataMeta | null;
}

/** 验证结果 */
export interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
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
export async function getStrategiesApi(params?: {
  skip?: number;
  limit?: number;
  search?: string;
}) {
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

/** 运行回测。闸口不放行时后端返回 422，detail 是 {reason, remedy} */
export async function runBacktestApi(data: BacktestRequest) {
  return requestClient.post<BacktestResult>('/quant/backtest', data);
}

/**
 * 拉口径表（转发 dc 的 `gate.py`）。
 *
 * 拿不到就返回 null，调用方退回静态兜底 —— 口径表挂了不该让整个回测页打不开，
 * 但兜底时会把「用的不是服务端那份」说明白（见 index.vue 的 stylesFallback）。
 */
export async function getBacktestStylesApi() {
  try {
    return await requestClient.get<BacktestStyleCatalog>('/quant/backtest/styles');
  } catch {
    return null;
  }
}

/** 历史回测记录（backtest_results 表，字段与 BacktestResult 不同） */
export interface BacktestHistoryItem {
  id: number;
  strategy_id: number;
  start_date: string;
  end_date: string;
  total_return: number;
  sharpe_ratio: number;
  max_drawdown: number;
  win_rate: number;
  trades_count: number;
  created_at: string;
}

/**
 * 从 422 响应里取出闸口的拒绝理由。
 * 不是闸口拒绝（比如网络错误、500）则返回 null。
 *
 * 两种形态都要认：
 * ① 统一包装 —— `{code, msg, data: {reason, remedy}}`（backend 的 `Response.fail`，
 *    dict 形态的 detail 会被放进 `data`，见 `main.py` 的 http_exception_handler）；
 * ② 裸 detail —— `{detail: {reason, remedy}}`（未经包装时）。
 */
export function parseBacktestRefusal(error: any): BacktestRefusal | null {
  const body = error?.response?.data;
  const payload = body?.data ?? body?.detail;
  if (
    payload &&
    typeof payload.reason === 'string' &&
    typeof payload.remedy === 'string'
  ) {
    return { reason: payload.reason, remedy: payload.remedy };
  }
  return null;
}

/** 获取回测历史（返回 backtest_results 表记录，不是 BacktestResult） */
export async function getBacktestHistoryApi(strategyId: number) {
  return requestClient.get<BacktestHistoryItem[]>(
    `/quant/backtest-history/${strategyId}`,
  );
}

/** 验证策略代码 */
export async function validateStrategyApi(code: string) {
  return requestClient.post<ValidationResult>(
    '/quant/validate-strategy',
    null,
    { params: { strategy_code: code } },
  );
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
