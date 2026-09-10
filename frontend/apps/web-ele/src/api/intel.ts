/**
 * 资讯分析 intel 数据接口层
 *
 * 对接主后端（:8000）/api/v1/intel 系列路由（读 Postgres intel schema，由 data-cleaner 产出）。
 * 主后端返回统一包装 { code, data, msg }，requestClient 已配置 responseReturn:'data'，
 * 故调用方直接拿到 data 解包后的内容（即 NewsMention[] / AuthorProfile[]）。
 */
import { requestClient } from '#/api/request';

import type {
  AuthorProfile,
  IntelUploadExtraction,
  IntelUploadFileResult,
  IntelUploadResult,
  NewsMention,
} from '../views/data/news-analysis/types';

/** 资讯抽取结果列表 */
export async function getIntelMentionsApi(): Promise<NewsMention[]> {
  return requestClient.get<NewsMention[]>('/intel/mentions');
}

/** 作者/来源画像列表 */
export async function getIntelProfilesApi(): Promise<AuthorProfile[]> {
  return requestClient.get<AuthorProfile[]>('/intel/profiles');
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
