/**
 * 因子策略相关共享 UI 主题与格式化工具
 */
import {
  Activity,
  Flame,
  Landmark,
  Scale,
  Settings,
  TrendingUp,
  Zap,
} from '@lucide/vue';
import type { Component } from 'vue';

import type {
  FactorStrategyConfig,
  NeutralizeMode,
  RebalanceConfig,
  UniverseType,
  WeightMode,
} from '#/api/factor-strategy';

/** 因子类别视觉主题（与因子底册页保持一致） */
export interface CategoryTheme {
  label: string;
  type: 'danger' | 'info' | 'primary' | 'success' | 'warning';
  icon: Component;
  color: string;
}

export const categoryThemes: Record<string, CategoryTheme> = {
  momentum: { label: '动量类', type: 'warning', icon: TrendingUp, color: '#e6a23c' },
  volatility: { label: '波动率类', type: 'danger', icon: Activity, color: '#f56c6c' },
  value: { label: '价值类', type: 'success', icon: Landmark, color: '#67c23a' },
  growth: { label: '成长类', type: 'primary', icon: Zap, color: '#409eff' },
  size: { label: '规模类', type: 'success', icon: Scale, color: '#67c23a' },
  sentiment: { label: '情绪类', type: 'info', icon: Flame, color: '#909399' },
  technical: { label: '技术类', type: 'primary', icon: Activity, color: '#36cfc9' },
  custom: { label: '自定义类', type: 'info', icon: Settings, color: '#9b59b6' },
};

export function categoryTheme(cat?: string | null): CategoryTheme {
  return (categoryThemes[cat || ''] ?? categoryThemes.custom) as CategoryTheme;
}

/** 权重模式展示 */
export const weightModeLabels: Record<WeightMode, string> = {
  auto_ir: '按 IR 自动',
  manual: '手动权重',
};

/** 中性化模式展示 */
export const neutralizeLabels: Record<NeutralizeMode, string> = {
  industry: '行业中性化',
  standardize: '仅标准化',
};

/** 标的股票池（板块）展示 */
export const universeLabels: Record<UniverseType, string> = {
  bj: '北交所',
  main: '沪深主板',
  cyb: '创业板',
  kcb: '科创板',
};

/** 标的股票池汇总文案（板块多选 + 自选股） */
export function universeSummary(
  universe: undefined | UniverseType[],
  customCodes: undefined | string[],
): string {
  const parts = (universe || []).map((u) => universeLabels[u]).filter(Boolean);
  if (customCodes && customCodes.length > 0) {
    parts.push(`自选股(${customCodes.length})`);
  }
  return parts.length > 0 ? parts.join('+') : '全市场';
}

/** 将配置中的 universe 规整为板块数组（兼容旧的单字符串配置） */
export function normalizeUniverse(u: unknown): UniverseType[] {
  if (Array.isArray(u)) {
    return u.filter(
      (x): x is UniverseType =>
        x === 'main' || x === 'cyb' || x === 'kcb' || x === 'bj',
    );
  }
  if (
    typeof u === 'string' &&
    (u === 'main' || u === 'cyb' || u === 'kcb' || u === 'bj')
  ) {
    return [u];
  }
  return [];
}

/** 调仓周期展示 */
export function rebalanceLabel(rb?: RebalanceConfig): string {
  if (!rb) return '每周';
  if (rb.freq === 'monthly') return '每月';
  if (rb.freq === 'every_n_days') return `每 ${rb.every_n_days || 5} 日`;
  return '每周';
}

/** 默认配置 */
export function defaultConfig(): FactorStrategyConfig {
  return {
    factor_codes: [],
    weights: {},
    weight_mode: 'auto_ir',
    neutralize: 'industry',
    top_n: 30,
    rebalance: { freq: 'weekly' },
    trade_time: '10:00',
    initial_capital: 1_000_000,
    filters: {
      exclude_st: true,
      min_list_days: 60,
      exclude_suspended: true,
      exclude_limit_up: true,
      exclude_limit_down: false,
      min_cap: null,
    },
    lookback_days: 60,
    universe: [],
    custom_codes: [],
    is_active: false,
  };
}

/** 数值着色 class（正绿负红） */
export function pnlClass(v: number): string {
  return v >= 0 ? 'text-emerald-500' : 'text-rose-500';
}

/** 百分比格式化 */
export function pct(v: number, digits = 2): string {
  return `${(v * 100).toFixed(digits)}%`;
}
