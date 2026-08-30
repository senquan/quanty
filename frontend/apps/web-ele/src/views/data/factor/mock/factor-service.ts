import type {
  BacktestParams,
  BacktestResult,
  Factor,
  FactorCategory,
} from '../types';

import {
  calculateCorrelationMatrix,
  generateFactorPerformance,
  INITIAL_FACTORS,
  runMultiFactorBacktest,
} from './factor-data';

/**
 * @deprecated 本地 mock 数据服务，已被 ../factor-service（真实 API）取代。
 * 仅作回退参考保留，页面不再引用。
 */

// In-memory store
let factorPool: Factor[] = [...INITIAL_FACTORS];
export const factorService = {
  async getFactors(): Promise<Factor[]> {
    return [...factorPool];
  },

  async getFactorById(id: string): Promise<Factor | undefined> {
    return factorPool.find((f) => f.id === id);
  },

  async createFactor(data: Partial<Factor>): Promise<Factor> {
    const factor = generateFactorPerformance(
      data.name || '未命名',
      (data.code || `CUSTOM_${Date.now()}`).toUpperCase(),
      data.formula || 'close',
      data.category || 'custom',
    );
    const newFactor: Factor = {
      ...factor,
      ...data,
      id: `user_${data.code?.toLowerCase() || Date.now()}_${Date.now()}`,
      author: 'user',
      createdAt: new Date().toISOString().split('T')[0]!,
    } as Factor;
    factorPool = [newFactor, ...factorPool];
    return newFactor;
  },

  async updateFactor(id: string, data: Partial<Factor>): Promise<Factor> {
    const idx = factorPool.findIndex((f) => f.id === id);
    if (idx === -1) throw new Error(`Factor ${id} not found`);
    const updated = { ...factorPool[idx]!, ...data, id };
    factorPool[idx] = updated;
    return updated;
  },

  async deleteFactor(id: string): Promise<void> {
    factorPool = factorPool.filter((f) => f.id !== id);
  },

  async getCorrelationMatrix(
    factorIds: string[],
  ): Promise<Record<string, Record<string, number>>> {
    const selected = factorPool.filter((f) => factorIds.includes(f.id));
    return calculateCorrelationMatrix(selected);
  },

  async runBacktest(params: BacktestParams): Promise<BacktestResult> {
    return runMultiFactorBacktest(params, factorPool);
  },

  async generateAIFactor(category: FactorCategory): Promise<Factor> {
    const templates: Record<
      FactorCategory,
      { name: string; code: string; formula: string }
    > = {
      momentum: {
        name: '非线性多周期价格通道极值系数',
        code: `CHNL_EXT_${Math.floor(Math.random() * 100)}`,
        formula: '(high - mean(close, 10)) / std(close, 20)',
      },
      volatility: {
        name: '波动率不对称偏度因子',
        code: `VOL_SKEW_${Math.floor(Math.random() * 100)}`,
        formula: 'std(close, 5) / std(close, 60) - 1',
      },
      value: {
        name: '反向市值账面资本再增益比率',
        code: `BM_VALUE_${Math.floor(Math.random() * 100)}`,
        formula: 'book_value / (market_cap * std(close, 10))',
      },
      growth: {
        name: '归母扣非净利润二重边际成长率',
        code: `DOUB_PROF_${Math.floor(Math.random() * 100)}`,
        formula:
          '(net_profit_q - delay(net_profit_q, 4)) / abs(delay(net_profit_q, 4))',
      },
      sentiment: {
        name: '大单主力资金净换手流比率',
        code: `BIG_FLOW_${Math.floor(Math.random() * 100)}`,
        formula: '(volume - delay(volume, 1)) / mean(volume, 20)',
      },
      technical: {
        name: '多周期均线乖离共振因子',
        code: `MA_RESO_${Math.floor(Math.random() * 100)}`,
        formula: '(close - mean(close, 20)) / std(close, 20)',
      },
      custom: {
        name: '阿尔法复合探索波动系数',
        code: `ALPHA_EXPL_${Math.floor(Math.random() * 100)}`,
        formula: 'rsi(close, 6) / close * std(close, 15)',
      },
    };

    const t = templates[category] || templates.custom;
    const factor = generateFactorPerformance(
      t.name,
      t.code,
      t.formula,
      category,
    );
    factor.author = 'user';
    factor.id = `user_${t.code.toLowerCase()}_${Date.now()}`;
    factorPool = [factor, ...factorPool];
    return factor;
  },
};
