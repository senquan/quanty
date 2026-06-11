import { baseRequestClient, requestClient } from '#/api/request';

export namespace AuthApi {
  /** 登录接口参数 */
  export interface LoginParams {
    password?: string;
    username?: string;
  }

  /** 登录接口返回值 - 后端响应格式 */
  export interface LoginResultRaw {
    access_token: string;
    refresh_token: string;
    token_type: string;
  }

  /** 登录接口返回值 - 前端使用格式 */
  export interface LoginResult {
    accessToken: string;
    refreshToken: string;
    tokenType: string;
  }

  export interface RefreshTokenResult {
    data: string;
    status: number;
  }
}

/**
 * 登录 - 使用 OAuth2PasswordRequestForm (form-urlencoded)
 */
export async function loginApi(data: AuthApi.LoginParams) {
  // 构建 form-urlencoded 格式的数据
  const formData = new URLSearchParams();
  if (data.username) formData.append('username', data.username);
  if (data.password) formData.append('password', data.password);

  const response = await requestClient.post<AuthApi.LoginResultRaw>(
    '/auth/login',
    formData.toString(),
    {
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
      },
    },
  );

  // 转换响应字段名
  return {
    accessToken: response.access_token,
    refreshToken: response.refresh_token,
    tokenType: response.token_type,
  };
}

/**
 * 刷新accessToken
 */
export async function refreshTokenApi() {
  return baseRequestClient.post<AuthApi.RefreshTokenResult>('/auth/refresh', {
    withCredentials: true,
  });
}

/**
 * 退出登录
 */
export async function logoutApi() {
  return baseRequestClient.post('/auth/logout', {
    withCredentials: true,
  });
}

/**
 * 获取用户权限码
 */
export async function getAccessCodesApi() {
  return requestClient.get<string[]>('/auth/codes');
}
