/**
 * 清洗服务网关 API 接口层
 * 对接主后端（:8000）/api/v1/cleaner 系列路由（阶段 B / 阶段 C）：
 *   - 服务注册 / 列表 / 详情 / 更新 / 删除
 *   - 连接测试 / QoS 轮询 / 因子同步
 *   - 聚合因子底册（FactorRegistry）
 *
 * 主后端返回统一包装 { code, data, msg }，故使用 requestClient（responseReturn: 'data'），
 * 调用方直接拿到 data 解包后的内容。
 */

import { requestClient } from '#/api/request';

// ============ 类型定义 ============

/** 清洗服务（对应 CleanerServiceOut） */
export interface CleanerServiceItem {
  id: number;
  service_code: string;
  name: string;
  base_url: string;
  status: string;
  last_heartbeat: string | null;
  qos: Record<string, unknown> | null;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

/** 注册 / 新建服务负载 */
export interface CleanerServiceCreatePayload {
  service_code: string;
  name: string;
  base_url: string;
  api_key: string;
}

/** 更新服务负载（全可选） */
export interface CleanerServiceUpdatePayload {
  name?: string;
  base_url?: string;
  api_key?: string;
  is_active?: boolean;
}

/** 因子注册表条目（FactorRegistryOut） */
export interface FactorRegistryItem {
  id: number;
  service_code: string;
  factor_code: string;
  name: string;
  category: string | null;
  frequency: string | null;
  description: string | null;
  formula: string | null;
  data_source: string | null;
  is_enabled: boolean;
  last_sync: string | null;
}

/** 连接测试结果（ConnectionTestResult） */
export interface ConnectionTestResult {
  ok: boolean;
  status: string | null;
  factor_count: number | null;
  message: string | null;
}

/** 批量勾选/取消入库因子负载 */
export interface FactorEnablePayload {
  service_code?: string;
  factor_codes?: string[];
  is_enabled?: boolean;
}

// ============ 服务管理 ============

/** 注册清洗服务（注册时会测试连接） */
export async function registerCleanerServiceApi(data: CleanerServiceCreatePayload) {
  return requestClient.post<CleanerServiceItem>('/cleaner', data);
}

/** 清洗服务列表 */
export async function listCleanerServicesApi() {
  return requestClient.get<CleanerServiceItem[]>('/cleaner');
}

/** 单个服务详情 */
export async function getCleanerServiceApi(serviceCode: string) {
  return requestClient.get<CleanerServiceItem>(
    `/cleaner/${encodeURIComponent(serviceCode)}`,
  );
}

/** 更新服务配置 */
export async function updateCleanerServiceApi(
  serviceCode: string,
  data: CleanerServiceUpdatePayload,
) {
  return requestClient.put<CleanerServiceItem>(
    `/cleaner/${encodeURIComponent(serviceCode)}`,
    data,
  );
}

/** 删除服务（级联移除其因子登记） */
export async function deleteCleanerServiceApi(serviceCode: string) {
  return requestClient.delete<{ code: number; msg: string }>(
    `/cleaner/${encodeURIComponent(serviceCode)}`,
  );
}

// ============ 服务运维 ============

/** 测试连接（不落库） */
export async function testCleanerServiceApi(serviceCode: string) {
  return requestClient.post<ConnectionTestResult>(
    `/cleaner/${encodeURIComponent(serviceCode)}/test`,
  );
}

/** 手动触发 QoS 轮询 */
export async function pollCleanerQosApi(serviceCode: string) {
  return requestClient.post<Record<string, unknown>>(
    `/cleaner/${encodeURIComponent(serviceCode)}/qos`,
  );
}

/** 拉取并入库该服务的因子口径（返回 data: { synced, status }） */
export async function syncCleanerFactorsApi(serviceCode: string) {
  return requestClient.post<{ synced: number; status: string }>(
    `/cleaner/${encodeURIComponent(serviceCode)}/sync`,
  );
}

// ============ 因子注册表 ============

/** 批量勾选/取消入库因子（返回 data: { updated }） */
export async function enableCleanerFactorsApi(
  serviceCode: string,
  data: FactorEnablePayload,
) {
  return requestClient.post<{ updated: number }>(
    `/cleaner/${encodeURIComponent(serviceCode)}/factors/enable`,
    data,
  );
}

/** 聚合因子底册（可按服务 / 仅启用过滤） */
export async function listFactorRegistryApi(params?: {
  service_code?: string;
  only_enabled?: boolean;
}) {
  return requestClient.get<FactorRegistryItem[]>('/cleaner/factors/registry', {
    params,
  });
}
