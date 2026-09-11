-- 013: intel 画像层（P2）—— author_profiles
-- 设计见 docs/memo/2026-09-07.intel-module-plan.md §4（P2 — 作者画像 + 历史准确度）
-- 幂等 DDL，IF NOT EXISTS；唯一性走索引。
--
-- ⚠️ 本文件若经 SQLAlchemy text() 执行，注释里禁止出现 "冒号+紧跟字母/数字"
--    （如 count:8），会被解析成 bind parameter 并报
--    "A value is required for bind parameter '8'"。
--    已统一用 " = " 书写示例；推荐改用 app.intel.store.run_sql_file()（原生执行）。

CREATE TABLE IF NOT EXISTS intel.author_profiles (
    id                  BIGSERIAL PRIMARY KEY,
    profile_key         TEXT NOT NULL,                   -- 作者名或 source_name（author 为空时的 fallback）
    profile_type        TEXT NOT NULL DEFAULT 'author',  -- 'author' | 'source'
    profile_version     TEXT NOT NULL DEFAULT 'v1',      -- 版本化不覆盖
    -- 聚合统计
    total_mentions      INTEGER NOT NULL DEFAULT 0,
    total_docs          INTEGER NOT NULL DEFAULT 0,
    unique_symbols      INTEGER NOT NULL DEFAULT 0,
    date_first          TIMESTAMPTZ,
    date_last           TIMESTAMPTZ,
    -- stance 分布（JSONB 计数器）
    stance_dist         JSONB NOT NULL DEFAULT '{}',     -- {"bullish": N, "neutral": N, "bearish": N}
    -- 风格向量（时间加权，半衰期 180 天）
    style_vector        JSONB NOT NULL DEFAULT '{}',     -- {"bullish_ratio": 0.05, "bearish_ratio": 0.01, ...}
    top_symbols         JSONB NOT NULL DEFAULT '[]',     -- [{"symbol" = "600519.SH", "count" = 8}, ...]
    top_sources         JSONB NOT NULL DEFAULT '[]',     -- [{"source_id" = 1, "name" = "...", "count" = 12}, ...]
    horizon_dist        JSONB NOT NULL DEFAULT '{}',     -- {"short" = N, "mid" = N, "long" = N, "event" = N}
    -- 准确度（超额收益，基于 factor.raw_bars 只读计算）
    avg_excess_20d      DOUBLE PRECISION,                -- 提及后 20 日平均超额收益（%）
    avg_excess_60d      DOUBLE PRECISION,                -- 提及后 60 日平均超额收益（%）
    accuracy_sample_size INTEGER NOT NULL DEFAULT 0,     -- 有行情数据的 mention 数
    win_rate_20d        DOUBLE PRECISION,                -- 20 日正超额占比
    win_rate_60d        DOUBLE PRECISION,                -- 60 日正超额占比
    -- 样本量标记
    sample_insufficient BOOLEAN NOT NULL DEFAULT false,   -- true = mentions < 30 或 accuracy_sample < 10
    -- 风格漂移
    drift_detected      BOOLEAN NOT NULL DEFAULT false,
    drift_detail        JSONB,                           -- 漂移详情（近 90d vs 全量对比）
    computed_at         TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_intel_profiles_key_ver
    ON intel.author_profiles(profile_key, profile_type, profile_version);

CREATE INDEX IF NOT EXISTS idx_intel_profiles_type ON intel.author_profiles(profile_type);
CREATE INDEX IF NOT EXISTS idx_intel_profiles_computed ON intel.author_profiles(computed_at);
