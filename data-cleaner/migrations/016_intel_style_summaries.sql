-- P4-4 作者风格总结报告（云端 LLM 综述，进画像卡片）
--
-- 与 author_profiles 分开建表的原因：LLM 产出是**独立版本化**的工件
--   - summary_version 总结器版本 / prompt_version LLM 提示词版本，改任一即新版本
--   - 重跑同版本覆盖（DO UPDATE），旧版本保留可回溯（与 factor_values / author_profiles 同款纪律）
--   - 成本/token 逐作者可审计（llm_runs 之外再留一份快照，便于画像卡片直接展示成本）
--
-- 注意（P3 踩过的坑）：SQL 注释里禁止出现「冒号紧跟字母或数字」，
-- SQLAlchemy text() 会把它当 bind parameter 解析。示例一律写成「键 = 值」。
CREATE TABLE IF NOT EXISTS intel.author_style_summaries (
    id                BIGSERIAL PRIMARY KEY,
    profile_key       TEXT NOT NULL,
    profile_type      TEXT NOT NULL DEFAULT 'author',
    summary_version   TEXT NOT NULL DEFAULT 'v1',     -- 总结器版本（改逻辑 = 新版本）
    prompt_version    TEXT NOT NULL,                  -- LLM 提示词版本（style_v1 ...）

    -- LLM 产出（进画像卡片）
    summary           TEXT,                           -- 自然语言风格综述（2-4 句）
    style_tags        JSONB NOT NULL DEFAULT '[]',    -- ["价值投资" 等风格标签]
    sectors           JSONB NOT NULL DEFAULT '[]',    -- 主要覆盖行业
    holding_period    TEXT,                           -- 典型持仓周期 short / mid / long / event
    conviction        TEXT,                           -- high / medium / low
    caveats           TEXT,                           -- 观点局限与风险提示
    confidence        DOUBLE PRECISION,               -- 0-1

    -- 输入快照（可追溯：这次总结基于哪些统计与样本）
    input_stats       JSONB,                          -- 送审时的聚合统计
    sample_size       INTEGER NOT NULL DEFAULT 0,     -- 送审的 thesis 条数

    -- 成本与审计
    model             TEXT,
    input_tokens      INTEGER,
    output_tokens     INTEGER,
    cost_cny          DOUBLE PRECISION,
    latency_ms        INTEGER,
    status            TEXT NOT NULL,                  -- ok / schema_fail / api_fail / skipped
    error             TEXT,
    computed_at       TIMESTAMPTZ DEFAULT now(),

    CONSTRAINT author_style_summaries_version_uniq
        UNIQUE (profile_key, profile_type, summary_version)
);

CREATE INDEX IF NOT EXISTS idx_style_summaries_key_time
    ON intel.author_style_summaries (profile_key, computed_at DESC);

CREATE INDEX IF NOT EXISTS idx_style_summaries_status
    ON intel.author_style_summaries (status);
