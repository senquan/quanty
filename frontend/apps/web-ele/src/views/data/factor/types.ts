export type FactorCategory =
  | 'custom'
  | 'growth'
  | 'momentum'
  | 'sentiment'
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
