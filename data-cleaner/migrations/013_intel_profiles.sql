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

-- ⚠️ 此处原本创建 3 列唯一索引 idx_intel_profiles_key_ver
--    (profile_key, profile_type, profile_version)，**已删除，不要加回来**。
--
--    原因：apply_migrations()（app/storage/db.py）每次启动会**全量重放**
--    所有 migrations/*.sql —— 没有 applied 记录表、没有校验和。018 引入
--    as_of 版本化后，同一 (key, type, ver) 会**合法地**存在多个 as_of 版本行
--    （实测 14 个 key × 2 个 as_of）。若本文件仍创建这个 3 列唯一索引，
--    重放时就会在已有多个 as_of 的库上撞 UniqueViolationError，
--    ⇒ 整个迁移流程中断、dc 起不来（2026-09-16 实际发生）。
--
--    最终唯一键由 018_intel_profiles_asof.sql 定义：
--        (profile_key, profile_type, profile_version, as_of)
--    018 里保留的 DROP INDEX IF EXISTS 负责清理历史遗留的旧索引，
--    因此这里删掉 CREATE 不会让任何环境失去唯一性约束。

CREATE INDEX IF NOT EXISTS idx_intel_profiles_type ON intel.author_profiles(profile_type);
CREATE INDEX IF NOT EXISTS idx_intel_profiles_computed ON intel.author_profiles(computed_at);
