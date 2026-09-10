/** 资讯分析模块类型定义 */

export type Stance = 'bearish' | 'bullish' | 'neutral';
export type Horizon = 'event' | 'long' | 'mid' | 'short';
export type ProfileType = 'author' | 'source';

/** 资讯抽取结果（intel.doc_mentions + JOIN documents） */
export interface NewsMention {
  id: string;
  docId: string;
  /** 资讯标题 / 摘要 */
  title: string;
  /** 提及标的（A 股 .SH/.SZ/.BJ） */
  symbol: string;
  stance: Stance;
  /** 模型置信度 0~1 */
  confidence: number;
  horizon: Horizon;
  /** 论点摘要 */
  thesis: string;
  /** 支撑证据（原文片段） */
  evidence: string;
  /** 来源（RSS 源名） */
  source: string;
  /** 作者（源未带 author 时为 null） */
  author: null | string;
  /** 发布时间 */
  publishedAt: string;
}

// ---- 资讯抽取分页（服务端分页：筛选与分页都下推到后端）----
export interface MentionQuery {
  /** 关键字：标题 / 论点 / 证据 / 标的 */
  q?: string;
  stance?: 'all' | Stance;
  source?: string;
  /** 1-based */
  page?: number;
  pageSize?: number;
}

export interface MentionPage {
  items: NewsMention[];
  total: number;
  page: number;
  pageSize: number;
  /** 来源下拉选项（全量，不随分页/筛选变化） */
  sources: string[];
}

/** 作者 / 来源画像（对应 intel.author_profiles） */
export interface AuthorProfile {
  profileKey: string;
  profileType: ProfileType;
  totalMentions: number;
  totalDocs: number;
  uniqueSymbols: number;
  stanceDist: {
    bearish: number;
    bullish: number;
    neutral: number;
  };
  topSymbols: string[];
  dateFirst: string;
  dateLast: string;
  /** 样本不足（mentions < 30 或 accuracy 样本为 0） */
  sampleInsufficient: boolean;
  /** 准确度样本量（需 raw_bars 后续行情，初期为 0） */
  accuracySampleSize: number;
  driftDetected: boolean;

  // ---- P4-4 LLM 风格总结（migrations/016；未生成时为空串 / 空数组） ----
  /** 自然语言风格综述（进画像卡片） */
  styleSummary?: string;
  /** 风格标签（价值投资 / 事件驱动 等，白名单内） */
  styleTags?: string[];
  /** 主要覆盖行业 */
  styleSectors?: string[];
  /** 典型持仓周期 short / mid / long / event */
  styleHoldingPeriod?: string;
  /** 观点笃定程度 high / medium / low */
  styleConviction?: string;
  /** 画像局限与风险提示 */
  styleCaveats?: string;
  /** 总结可靠度 0~1 */
  styleConfidence?: null | number;
  /** ok / schema_fail / api_fail / skipped / ''（未生成） */
  styleStatus?: string;
}

// ---- P4-1 批量投喂（POST /intel/upload）返回结构 ----
export interface IntelUploadFileResult {
  file: string;
  /** ok / dry_run / error */
  status: string;
  error?: string;
  /** dry_run 时解析出的篇数 */
  parsed?: number;
  /** dry_run 时的标题抽样 */
  titles?: string[];
  fetched?: number;
  new?: number;
  dup?: number;
  reposts?: number;
}

/** 上传后立即抽取的结果（仅开启「立即抽取」时返回） */
export interface IntelUploadExtraction {
  /** ok / budget_stopped / skipped / error */
  status: string;
  /** 本次新增、本该抽取的篇数 */
  requested: number;
  /** 实际送进 LLM 的篇数（受 extract_limit 限制） */
  done: number;
  /** 是否因超上限被截断（截断部分留待后续脚本/定时任务） */
  truncated: boolean;
  prescreenHit?: number;
  prescreenMiss?: number;
  extracted?: number;
  noMention?: number;
  quarantined?: number;
  apiFail?: number;
  costCny?: number;
  stoppedReason?: null | string;
  /** status=skipped / error 时的原因 */
  reason?: string;
  error?: string;
}

export interface IntelUploadResult {
  source: string;
  sourceType: string;
  dryRun: boolean;
  /** 前端选中的文件数 */
  uploaded: number;
  /** 通过扩展名/大小校验的文件数 */
  accepted: number;
  /** 从 zip 里解出的文章文件数（便于核对"传 1 个包进来 25 篇"） */
  extracted?: number;
  failed: number;
  fetched: number;
  new: number;
  dup: number;
  reposts: number;
  /** 被拒收的文件（扩展名不支持 / 超限） */
  rejected: { error: string; file: string }[];
  files: IntelUploadFileResult[];
  /** 上传后立即抽取的结果；未开启时为 null */
  extraction?: IntelUploadExtraction | null;
  next?: string;
}

// --------------------------------------------------------------------------
// P4-3 RSSHub 源管理
//
// RSSHub 是**第三方中继**（稳定性与合规性都不保证，见设计文档 §7.1），
// 所以源默认停用，只有用户确认能拉通后手动启用，health 也如实标 degraded。
// --------------------------------------------------------------------------
export interface RsshubHealth {
  /** ok / partial / failed / degraded / ''（从未拉过） */
  status: string;
  checkedAt: string;
  latencyMs: null | number;
  error: string;
  lastItemAt: string;
}

export interface RsshubSource {
  id: number;
  name: string;
  /** 原始 URL（可能是 rsshub://<route>） */
  url: string;
  /** 按 INTEL_RSSHUB_BASE_URL 解析后的真实 feed URL；未配置时为空串 */
  resolvedUrl: string;
  resolveError: string;
  credibility: string;
  enabled: boolean;
  /** 该源下已入库文档数（>0 时不允许删除） */
  docCount: number;
  createdAt: string;
  health: RsshubHealth;
}

export interface RsshubStatus {
  baseUrl: string;
  configured: boolean;
  enabled: boolean;
  total: number;
  enabledCount: number;
  hint: string;
}

export interface RsshubSourceList {
  count: number;
  enabledCount: number;
  sources: RsshubSource[];
}

/** POST /intel/rsshub/run 的返回（dc run_rsshub_ingest 汇总） */
export interface RsshubRunResult {
  sources: number;
  fetched: number;
  new: number;
  dup: number;
  reposts: number;
  failed_sources?: number;
  details?: { error?: string; fetched?: number; new?: number; source?: string; }[];
}

/** POST /intel/rsshub/test：试探 URL，不入库 */
export interface RsshubTestResult {
  ok: boolean;
  resolvedUrl: string;
  count: number;
  titles: string[];
  error: string;
  latencyMs: number;
}
