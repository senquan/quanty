/**
 * 因子效能分级（IC / IR / 夏普比率）
 *
 * 分级阈值（默认口径，日频因子）：
 *   IC    S ≥0.07 | A ≥0.05 | B ≥0.03 | C ≥0.02 | D <0.02
 *   IR    S ≥1.0  | A ≥0.7  | B ≥0.5  | C ≥0.3  | D <0.3
 *   夏普  S ≥2.0  | A ≥1.0  | B ≥0.7  | C ≥0.3  | D <0.3
 *
 * 分值：S=5 A=4 B=3 C=2 D=1（对应 Factor.icRank / irRank / sharpeRank）
 *
 * 关于绝对值：反转型因子的 IC/IR/夏普为负但稳定有效，其预测能力并不弱；
 * 因此对三个指标统一取绝对值参与分级（可用 useAbsolute=false 关闭）。
 *
 * 阈值可调项（对应不同策略场景）：
 *   lowFrequency  周频/月频因子：IC 阈值整体下调 0.02
 *   neutralized   行业中性化后的因子：C 级 IC 下限放宽至 0.015
 *   longOnly      纯多头组合：夏普 S 级下调至 1.5，其余下调 0.3
 *   monthly       低频月频因子：夏普 C 级下限放宽至 0.2
 */

export type GradeLevel = 'S' | 'A' | 'B' | 'C' | 'D';

export interface Grade {
  level: GradeLevel;
  /** S=5 A=4 B=3 C=2 D=1 */
  score: number;
  label: string;
}

export interface GradeOptions {
  lowFrequency?: boolean;
  neutralized?: boolean;
  longOnly?: boolean;
  monthly?: boolean;
  /** 是否取绝对值参与分级（默认 true） */
  useAbsolute?: boolean;
}

const GRADES: Record<GradeLevel, Grade> = {
  S: { level: 'S', score: 5, label: '优秀' },
  A: { level: 'A', score: 4, label: '良好' },
  B: { level: 'B', score: 3, label: '合格' },
  C: { level: 'C', score: 2, label: '弱效' },
  D: { level: 'D', score: 1, label: '无效' },
};

const IC_BASE = { S: 0.07, A: 0.05, B: 0.03, C: 0.02 };
const IR_BASE = { S: 1.0, A: 0.7, B: 0.5, C: 0.3 };
const SHARPE_BASE = { S: 2.0, A: 1.0, B: 0.7, C: 0.3 };

interface Thresholds {
  S: number;
  A: number;
  B: number;
  C: number;
}

function icThresholds(o: GradeOptions): Thresholds {
  const off = o.lowFrequency ? 0.02 : 0;
  return {
    S: IC_BASE.S - off,
    A: IC_BASE.A - off,
    B: IC_BASE.B - off,
    // 行业中性化后收益特征变化，C 级下限单独放宽
    C: o.neutralized ? 0.015 : IC_BASE.C - off,
  };
}

function irThresholds(): Thresholds {
  // IR 阈值不随频率/中性化调整
  return { ...IR_BASE };
}

function sharpeThresholds(o: GradeOptions): Thresholds {
  if (o.longOnly) {
    // 纯多头组合：S 级下调至 1.5，其余同步下调 0.3（下限 0）
    return {
      S: 1.5,
      A: Math.max(SHARPE_BASE.A - 0.3, 0),
      B: Math.max(SHARPE_BASE.B - 0.3, 0),
      C: Math.max(SHARPE_BASE.C - 0.3, 0),
    };
  }
  return {
    ...SHARPE_BASE,
    // 低频月频策略波动更低，C 级下限放宽至 0.2
    C: o.monthly ? 0.2 : SHARPE_BASE.C,
  };
}

function gradeBy(value: number, t: Thresholds): Grade {
  if (!Number.isFinite(value)) return GRADES.D;
  const v = Math.abs(value);
  if (v >= t.S) return GRADES.S;
  if (v >= t.A) return GRADES.A;
  if (v >= t.B) return GRADES.B;
  if (v >= t.C) return GRADES.C;
  return GRADES.D;
}

function normalize(value: number, o: GradeOptions): number {
  return o.useAbsolute === false ? value : Math.abs(value);
}

/** IC 均值分级（按绝对值） */
export function gradeIC(icMean: number, o: GradeOptions = {}): Grade {
  return gradeBy(normalize(icMean, o), icThresholds(o));
}

/** 信息比率 IR 分级 */
export function gradeIR(ir: number, o: GradeOptions = {}): Grade {
  return gradeBy(normalize(ir, o), irThresholds());
}

/** 夏普比率分级 */
export function gradeSharpe(sharpe: number, o: GradeOptions = {}): Grade {
  return gradeBy(normalize(sharpe, o), sharpeThresholds(o));
}

/** 综合分值：三项均值（四舍五入取整，1~5）；缺失项不计入 */
export function overallScore(scores: (number | undefined | null)[]): number {
  const valid = scores.filter(
    (s): s is number => typeof s === 'number' && s > 0 && Number.isFinite(s),
  );
  if (valid.length === 0) return 0;
  return Math.round(valid.reduce((a, b) => a + b, 0) / valid.length);
}

/** 分值 -> 等级（用于综合评级展示） */
export function gradeFromScore(score: number): Grade | null {
  return Object.values(GRADES).find((g) => g.score === score) ?? null;
}

/** 等级配色（金/橙/蓝/灰蓝/浅灰） */
export function gradeColors(level: GradeLevel): { color: string; bg: string } {
  switch (level) {
    case 'S': {
      return { color: '#b8860b', bg: '#fdf6e3' };
    }
    case 'A': {
      return { color: '#d97706', bg: '#fff7ed' };
    }
    case 'B': {
      return { color: '#2563eb', bg: '#eff6ff' };
    }
    case 'C': {
      return { color: '#64748b', bg: '#f1f5f9' };
    }
    default: {
      return { color: '#9ca3af', bg: '#f9fafb' };
    }
  }
}
