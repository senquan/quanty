/**
 * 资讯分析数据服务
 *
 * 已接真数据：通过 src/api/intel.ts 调用主后端
 *   GET /api/v1/intel/mentions   → NewsMention[]
 *   GET /api/v1/intel/profiles   → AuthorProfile[]
 * （读 Postgres intel schema，由 data-cleaner 产出；后端端点见 backend/app/api/api_v1/endpoints/intel.py）
 *
 * 如需回退到本地 mock（断网/后端未起时审阅 UI），把下面两个方法改回：
 *   return delay(mockMentions);
 *   return delay(mockProfiles);
 * 并恢复上方对 './mock/news-data' 的 import 即可，组件无需改动。
 */

import type {
  AuthorProfile,
  IntelUploadResult,
  NewsMention,
} from './types';

import {
  getIntelMentionsApi,
  getIntelProfilesApi,
  uploadIntelArticlesApi,
} from '#/api/intel';

export const newsService = {
  async getMentions(): Promise<NewsMention[]> {
    return getIntelMentionsApi();
  },

  async getProfiles(): Promise<AuthorProfile[]> {
    return getIntelProfilesApi();
  },

  /** 批量投喂文章文件（P4-1），返回入库统计 */
  async uploadArticles(params: {
    dryRun?: boolean;
    /** 入库后立即跑理解层抽取（花钱） */
    extract?: boolean;
    extractLimit?: number;
    files: File[];
    sourceName?: string;
    sourceType?: 'manual' | 'wechat';
  }): Promise<IntelUploadResult> {
    return uploadIntelArticlesApi(params);
  },
};
