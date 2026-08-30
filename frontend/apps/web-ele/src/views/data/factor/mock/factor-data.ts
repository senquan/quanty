import type {
  BacktestParams,
  BacktestResult,
  Factor,
  FactorCategory,
  SelectedStock,
  StockSelectionParams,
  StockSelectionPeriod,
  StockSelectionResult,
} from '../types';
import { gradeIC, gradeIR, gradeSharpe } from '../grading';

// Standard 12-month dates
export const GLOBAL_DATES = [
  '2025-06',
  '2025-07',
  '2025-08',
  '2025-09',
  '2025-10',
  '2025-11',
  '2025-12',
  '2026-01',
  '2026-02',
  '2026-03',
  '2026-04',
  '2026-05',
];

// Base Benchmark Cumulative Returns starting at 100
export const BENCHMARK_CURVES: Record<string, number[]> = {
  CSI300: [
    100.0, 102.5, 98.4, 101.2, 104.1, 102.0, 99.5, 103.2, 106.8, 105.4,
    108.5, 110.2,
  ],
  CSI500: [
    100.0, 101.2, 97.5, 100.8, 103.0, 101.1, 98.0, 102.4, 105.1, 103.8,
    107.0, 109.1,
  ],
  SSE50: [
    100.0, 103.1, 100.0, 102.4, 105.8, 104.0, 101.5, 104.8, 108.1, 107.1,
    110.0, 111.5,
  ],
};

// Simple hash for deterministic mock curves
function hashString(str: string): number {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = (hash << 5) - hash + char;
    hash |= 0;
  }
  return Math.abs(hash);
}

function getCategoryDescription(category: FactorCategory, name: string): string {
  const map: Record<FactorCategory, string> = {
    momentum: `${name}因子通过追踪资产价格的变动趋势来识别中短期动量突破效应，用于在上涨通道中追随强势股。`,
    volatility: `${name}因子评估股价震荡幅度和非系统性风险，高波动往往代表市场分歧巨大，可作为风险控制或反转策略因子。`,
    value: `${name}因子利用长期经典的价值投资模型，评估股票估值性价比及高分红率，侧重确定性与估值安全边际。`,
    growth: `${name}因子反映企业基本面的扩张和成长潜力，利用核心财务数据同比/环比增速指标来寻找高成长型个股。`,
    sentiment: `${name}因子结合资金流入流出、多空比率及散户贴吧讨论情绪，追踪短线资金博弈热度并识别超买超卖情绪。`,
    technical: `${name}因子基于均线、动量振荡与布林通道等技术形态刻画价格趋势强弱与超买超卖状态。`,
    custom: `${name}因子是研究员自定义编写的高级量化计算因子，旨在探索特殊市场结构下的非线性超额收益特征。`,
  };
  return map[category] || map.custom;
}

