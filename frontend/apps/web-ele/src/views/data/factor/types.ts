export type FactorCategory =
  | 'custom'
  | 'growth'
  | 'momentum'
  | 'sentiment'
  | 'size'
  | 'technical'
  | 'value'
  | 'volatility';

export type UpdateFrequency = 'Daily' | 'Monthly' | 'Quarterly' | 'Weekly';

export interface Factor {
  id: string;
  name: string;
  code: string;
  category: FactorCategory;
  description: string;
  formula: string;
  dataSources: string[];
  frequency: UpdateFrequency;
  status: 'active' | 'draft';
  author: 'system' | 'user';
  createdAt: string;

  // Efficacy assessment metrics（未评估的因子为 0）
  icMean: number;
  icStd: number;
  ir: number;
  winRate: number;
  maxDrawdown: number;
  sharpeRatio: number;
  icRank: number;
  irRank: number;
  sharpeRank: number;

  // Series data
  // 清洗服务目前只提供因子定义与效能指标，不提供时间序列；
  // 接真实 API 后这些数组为空，相关图表会自动降级展示。
  dates: string[];
  icSeries: number[];
  longReturns: number[];
  shortReturns: number[];
  benchmarkReturns: number[];

  // Universe values for correlation calculation
  mockUniverseValues: number[];

  // 来源清洗服务状态（backend-owned 语义，用于“dc 离线”提示与徽标）
  available?: boolean;
  serviceCode?: string;
  serviceStatus?: string;
}

export interface BacktestParams {
  selectedFactorIds: string[];
  weightMethod: 'equal' | 'ic_weighted' | 'max_sharpe';
  benchmark: 'CSI300' | 'CSI500' | 'SSE50';
  startDate: string;
  endDate: string;
  rebalancePeriod: 'Monthly' | 'Quarterly' | 'Weekly';
  transactionFee: number;
}

export interface BacktestMetrics {
  totalReturn: number;
  annualizedReturn: number;
  benchmarkReturn: number;
  sharpeRatio: number;
  maxDrawdown: number;
  informationRatio: number;
  beta: number;
  alpha: number;
  turnoverRate: number;
}

export interface BacktestResult {
  dates: string[];
  portfolioReturns: number[];
  benchmarkReturns: number[];
  excessReturns: number[];
  metrics: BacktestMetrics;
  factorWeights: Record<string, number>;
}

// ---- 一键组合选股 ----
export interface SelectedStock {
  code: string;
  name: string;
  /** 复合因子得分（加权因子暴露） */
  score: number;
  /** 组合内权重 */
  weight: number;
  /** 模型预测下期超额收益（%） */
  expectedReturn: number;
  side: 'long' | 'short';
  /** 行业（真实接口返回，模拟引擎为空） */
  industry?: string;
}

export interface StockSelectionPeriod {
  date: string;
  stocks: SelectedStock[];
  avgScore: number;
  avgExpectedReturn: number;
}

export interface StockSelectionMetrics {
  avgStocks: number;
  /** 平均复合得分 */
  avgScore: number;
  /** 平均预测月超额收益(%) */
  avgExpectedReturn: number;
  /** 命中率：组合预测跑赢基准的月份占比 */
  hitRate: number;
  /** 平均双边换手率(%) */
  turnover: number;
}

export interface StockSelectionParams {
  selectedFactorIds: string[];
  weightMethod: 'equal' | 'ic_weighted' | 'max_sharpe';
  /** 标的股票池（板块多选，空数组 = 全市场） */
  universe: ('bj' | 'cyb' | 'kcb' | 'main')[];
  /** 自选股代码列表（与所选板块取并集，跨板块生效） */
  customCodes?: string[];
  /** 多头精选 / 多空对冲 */
  mode: 'long' | 'long_short';
  topN: number;
}

export interface StockSelectionResult {
  periods: StockSelectionPeriod[];
  metrics: StockSelectionMetrics;
  factorWeights: Record<string, number>;
}
