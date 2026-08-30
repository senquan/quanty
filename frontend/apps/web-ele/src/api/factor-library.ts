/**
 * 因子底册库 API 接口层
 *
 * 对接主后端（:8000）/api/v1/factors 系列路由，由主后端代理转发到已登记的
 * data-cleaner 实例，因此前端无需感知清洗服务地址与 X-API-Key。
 *
 * 与 cleaner-gateway 一致：主后端返回统一包装 { code, data, msg }，
 * 使用 requestClient（responseReturn: 'data'），调用方直接拿到 data。
 */

import { requestClient } from '#/api/request';

// ============ 类型定义 ============

/** 效能指标（data-cleaner factor.metrics 一行） */
export interface FactorMetricsRaw {
  factor_code: string;
  as_of_date: string;
  ic_mean: number | null;
  ic_std: number | null;
  ir: number | null;
  sharpe_ratio: number | null;
  max_drawdown: number | null;
  win_rate: number | null;
}

/** 清洗服务侧的因子定义 */
export interface FactorDefinition {
  code: string;
  name: string;
  category: string | null;
  frequency: string | null;
  formula: string | null;
  data_sources: string[] | null;
  author?: string | null;
  status?: string | null;
  created_at?: string | null;
  /** 因子说明（003 迁移新增，存于 factor.definitions.description） */
  description?: string | null;
  /** 由主后端并发补齐的最新一期效能指标，可能为 null */
  metrics?: FactorMetricsRaw | null;
}

/** 相关性矩阵：correlation[a][b] = 数值 */
export interface FactorCorrelationResult {
  codes: string[];
  correlation: Record<string, Record<string, number | null>>;
  /** 无可用因子值、已被跳过的因子代码 */
  missing?: string[];
}

// ============ 接口 ============

/** 因子列表（可带效能指标） */
export async function listFactorsApi(params?: {
  category?: string;
  search?: string;
  with_metrics?: boolean;
}) {
  return requestClient.get<FactorDefinition[]>('/factors', { params });
}

/** 因子详情 */
export async function getFactorApi(code: string) {
  return requestClient.get<FactorDefinition>(
    `/factors/${encodeURIComponent(code)}`,
  );
}

/** 创建自定义因子 */
export async function createFactorApi(data: {
  code: string;
  name: string;
  category?: string;
  frequency?: string;
  formula: string;
  data_sources?: string[];
}) {
  return requestClient.post<FactorDefinition>('/factors', data);
}

/** 更新自定义因子（部分更新） */
export async function updateFactorApi(
  code: string,
  data: {
    name?: string;
    category?: string;
    frequency?: string;
    formula?: string;
    data_sources?: string[];
  },
) {
  return requestClient.put<{ code: string; status: string }>(
    `/factors/${encodeURIComponent(code)}`,
    data,
  );
}

/** 删除自定义因子 */
export async function deleteFactorApi(code: string) {
  return requestClient.delete<{ code: number; msg: string }>(
    `/factors/${encodeURIComponent(code)}`,
  );
}

/** AI 生成因子 */
export async function aiGenerateFactorApi(data: {
  description: string;
  category?: string;
}) {
  return requestClient.post<FactorDefinition>('/factors/ai-generate', data);
}

/** 因子相关性矩阵（基于清洗服务已落库的因子值） */
export async function factorCorrelationApi(codes: string[]) {
  return requestClient.post<FactorCorrelationResult>('/factors/correlation', {
    codes,
  });
}