export function generateFactorPerformance(
  name: string,
  code: string,
  formula: string,
  category: FactorCategory,
): Factor {
  const seed = hashString(code + formula);
  const rand = (offset: number) => {
    const s = Math.sin(seed + offset) * 10_000;
    return s - Math.floor(s);
  };

  let icMean = 0;
  let icStd = 0.05;
  let sharpeRatio = 1.0;
  let maxDrawdown = 0.15;
  let winRate = 0.5;
  const upperFormula = formula.toUpperCase();

  switch (category) {
    case 'momentum': {
      icMean = 0.04 + rand(1) * 0.025;
      icStd = 0.045 + rand(2) * 0.015;
      sharpeRatio = 1.35 + rand(3) * 0.5;
      maxDrawdown = 0.12 + rand(4) * 0.08;
      winRate = 0.54 + rand(5) * 0.08;
      break;
    }
    case 'volatility': {
      icMean = -0.035 - rand(1) * 0.03;
      icStd = 0.05 + rand(2) * 0.02;
      sharpeRatio = 0.85 + rand(3) * 0.4;
      maxDrawdown = 0.18 + rand(4) * 0.12;
      winRate = 0.51 + rand(5) * 0.05;
      break;
    }
    case 'value': {
      icMean = -0.045 - rand(1) * 0.025;
      if (
        upperFormula.includes('DIVIDEND') ||
        upperFormula.includes('DY_')
      ) {
        icMean = 0.035 + rand(1) * 0.02;
      }
      icStd = 0.04 + rand(2) * 0.02;
      sharpeRatio = 1.25 + rand(3) * 0.4;
      maxDrawdown = 0.14 + rand(4) * 0.07;
      winRate = 0.53 + rand(5) * 0.06;
      break;
    }
    case 'growth': {
      icMean = 0.055 + rand(1) * 0.028;
      icStd = 0.038 + rand(2) * 0.012;
      sharpeRatio = 1.62 + rand(3) * 0.6;
      maxDrawdown = 0.1 + rand(4) * 0.05;
      winRate = 0.58 + rand(5) * 0.07;
      break;
    }
    case 'sentiment': {
      icMean = 0.03 + rand(1) * 0.035;
      if (upperFormula.includes('VOLUME_')) {
        icMean = -0.025 - rand(1) * 0.02;
      }
      icStd = 0.06 + rand(2) * 0.03;
      sharpeRatio = 1.1 + rand(3) * 0.5;
      maxDrawdown = 0.16 + rand(4) * 0.1;
      winRate = 0.52 + rand(5) * 0.08;
      break;
    }
    default: {
      icMean = (rand(1) - 0.5) * 0.1;
      icStd = 0.04 + rand(2) * 0.03;
      sharpeRatio = 0.8 + rand(3) * 1.0;
      maxDrawdown = 0.08 + rand(4) * 0.16;
      winRate = 0.48 + rand(5) * 0.15;
      break;
    }
  }

  const ir = icMean / icStd;

  // IC series
  const icSeries: number[] = [];
  for (let i = 0; i < 12; i++) {
    const monthlyIC = icMean + (rand(10 + i) - 0.5) * icStd * 1.8;
    icSeries.push(Number(monthlyIC.toFixed(4)));
  }

  // Cumulative returns
  const longReturns: number[] = [100.0];
  const shortReturns: number[] = [100.0];
  const benchmarkReturns = [...BENCHMARK_CURVES.CSI300!];
  const alphaFactor = icMean * 180;
  const beta = 0.85 + rand(20) * 0.3;

  for (let i = 1; i < 12; i++) {
    const monthlyBenchReturn =
      benchmarkReturns[i]! / benchmarkReturns[i - 1]! - 1;
    const randVol = (rand(30 + i) - 0.5) * 0.06;
    const longMonthlyReturn =
      monthlyBenchReturn * beta + alphaFactor / 100 + randVol;
    const shortMonthlyReturn =
      monthlyBenchReturn * beta - alphaFactor / 100 - randVol * 0.4;
    const lastLong = longReturns[longReturns.length - 1]!;
    const lastShort = shortReturns[shortReturns.length - 1]!;
    longReturns.push(Number((lastLong * (1 + longMonthlyReturn)).toFixed(2)));
    shortReturns.push(
      Number((lastShort * (1 + shortMonthlyReturn)).toFixed(2)),
    );
  }

  // Mock universe values for correlation
  const mockUniverseValues: number[] = [];
  for (let k = 0; k < 30; k++) {
    const categoryFactorVal =
      category === 'momentum'
        ? 0.3
        : category === 'value'
          ? -0.2
          : 0.0;
    const stockVal =
      categoryFactorVal + Math.sin(seed + k) * 2.0 + Math.cos(k * 1.5) * 0.8;
    mockUniverseValues.push(Number(stockVal.toFixed(3)));
  }

  // Data sources
  const dataSources: string[] = [];
  if (
    upperFormula.includes('CLOSE') ||
    upperFormula.includes('OPEN') ||
    upperFormula.includes('RSI') ||
    upperFormula.includes('MOM')
  ) {
    dataSources.push('日K收盘价');
  }
  if (
    upperFormula.includes('HIGH') ||
    upperFormula.includes('LOW') ||
    upperFormula.includes('ATR')
  ) {
    dataSources.push('日K最高最低价');
  }
  if (
    upperFormula.includes('VOLUME') ||
    upperFormula.includes('TURNOVER')
  ) {
    dataSources.push('日K成交量');
  }
  if (
    upperFormula.includes('PE') ||
    upperFormula.includes('NET_INCOME') ||
    upperFormula.includes('NET_PROFIT')
  ) {
    dataSources.push('利润表/利润总额');
  }
  if (
    upperFormula.includes('PB') ||
    upperFormula.includes('BOOK_VALUE')
  ) {
    dataSources.push('资产负债表/股东权益');
  }
  if (
    upperFormula.includes('DIVIDEND') ||
    upperFormula.includes('DY_')
  ) {
    dataSources.push('分红送股公告名册');
  }
  if (
    upperFormula.includes('BULLISH') ||
    upperFormula.includes('POSTS')
  ) {
    dataSources.push('第三方股吧舆情监控');
  }
  if (dataSources.length === 0) dataSources.push('日K收盘价');

  return {
    id: `${code.toLowerCase()}_${category}`,
    name,
    code,
    category,
    description: getCategoryDescription(category, name),
    formula,
    dataSources,
    frequency:
      category === 'growth'
        ? 'Quarterly'
        : category === 'value'
          ? 'Weekly'
          : 'Daily',
    status: 'active',
    author: 'system',
    createdAt: '2025-01-15',
    icMean: Number(icMean.toFixed(4)),
    icStd: Number(icStd.toFixed(4)),
    ir: Number(ir.toFixed(3)),
    winRate: Number(winRate.toFixed(3)),
    maxDrawdown: Number(maxDrawdown.toFixed(3)),
    sharpeRatio: Number(sharpeRatio.toFixed(2)),
    // 分级评分沿用与真实数据一致的规则（../grading）
    icRank: gradeIC(icMean).score,
    irRank: gradeIR(ir).score,
    sharpeRank: gradeSharpe(sharpeRatio).score,
    dates: GLOBAL_DATES,
    icSeries,
    longReturns,
    shortReturns,
    benchmarkReturns,
    mockUniverseValues,
  };
}

