/**
 * 因子底册库数据服务（真实 API 版）
 *
 * 早期版本基于 ./mock/factor-data 的本地假数据；现改为调用主后端
 * /api/v1/factors（由主后端代理到已登记的 data-cleaner 实例）。
 *
 * 说明：
 * - 因子定义、效能指标（IC/IR/Sharpe/回撤/胜率）、相关性矩阵均来自真实接口
 * - 净值/IC 时间序列目前清洗服务未提供，返回空数组，相关图表会自动降级展示
 * - 组合回测引擎尚未接入，暂沿用本地模拟
 */
import type {
  Factor,
  FactorCategory,
  UpdateFrequency,
} from './types';

import {
  type CleanerServiceStatus,
  type FactorDefinition,
  aiGenerateFactorApi,
  createFactorApi,
  deleteFactorApi,
  factorCorrelationApi,
  listFactorServicesApi,
  listFactorsApi,
  updateFactorApi,
} from '#/api/factor-library';

import { describeFactor } from './descriptions';
import { gradeIC, gradeIR, gradeSharpe } from './grading';

const VALID_CATEGORIES: FactorCategory[] = [
  'momentum',
  'volatility',
  'value',
  'growth',
  'size',
  'sentiment',
  'technical',
  'custom',
];

/** 清洗服务侧类别名 -> 前端类别（未收录的归入 custom） */
const CATEGORY_ALIAS: Record<string, FactorCategory> = {
  momentum: 'momentum',
  volatility: 'volatility',
  technical: 'technical',
  value: 'value',
  growth: 'growth',
  size: 'size',
  sentiment: 'sentiment',
  custom: 'custom',
};

const FREQ_ALIAS: Record<string, UpdateFrequency> = {
  daily: 'Daily',
  '1d': 'Daily',
  weekly: 'Weekly',
  '1w': 'Weekly',
  monthly: 'Monthly',
  '1mo': 'Monthly',
  quarterly: 'Quarterly',
  '1q': 'Quarterly',
};

function normalizeCategory(raw: string | null | undefined): FactorCategory {
  if (!raw) return 'custom';
  const key = raw.trim().toLowerCase();
  if (CATEGORY_ALIAS[key]) return CATEGORY_ALIAS[key]!;
  return (VALID_CATEGORIES as string[]).includes(raw) ? (raw as FactorCategory) : 'custom';
}

function normalizeFrequency(raw: string | null | undefined): UpdateFrequency {
  if (!raw) return 'Daily';
  const key = raw.trim().toLowerCase();
  return FREQ_ALIAS[key] ?? 'Daily';
}

function num(v: number | null | undefined): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : 0;
}

/** 清洗服务的因子定义 -> 前端 Factor */
export function toFactor(d: FactorDefinition): Factor {
  const m = d.metrics;
  const author: Factor['author'] =
    d.author && d.author !== 'system' && d.author !== 'builtin' ? 'user' : 'system';

  const frequency = normalizeFrequency(d.frequency);
  // 分级：无评估记录时三项分值均为 0（卡片显示“未评估”）
  const grading = m
    ? {
        // 非日频因子的 IC 阈值整体下调 0.02
        lowFrequency: frequency !== 'Daily',
      }
    : undefined;
  const ranks = m
    ? {
        icRank: gradeIC(num(m?.ic_mean), grading).score,
        irRank: gradeIR(num(m?.ir), grading).score,
        sharpeRank: gradeSharpe(num(m?.sharpe_ratio), grading).score,
      }
    : { icRank: 0, irRank: 0, sharpeRank: 0 };

  return {
    ...ranks,
    // 清洗服务以 code 为唯一标识，直接复用为 id
    id: d.code,
    code: d.code,
    name: d.name || d.code,
    category: normalizeCategory(d.category),
    // 说明优先取服务端（factor.definitions.description，003 迁移）；
    // 未播种的因子回退到本地说明表，再回退到公式描述
    description:
      (d.description ?? '').trim() ||
      describeFactor(d.code, {
        formula: d.formula ?? undefined,
        name: d.name,
      }),
    formula: d.formula ?? '',
    dataSources: d.data_sources ?? [],
    frequency,
    status: (d.status as Factor['status']) ?? 'active',
    author,
    createdAt: d.created_at ?? '',

    // 效能指标：无评估记录时为 0，UI 按“未评估”展示
    icMean: num(m?.ic_mean),
    icStd: num(m?.ic_std),
    ir: num(m?.ir),
    winRate: num(m?.win_rate),
    maxDrawdown: num(m?.max_drawdown),
    sharpeRatio: num(m?.sharpe_ratio),

    // 时间序列：清洗服务暂未提供，留空由 UI 降级
    dates: [],
    icSeries: [],
    longReturns: [],
    shortReturns: [],
    benchmarkReturns: [],
    mockUniverseValues: [],

    // 来源服务状态（backend-owned 语义）：用于“dc 离线”徽标
    available: d.available ?? true,
    serviceCode: d.service_code ?? '',
    serviceStatus: d.service_status ?? 'unknown',
  };
}

