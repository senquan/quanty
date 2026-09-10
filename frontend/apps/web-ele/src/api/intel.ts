import type {
  AuthorProfile,
  IntelUploadExtraction,
  IntelUploadFileResult,
  IntelUploadResult,
  MentionPage,
  MentionQuery,
  RsshubRunResult,
  RsshubSource,
  RsshubSourceList,
  RsshubStatus,
  RsshubTestResult,
} from '../views/data/news-analysis/types';

/**
 * 资讯分析 intel 数据接口层
 *
 * 对接主后端（:8000）/api/v1/intel 系列路由（读 Postgres intel schema，由 data-cleaner 产出）。
 * 主后端返回统一包装 { code, data, msg }，requestClient 已配置 responseReturn:'data'，
 * 故调用方直接拿到 data 解包后的内容（即 NewsMention[] / AuthorProfile[]）。
 */
import { requestClient } from '#/api/request';

/**
 * 资讯抽取结果（**服务端分页**）
 *
 * 数据随每日构建持续增长（已 800+ 条），全量下发既拖慢首屏，也让前端筛选失真
 * （只能筛到已下载的那一页）。故关键字 / 倾向 / 来源 / 分页全部下推到后端，
 * 返回 { items, total, page, pageSize, sources }。
 */
export async function getIntelMentionsApi(
  params: MentionQuery = {},
): Promise<MentionPage> {
  return requestClient.get<MentionPage>('/intel/mentions', { params });
}

/** 作者/来源画像列表 */
export async function getIntelProfilesApi(): Promise<AuthorProfile[]> {
  return requestClient.get<AuthorProfile[]>('/intel/profiles');
}

// --------------------------------------------------------------------------
// P4-3 RSSHub 源管理（主后端转发 dc，前端不直连 dc）
// --------------------------------------------------------------------------
export async function getRsshubStatusApi(): Promise<RsshubStatus> {
  return requestClient.get<RsshubStatus>('/intel/rsshub/status');
}

export async function listRsshubSourcesApi(): Promise<RsshubSourceList> {
  return requestClient.get<RsshubSourceList>('/intel/rsshub/sources');
}

/** 登记一个 RSSHub 源（默认停用，用户确认能拉通再启用） */
export async function addRsshubSourceApi(params: {
  credibility?: string;
  enabled?: boolean;
  name: string;
  url: string;
}): Promise<RsshubSource> {
  return requestClient.post<RsshubSource>('/intel/rsshub/sources', params);
}

/** 改名 / 改 URL / 启停（只传要改的字段） */
export async function updateRsshubSourceApi(
  id: number,
  patch: {
    credibility?: string;
    enabled?: boolean;
    name?: string;
    url?: string;
  },
): Promise<{ id: number; updated: boolean }> {
  // ⚠️ Vben 的 RequestClient 只暴露 get/post/put/delete，没有 patch
  // （见 packages/effects/request/src/request-client/request-client.ts:99~139）。
  // 后端这里是 PATCH 语义（局部更新，exclude_none），故走底层 request() 指定 method。
  return requestClient.request<{ id: number; updated: boolean }>(
    `/intel/rsshub/sources/${id}`,
    { data: patch, method: 'PATCH' },
  );
}

/** 删除源（该源已有文档时 dc 返回 409，前端应提示改为停用） */
export async function deleteRsshubSourceApi(
  id: number,
): Promise<{ deleted: boolean; id: number }> {
  return requestClient.delete<{ deleted: boolean; id: number }>(
    `/intel/rsshub/sources/${id}`,
  );
}

/** 立即跑一轮摄取（只拉已启用的源）；源多时较慢，放宽超时 */
export async function runRsshubIngestApi(): Promise<RsshubRunResult> {
  return requestClient.post<RsshubRunResult>(
    '/intel/rsshub/run',
    {},
    { timeout: 300_000 },
  );
}

/** 试探 feed URL 能否拉通（不入库、不写 health） */
export async function testRsshubUrlApi(
  url: string,
  limit = 10,
): Promise<RsshubTestResult> {
  return requestClient.post<RsshubTestResult>('/intel/rsshub/test', {
    limit,
    url,
  });
}

/**
 * 批量投喂文章文件（P4-1 人工投喂）
 *
 * 主后端 POST /api/v1/intel/upload 转发至 data-cleaner 同名端点，
 * 支持 html/htm/mhtml（浏览器导出、剪藏）/txt/md（纯文本）/csv、txt（URL 清单）。
 *
 * 注意两个坑：
 *   1. 必须把 Content-Type 置为 undefined，让浏览器自动补 multipart boundary；
 *      若沿用 requestClient 默认的 application/json 会直接 422。
 *   2. 默认 10s 超时不够（批量解析+入库），显式放宽；开启「立即抽取」后 dc 会
 *      同步跑 LLM（约 3~4s/篇），超时要再放大。
 */
export async function uploadIntelArticlesApi(params: {
  dryRun?: boolean;
  /** 入库后立即对本次新增文档跑理解层抽取（会产生 LLM 成本） */
  extract?: boolean;
  /** 立即抽取的篇数上限；不传由 dc 决定 */
  extractLimit?: number;
  files: File[];
  sourceName?: string;
  sourceType?: 'manual' | 'wechat';
}): Promise<IntelUploadResult> {
  const form = new FormData();
  params.files.forEach((f) => form.append('files', f, f.name));
  form.append('source_name', params.sourceName || '');
  form.append('source_type', params.sourceType || 'manual');
  form.append('dry_run', params.dryRun ? 'true' : 'false');
  if (params.extract) {
    form.append('extract', 'true');
    if (params.extractLimit && params.extractLimit > 0) {
      form.append('extract_limit', String(params.extractLimit));
    }
  }

  return requestClient.post<IntelUploadResult>('/intel/upload', form, {
    headers: { 'Content-Type': undefined },
    timeout: params.extract ? 900_000 : 300_000,
  });
}

export type {
  IntelUploadExtraction,
  IntelUploadFileResult,
  IntelUploadResult,
};