// 10 default factors
export const INITIAL_FACTORS: Factor[] = [
  generateFactorPerformance(
    '20日价格动量',
    'MOM_20',
    '(close - delay(close, 20)) / delay(close, 20)',
    'momentum',
  ),
  generateFactorPerformance(
    '14日相对强弱指标',
    'RSI_14',
    'rsi(close, 14)',
    'momentum',
  ),
  generateFactorPerformance(
    'MACD振幅扩散',
    'MACD_DIFF',
    'ema(close, 12) - ema(close, 26)',
    'momentum',
  ),
  generateFactorPerformance(
    '20日滚动波动率',
    'VOL_20',
    'std(close, 20) / mean(close, 20)',
    'volatility',
  ),
  generateFactorPerformance(
    'ATR真实振幅波动',
    'ATR_14',
    'mean(max(high-low, abs(high-delay(close,1))), 14)',
    'volatility',
  ),
  generateFactorPerformance(
    '市盈率(TTM)倒数',
    'EP_TTM',
    'net_income_ttm / market_cap',
    'value',
  ),
  generateFactorPerformance(
    '股息分红再投资率',
    'DY_1Y',
    'dividends_1y / market_cap',
    'value',
  ),
  generateFactorPerformance(
    '净利润同比增速',
    'NET_PROFIT_GROWTH',
    '(net_profit_q - net_profit_q_yoy) / abs(net_profit_q_yoy)',
    'growth',
  ),
  generateFactorPerformance(
    '营业收入成长率',
    'REV_GROWTH_YOY',
    '(rev_q - rev_q_yoy) / rev_q_yoy',
    'growth',
  ),
  generateFactorPerformance(
    '散户股吧舆情牛市指数',
    'BULLISH_INDEX',
    'bullish_posts / total_posts',
    'sentiment',
  ),
];

// Pearson correlation
function calculatePearson(x: number[], y: number[]): number {
  const n = Math.min(x.length, y.length);
  if (n === 0) return 1.0;
  let sumX = 0,
    sumY = 0,
    sumXY = 0,
    sumX2 = 0,
    sumY2 = 0;
  for (let i = 0; i < n; i++) {
    sumX += x[i]!;
    sumY += y[i]!;
    sumXY += x[i]! * y[i]!;
    sumX2 += x[i]! * x[i]!;
    sumY2 += y[i]! * y[i]!;
  }
  const num = n * sumXY - sumX * sumY;
  const den = Math.sqrt((n * sumX2 - sumX * sumX) * (n * sumY2 - sumY * sumY));
  if (den === 0) return 0.0;
  return num / den;
}

export function calculateCorrelationMatrix(
  factors: Factor[],
): Record<string, Record<string, number>> {
  const matrix: Record<string, Record<string, number>> = {};
  for (const f of factors) matrix[f.code] = {};

  for (let i = 0; i < factors.length; i++) {
    const f1 = factors[i]!;
    for (let j = i; j < factors.length; j++) {
      const f2 = factors[j]!;
      const r = Number(
        calculatePearson(f1.mockUniverseValues, f2.mockUniverseValues).toFixed(
          3,
        ),
      );
      matrix[f1.code]![f2.code] = r;
      matrix[f2.code]![f1.code] = r;
    }
  }
  return matrix;
}

