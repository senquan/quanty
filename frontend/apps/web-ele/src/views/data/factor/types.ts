export type FactorCategory =
  | 'momentum'
  | 'volatility'
  | 'value'
  | 'growth'
  | 'sentiment'
  | 'custom';

export type UpdateFrequency = 'Daily' | 'Weekly' | 'Monthly' | 'Quarterly';

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

  // Efficacy assessment metrics
  icMean: number;
  icStd: number;
  ir: number;
  winRate: number;
  maxDrawdown: number;
  sharpeRatio: number;

  // Series data
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
  rebalancePeriod: 'Weekly' | 'Monthly' | 'Quarterly';
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
