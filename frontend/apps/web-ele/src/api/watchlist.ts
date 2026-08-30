/**
 * 自选股管理 API 接口层
 * 对接后端 /api/v1/watchlist/ 下的 CRUD 端点（按用户隔离）
 */

import { requestClient } from '#/api/request';

// ============ 类型定义 ============

/** 自选股条目 */
export interface WatchlistItem {
  id: number;
  user_id: number;
  code: string;
  name?: string | null;
  note?: string | null;
  created_at: string;
  updated_at?: string | null;
}

/** 新增/修改请求 */
export interface WatchlistItemPayload {
  code: string;
  name?: string;
  note?: string;
}

// ============ API 接口 ============

/** 获取自选股列表 */
export async function getWatchlistApi(params?: { search?: string }) {
  return requestClient.get<WatchlistItem[]>('/watchlist', { params });
}

/** 新增一只自选股 */
export async function createWatchlistApi(data: WatchlistItemPayload) {
  return requestClient.post<WatchlistItem>('/watchlist', data);
}

/** 批量导入自选股 */
export async function bulkCreateWatchlistApi(items: WatchlistItemPayload[]) {
  return requestClient.post<WatchlistItem[]>('/watchlist/bulk', { items });
}

/** 修改自选股（名称/备注） */
export async function updateWatchlistApi(id: number, data: Partial<WatchlistItemPayload>) {
  return requestClient.put<WatchlistItem>(`/watchlist/${id}`, data);
}

/** 删除自选股 */
export async function deleteWatchlistApi(id: number) {
  return requestClient.delete(`/watchlist/${id}`);
}