// Multi-factor backtest engine
export function runMultiFactorBacktest(
  params: BacktestParams,
  allFactors: Factor[],
): BacktestResult {
  const selectedFactors = allFactors.filter((f) =>
    params.selectedFactorIds.includes(f.id),
  );

  if (selectedFactors.length === 0) {
    return {
      dates: GLOBAL_DATES,
      portfolioReturns: GLOBAL_DATES.map(() => 100),
      benchmarkReturns: BENCHMARK_CURVES.CSI300!,
      excessReturns: GLOBAL_DATES.map(() => 0),
      metrics: {
        totalReturn: 0,
        annualizedReturn: 0,
        benchmarkReturn: 0,
        sharpeRatio: 0,
        maxDrawdown: 0,
        informationRatio: 0,
        beta: 1.0,
        alpha: 0,
        turnoverRate: 0,
      },
      factorWeights: {},
    };
  }

  // Calculate weights
  const weights: Record<string, number> = {};
  if (params.weightMethod === 'equal') {
    const w = 1.0 / selectedFactors.length;
    for (const f of selectedFactors)
      weights[f.code] = Number(w.toFixed(3));
  } else if (params.weightMethod === 'ic_weighted') {
    let totalAbsIC = 0;
    for (const f of selectedFactors) totalAbsIC += Math.abs(f.icMean);
    if (totalAbsIC === 0) {
      const w = 1.0 / selectedFactors.length;
      for (const f of selectedFactors) weights[f.code] = w;
    } else {
      for (const f of selectedFactors)
        weights[f.code] = Number(
          (Math.abs(f.icMean) / totalAbsIC).toFixed(3),
        );
    }
  } else {
    const corrMap = calculateCorrelationMatrix(selectedFactors);
    const scores: Record<string, number> = {};
    let totalScore = 0;
    for (const f of selectedFactors) {
      let penalty = 0;
      for (const other of selectedFactors) {
        if (other.code !== f.code) {
          const correlation = corrMap[f.code]?.[other.code] || 0;
          if (correlation > 0.4) penalty += correlation * 0.4;
        }
      }
      const finalScore = Math.max(0.1, f.sharpeRatio - penalty);
      scores[f.code] = finalScore;
      totalScore += finalScore;
    }
    for (const f of selectedFactors)
      weights[f.code] = Number((scores[f.code]! / totalScore).toFixed(4));
  }

  const dates = GLOBAL_DATES;
  const benchmarkCurve =
    BENCHMARK_CURVES[params.benchmark] || BENCHMARK_CURVES.CSI300!;
  const portfolioReturns: number[] = [100.0];
  const excessReturns: number[] = [0.0];
  const feeDeduction = params.transactionFee;
  const turnoverRatio =
    params.rebalancePeriod === 'Weekly'
      ? 0.25
      : params.rebalancePeriod === 'Monthly'
        ? 0.15
        : 0.08;

  for (let i = 1; i < dates.length; i++) {
    let weightedFactorChange = 0;
    for (const f of selectedFactors) {
      const factorMonthlyReturn =
        f.longReturns[i]! / f.longReturns[i - 1]! - 1;
      weightedFactorChange += factorMonthlyReturn * weights[f.code]!;
    }
    const friction = feeDeduction * turnoverRatio;
    const finalMonthlyReturn = weightedFactorChange - friction;
    const lastPortVal = portfolioReturns[portfolioReturns.length - 1]!;
    const currentPortVal = Number(
      (lastPortVal * (1 + finalMonthlyReturn)).toFixed(2),
    );
    portfolioReturns.push(currentPortVal);
    const portCumReturn = currentPortVal / 100 - 1;
    const benchCumReturn = benchmarkCurve[i]! / 100 - 1;
    excessReturns.push(
      Number(((portCumReturn - benchCumReturn) * 100).toFixed(2)),
    );
  }

  const totalPortReturn =
    portfolioReturns[portfolioReturns.length - 1]! / 100 - 1;
  const totalBenchReturn =
    benchmarkCurve[benchmarkCurve.length - 1]! / 100 - 1;
  const annualizedReturn = totalPortReturn;

  // Sharpe
  const monthlyDiffs: number[] = [];
  for (let i = 1; i < dates.length; i++) {
    monthlyDiffs.push(
      portfolioReturns[i]! / portfolioReturns[i - 1]! - 1,
    );
  }
  const meanMonthly =
    monthlyDiffs.reduce((a, b) => a + b, 0) / monthlyDiffs.length;
  const varianceMonthly =
    monthlyDiffs.reduce((sum, val) => sum + (val - meanMonthly) ** 2, 0) /
    monthlyDiffs.length;
  const stdMonthly = Math.sqrt(varianceMonthly);
  const riskFree = 0.025;
  const annualizedStd = stdMonthly * Math.sqrt(12);
  const sharpeRatio =
    annualizedStd > 0
      ? (annualizedReturn - riskFree) / annualizedStd
      : 0;

  // Max drawdown
  let maxDrawdown = 0;
  let peak = 0;
  for (const val of portfolioReturns) {
    if (val > peak) peak = val;
    const dd = (peak - val) / peak;
    if (dd > maxDrawdown) maxDrawdown = dd;
  }

  // IR
  const monthlyExcess: number[] = [];
  for (let i = 1; i < dates.length; i++) {
    const portRet = portfolioReturns[i]! / portfolioReturns[i - 1]! - 1;
    const benchRet = benchmarkCurve[i]! / benchmarkCurve[i - 1]! - 1;
    monthlyExcess.push(portRet - benchRet);
  }
  const meanExcess =
    monthlyExcess.reduce((a, b) => a + b, 0) / monthlyExcess.length;
  const varExcess =
    monthlyExcess.reduce((sum, val) => sum + (val - meanExcess) ** 2, 0) /
    monthlyExcess.length;
  const stdExcess = Math.sqrt(varExcess);
  const infoRatio =
    stdExcess > 0
      ? (meanExcess * 12) / (stdExcess * Math.sqrt(12))
      : 0;

  const beta = 0.85 + totalPortReturn * 0.1 - totalBenchReturn * 0.05;
  const alphaVal =
    annualizedReturn - (riskFree + beta * (totalBenchReturn - riskFree));

  return {
    dates,
    portfolioReturns,
    benchmarkReturns: benchmarkCurve,
    excessReturns,
    metrics: {
      totalReturn: Number((totalPortReturn * 100).toFixed(2)),
      annualizedReturn: Number((annualizedReturn * 100).toFixed(2)),
      benchmarkReturn: Number((totalBenchReturn * 100).toFixed(2)),
      sharpeRatio: Number(sharpeRatio.toFixed(2)),
      maxDrawdown: Number((maxDrawdown * 100).toFixed(2)),
      informationRatio: Number(infoRatio.toFixed(2)),
      beta: Number(beta.toFixed(2)),
      alpha: Number((alphaVal * 100).toFixed(2)),
      turnoverRate: Number((turnoverRatio * 100).toFixed(1)),
    },
    factorWeights: weights,
  };
}

