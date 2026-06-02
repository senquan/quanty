import type { UserInfo } from '@vben/types';

import { requestClient } from '#/api/request';

/** 获取当前登录用户信息（个人中心） */
export async function getUserInfoApi() {
  return requestClient.get<UserInfo>('/user/info');
}

/** 系统用户 */
export interface SystemUser {
  id: number;
  username: string;
  email: string;
  full_name?: string | null;
  phone?: string | null;
  is_active: boolean;
  role_id?: number | null;
  role?: { id: number; name: string } | null;
  created_at: string;
  updated_at?: string | null;
}

export interface UserListResult {
  items: SystemUser[];
  total: number;
}

export interface UserCreate {
  username: string;
  email: string;
  password: string;
  full_name?: string;
  phone?: string;
  is_active: boolean;
  role_id?: number | null;
}

export type UserUpdate = Partial<Omit<UserCreate, 'password'>>;

export async function getUsersApi(params?: {
  skip?: number;
  limit?: number;
  search?: string;
}) {
  return requestClient.get<UserListResult>('/users/', { params });
}

export async function getUserApi(id: number) {
  return requestClient.get<SystemUser>(`/users/${id}`);
}

export async function createUserApi(data: UserCreate) {
  return requestClient.post<SystemUser>('/users/', data);
}

export async function updateUserApi(id: number, data: UserUpdate) {
  return requestClient.put<SystemUser>(`/users/${id}`, data);
}

export async function deleteUserApi(id: number) {
  return requestClient.delete(`/users/${id}`);
}
