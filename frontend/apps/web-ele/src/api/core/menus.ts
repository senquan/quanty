/** 菜单管理 API 接口层 */

import { requestClient } from '#/api/request';

export interface Menu {
  id: number;
  name: string;
  type: number;
  path: string;
  label: string;
  component?: string | null;
  icon?: string | null;
  oidx: number;
  parent_id?: number | null;
  is_enabled: boolean;
  is_cached: boolean;
  is_hidden: boolean;
  permission?: string | null;
  created_at?: string;
  updated_at?: string | null;
}

export interface MenuCreate {
  name: string;
  type: number;
  path: string;
  label: string;
  component?: string;
  icon?: string;
  oidx: number;
  parent_id: number;
  is_enabled: boolean;
  is_cached: boolean;
  is_hidden: boolean;
  permission?: string;
}

export type MenuUpdate = Partial<MenuCreate>;

export async function getMenusApi(params?: { search?: string; parent_id?: number }) {
  return requestClient.get<Menu[]>('/system/menus/', { params });
}

export async function getMenuApi(id: number) {
  return requestClient.get<Menu>(`/system/menus/${id}`);
}

export async function createMenuApi(data: MenuCreate) {
  return requestClient.post<Menu>('/system/menus/', data);
}

export async function updateMenuApi(id: number, data: MenuUpdate) {
  return requestClient.put<Menu>(`/system/menus/${id}`, data);
}

export async function deleteMenuApi(id: number) {
  return requestClient.delete(`/system/menus/${id}`);
}