// ============ 一键组合选股引擎（本地模拟，未接入真实 backend） ============
/** 模拟股票池（runStockSelection 会按 universe 板块/自选股过滤） */
export const STOCK_UNIVERSE: { code: string; name: string }[] = [
  { code: '600519.SH', name: '贵州茅台' },
  { code: '601318.SH', name: '中国平安' },
  { code: '600036.SH', name: '招商银行' },
  { code: '000858.SZ', name: '五粮液' },
  { code: '601166.SH', name: '兴业银行' },
  { code: '600276.SH', name: '恒瑞医药' },
  { code: '000333.SZ', name: '美的集团' },
  { code: '002594.SZ', name: '比亚迪' },
  { code: '600900.SH', name: '长江电力' },
  { code: '601899.SH', name: '紫金矿业' },
  { code: '000651.SZ', name: '格力电器' },
  { code: '600030.SH', name: '中信证券' },
  { code: '601888.SH', name: '中国中免' },
  { code: '603259.SH', name: '药明康德' },
  { code: '000002.SZ', name: '万科A' },
  { code: '600887.SH', name: '伊利股份' },
  { code: '601012.SH', name: '隆基绿能' },
  { code: '600309.SH', name: '万华化学' },
  { code: '002415.SZ', name: '海康威视' },
  { code: '600009.SH', name: '上海机场' },
  { code: '601398.SH', name: '工商银行' },
  { code: '601628.SH', name: '中国人寿' },
  { code: '600000.SH', name: '浦发银行' },
  { code: '000001.SZ', name: '平安银行' },
  { code: '600585.SH', name: '海螺水泥' },
  { code: '601857.SH', name: '中国石油' },
  { code: '600028.SH', name: '中国石化' },
  { code: '601988.SH', name: '中国银行' },
  { code: '600104.SH', name: '上汽集团' },
  { code: '000725.SZ', name: '京东方A' },
  { code: '002475.SZ', name: '立讯精密' },
  { code: '603288.SH', name: '海天味业' },
  { code: '600690.SH', name: '海尔智家' },
  { code: '601668.SH', name: '中国建筑' },
  { code: '600048.SH', name: '保利发展' },
  { code: '000063.SZ', name: '中兴通讯' },
  { code: '601658.SH', name: '邮储银行' },
  { code: '600438.SH', name: '通威股份' },
  { code: '002714.SZ', name: '牧原股份' },
  { code: '600031.SH', name: '三一重工' },
  { code: '835185.BJ', name: '贝特瑞' },
  { code: '835368.BJ', name: '连城数控' },
  { code: '920002.BJ', name: '中科美菱' },
];

