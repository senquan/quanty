/**
 * 资讯分析数据服务
 *
 * 已接真数据：通过 src/api/intel.ts 调用主后端
 *   GET /api/v1/intel/mentions   → MentionPage（服务端分页 + 筛选）
 *   GET /api/v1/intel/profiles   → AuthorProfile[]
 *   /api/v1/intel/rsshub/*       → RSSHub 源管理（P4-3）
 * （读 Postgres intel schema，由 data-cleaner 产出；后端端点见 backend/app/api/api_v1/endpoints/intel.py）
 *
 * 如需回退到本地 mock（断网/后端未起时审阅 UI），把 getMentions 改回：
 *   return { items: mockMentions, total: mockMentions.length, page: 1,
 *            pageSize: mockMentions.length, sources: [] };
 * 并恢复上方对 './mock/news-data' 的 import 即可，组件无需改动。
 */

import type {
  AuthorProfile,
  IntelUploadResult,
  MentionPage,
  MentionQuery,
  RsshubRunResult,
  RsshubSourceList,
  RsshubStatus,
  RsshubTestResult,
} from './types';

import {
  addRsshubSourceApi,
  deleteRsshubSourceApi,
  getIntelMentionsApi,
  getIntelProfilesApi,
  getRsshubStatusApi,
  listRsshubSourcesApi,
  runRsshubIngestApi,
  testRsshubUrlApi,
  updateRsshubSourceApi,
  uploadIntelArticlesApi,
} from '#/api/intel';

export const newsService = {
  /** 资讯抽取（服务端分页，默认 20 行一页） */
  async getMentions(params: MentionQuery = {}): Promise<MentionPage> {
    return getIntelMentionsApi(params);
  },

  async getProfiles(): Promise<AuthorProfile[]> {
    return getIntelProfilesApi();
  },

  // ---- P4-3 RSSHub 源管理 ----
  async getRsshubStatus(): Promise<RsshubStatus> {
    return getRsshubStatusApi();
  },

  async listRsshubSources(): Promise<RsshubSourceList> {
    return listRsshubSourcesApi();
  },

  async addRsshubSource(params: {
    credibility?: string;
    enabled?: boolean;
    name: string;
    url: string;
  }) {
    return addRsshubSourceApi(params);
  },

  async updateRsshubSource(
    id: number,
    patch: {
      credibility?: string;
      enabled?: boolean;
      name?: string;
      url?: string;
    },
  ) {
    return updateRsshubSourceApi(id, patch);
  },

  async deleteRsshubSource(id: number) {
    return deleteRsshubSourceApi(id);
  },

  async runRsshubIngest(): Promise<RsshubRunResult> {
    return runRsshubIngestApi();
  },

  async testRsshubUrl(url: string, limit = 10): Promise<RsshubTestResult> {
    return testRsshubUrlApi(url, limit);
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
