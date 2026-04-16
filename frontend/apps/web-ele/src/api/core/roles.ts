/** 角色管理 API 接口层 */

import { requestClient } from '#/api/request';

export interface Role {
  id: number;
  name: string;
  code: string;
  description: string;
  is_active: boolean;
  menu_ids: number[];
  created_at: string;
}

export interface RoleCreate {
  name: string;
  code: string;
  description: string;
  is_active: boolean;
}

export async function getRolesApi() {
  return requestClient.get<Role[]>('/roles/');
}

export async function getRoleApi(id: number) {
  return requestClient.get<Role>(`/roles/${id}`);
}

export async function createRoleApi(data: RoleCreate) {
  return requestClient.post<Role>('/roles/', data);
}

export async function updateRoleApi(id: number, data: Partial<RoleCreate>) {
  return requestClient.put<Role>(`/roles/${id}`, data);
}

export async function deleteRoleApi(id: number) {
  return requestClient.delete(`/roles/${id}`);
}