/** AI 生成的描述文案（按类别给出语义化提示） */
const AI_DESCRIPTIONS: Record<FactorCategory, string> = {
  momentum: '捕捉多周期价格通道中的非线性动量极值',
  volatility: '刻画收益率波动的不对称性与尾部风险',
  value: '基于估值与账面资本的反向溢价度量',
  growth: '衡量盈利与营收的边际成长加速度',
  size: '基于总/流通市值的规模溢价，小市值常具超额收益',
  sentiment: '反映资金流向与换手活跃度的情绪强度',
  technical: '结合趋势与超买超卖的技术形态指标',
  custom: '复合价量特征的自适应探索因子',
};

export const factorService = {
  async getFactors(): Promise<Factor[]> {
    const list = await listFactorsApi({ with_metrics: true });
    return (list ?? []).map(toFactor);
  },

  async getFactorById(id: string): Promise<Factor | undefined> {
    const list = await listFactorsApi({ with_metrics: true });
    const hit = (list ?? []).find((d) => d.code === id);
    return hit ? toFactor(hit) : undefined;
  },

  async createFactor(data: Partial<Factor>): Promise<Factor> {
    const created = await createFactorApi({
      code: (data.code || `CUSTOM_${Date.now()}`).toUpperCase(),
      name: data.name || '未命名因子',
      category: data.category || 'custom',
      frequency: data.frequency === 'Daily' ? '1d' : (data.frequency ?? '1d'),
      formula: data.formula || 'close',
      data_sources: data.dataSources?.length ? data.dataSources : ['adj_close'],
    });
    return toFactor(created);
  },

  async updateFactor(id: string, data: Partial<Factor>): Promise<Factor> {
    await updateFactorApi(id, {
      name: data.name,
      category: data.category,
      frequency: data.frequency === 'Daily' ? '1d' : data.frequency,
      formula: data.formula,
      data_sources: data.dataSources,
    });
    // 更新接口仅返回 {code, status}，回读一次拿完整定义
    const refreshed = await this.getFactorById(id);
    return refreshed ?? ({ ...(data as Factor), id } as Factor);
  },

  async deleteFactor(id: string): Promise<void> {
    await deleteFactorApi(id);
  },

  /** 相关性矩阵（基于清洗服务已落库的因子值）；返回矩阵与缺失因子 */
  async getCorrelationMatrix(codes: string[]): Promise<{
    matrix: Record<string, Record<string, number>>;
    missing: string[];
  }> {
    const res = await factorCorrelationApi(codes);
    const matrix: Record<string, Record<string, number>> = {};
    for (const [a, row] of Object.entries(res?.correlation ?? {})) {
      matrix[a] = {};
      for (const [b, v] of Object.entries(row ?? {})) {
        matrix[a]![b] = typeof v === 'number' && Number.isFinite(v) ? v : 0;
      }
    }
    return { matrix, missing: res?.missing ?? [] };
  },

  async generateAIFactor(category: FactorCategory): Promise<Factor> {
    const gen = await aiGenerateFactorApi({
      description: AI_DESCRIPTIONS[category] ?? AI_DESCRIPTIONS.custom,
      category: category === 'custom' ? undefined : category,
    });
    return toFactor(gen);
  },

  // 组合回测：清洗服务暂无对应引擎，backtest-studio 仍直接使用本地模拟
  // （./mock/factor-data 的 runMultiFactorBacktest），此处不再重复包装。

  /** 清洗服务实时状态（用于“dc 离线”提示） */
  async getServices(): Promise<CleanerServiceStatus[]> {
    return (await listFactorServicesApi()) ?? [];
  },
};