/** 确定性随机：基于字符串种子生成 [0,1) */
function randFromKeys(...keys: string[]): number {
  const s = hashString(keys.join('|'));
  const r = Math.sin(s) * 10_000;
  return r - Math.floor(r);
}

/** 个股在某因子上的标准化暴露，范围约 [-1, 1] */
function stockFactorExposure(stockCode: string, factorCode: string): number {
  return randFromKeys(stockCode, factorCode) * 2 - 1;
}

/** 板块 -> 代码前 3 位前缀（与 data-cleaner engine 一致的板块划分） */
const MOCK_UNIVERSE_BOARDS: Record<string, string[]> = {
  main: ['600', '601', '603', '605', '000', '001', '002', '003'],
  cyb: ['300', '301'],
  kcb: ['688', '689'],
  bj: ['8', '920'],
};

function mockInUniverse(
  code: string,
  universe: string[],
  customCodes: string[],
): boolean {
  const digits = code.replace(/\D/g, '');
  // 自选股（跨板块，并集）
  if (customCodes.length > 0) {
    const set = new Set(customCodes.map((c) => c.replace(/\D/g, '').slice(0, 6)));
    if (set.has(digits.slice(0, 6))) return true;
  }
  // 板块过滤：未选板块 => 全市场
  if (!universe || universe.length === 0) return true;
  let ok = false;
  for (const u of universe) {
    const prefixes = MOCK_UNIVERSE_BOARDS[u];
    if (prefixes && prefixes.some((p) => digits.slice(0, p.length) === p)) {
      ok = true;
      break;
    }
  }
  return ok;
}

export function runStockSelection(
  params: StockSelectionParams,
  allFactors: Factor[],
): StockSelectionResult {
  const selectedFactors = allFactors.filter((f) =>
    params.selectedFactorIds.includes(f.id),
  );

  // 按标的股票池（板块/自选股）过滤候选标的
  const pool = STOCK_UNIVERSE.filter((s) =>
    mockInUniverse(s.code, params.universe, params.customCodes || []),
  );

  const emptyResult: StockSelectionResult = {
    periods: GLOBAL_DATES.map((date) => ({
      date,
      stocks: [],
      avgScore: 0,
      avgExpectedReturn: 0,
    })),
    metrics: {
      avgStocks: 0,
      avgScore: 0,
      avgExpectedReturn: 0,
      hitRate: 0,
      turnover: 0,
    },
    factorWeights: {},
  };

  if (selectedFactors.length === 0 || pool.length === 0) {
    return emptyResult;
  }

  // 因子权重（与回测一致的三种加权模型）
  const weights: Record<string, number> = {};
  if (params.weightMethod === 'equal') {
    const w = 1.0 / selectedFactors.length;
    for (const f of selectedFactors) weights[f.code] = w;
  } else if (params.weightMethod === 'ic_weighted') {
    let totalAbsIC = 0;
    for (const f of selectedFactors) totalAbsIC += Math.abs(f.icMean);
    if (totalAbsIC === 0) {
      const w = 1.0 / selectedFactors.length;
      for (const f of selectedFactors) weights[f.code] = w;
    } else {
      for (const f of selectedFactors)
        weights[f.code] = Math.abs(f.icMean) / totalAbsIC;
    }
  } else {
    const corrMap = calculateCorrelationMatrix(selectedFactors);
    const scores: Record<string, number> = {};
    let totalScore = 0;
    for (const f of selectedFactors) {
      let penalty = 0;
      for (const other of selectedFactors) {
        if (other.code !== f.code) {
          const correlation = corrMap[f.code]?.[other.code] || 0;
          if (correlation > 0.4) penalty += correlation * 0.4;
        }
      }
      const finalScore = Math.max(0.1, f.sharpeRatio - penalty);
      scores[f.code] = finalScore;
      totalScore += finalScore;
    }
    for (const f of selectedFactors) weights[f.code] = scores[f.code]! / totalScore;
  }

  const topN = Math.max(1, Math.min(params.topN, pool.length));
  const benchmarkCurve = BENCHMARK_CURVES.CSI300!;

  // 计算每个月的持仓
  const periods: StockSelectionPeriod[] = [];
  const selectedCodeSets: string[][] = [];

  for (let i = 0; i < GLOBAL_DATES.length; i++) {
    // 个股复合得分
    const scored = pool.map((s) => {
      let score = 0;
      for (const f of selectedFactors) {
        score += weights[f.code]! * stockFactorExposure(s.code, f.code);
      }
      return { ...s, score: Number(score.toFixed(4)) };
    });

    scored.sort((a, b) => b.score - a.score);

    const pickLong = scored.slice(0, topN);
    const pickShort =
      params.mode === 'long_short'
        ? scored.slice(scored.length - topN).reverse()
        : [];

    const buildStock = (
      item: { code: string; name: string; score: number },
      side: 'long' | 'short',
    ): SelectedStock => {
      const expectedReturn = Number(
        (item.score * 2.5 * (side === 'long' ? 1 : -1)).toFixed(2),
      );
      return {
        code: item.code,
        name: item.name,
        score: item.score,
        weight: Number((1 / (side === 'long' ? topN : pickShort.length || 1)).toFixed(4)),
        expectedReturn,
        side,
      };
    };

    const stocks = [
      ...pickLong.map((s) => buildStock(s, 'long')),
      ...pickShort.map((s) => buildStock(s, 'short')),
    ];

    const avgScore =
      stocks.reduce((sum, s) => sum + s.score, 0) / (stocks.length || 1);
    const avgExpectedReturn =
      stocks.reduce((sum, s) => sum + s.expectedReturn, 0) /
      (stocks.length || 1);

    periods.push({
      date: GLOBAL_DATES[i]!,
      stocks,
      avgScore: Number(avgScore.toFixed(3)),
      avgExpectedReturn: Number(avgExpectedReturn.toFixed(2)),
    });
    selectedCodeSets.push(pickLong.map((s) => s.code));
  }

  // 汇总指标
  const avgScore =
    periods.reduce((sum, p) => sum + p.avgScore, 0) / periods.length;
  const avgExpectedReturn =
    periods.reduce((sum, p) => sum + p.avgExpectedReturn, 0) / periods.length;

  let hitMonths = 0;
  for (let i = 1; i < periods.length; i++) {
    const benchMonthly =
      benchmarkCurve[i]! / benchmarkCurve[i - 1]! - 1;
    if (periods[i]!.avgExpectedReturn / 100 > benchMonthly) hitMonths++;
  }
  const hitRate = Number(((hitMonths / (periods.length - 1 || 1)) * 100).toFixed(1));

  // 平均双边换手率（相邻月份持仓交集）
  let turnoverSum = 0;
  for (let i = 1; i < selectedCodeSets.length; i++) {
    const prev = new Set(selectedCodeSets[i - 1]!);
    const cur = selectedCodeSets[i]!;
    let overlap = 0;
    for (const c of cur) if (prev.has(c)) overlap++;
    turnoverSum += (1 - overlap / topN) * 2 * 100;
  }
  const turnover = Number(
    (turnoverSum / (selectedCodeSets.length - 1 || 1)).toFixed(1),
  );

  return {
    periods,
    metrics: {
      avgStocks: params.mode === 'long_short' ? topN * 2 : topN,
      avgScore: Number(avgScore.toFixed(3)),
      avgExpectedReturn: Number(avgExpectedReturn.toFixed(2)),
      hitRate,
      turnover,
    },
    factorWeights: weights,
  };
}
