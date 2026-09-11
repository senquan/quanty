-- 014: intel 因子层（P3）—— factor_values + INTL_* 因子注册
-- 设计见 docs/memo/2026-09-07.intel-module-plan.md §5（P3 — 因子化 + 回测接入）
--
-- 与 P0-2 约定一致：因子表推迟到 P3 才建，不提前建空表。
--
-- 防前视核心（P3-1）：available_at = max(published_at, ingested_at)。
--   回测在 T 日只能看见 available_at <= T 的因子值。
-- 版本化不覆盖（P3-2 验收）：唯一约束含 prompt_version / factor_version，
--   LLM 重跑产生新版本时插入新行，旧版本行的 available_at 与可见性保持不变。

CREATE TABLE IF NOT EXISTS intel.factor_values (
    id                  BIGSERIAL PRIMARY KEY,
    symbol              TEXT NOT NULL,                    -- A 股代码（600519.SH 格式）
    trade_date          DATE NOT NULL,                    -- 因子所属交易日
    factor_code         TEXT NOT NULL,                    -- INTL_MENTION_HEAT_5 / INTL_SENTIMENT_10 / ...
    value               DOUBLE PRECISION,                 -- 因子值（NULL 表示该日无值）
    -- ---- 防前视（P3-1）----
    -- 因子对回测可见的最早时刻 = max(文档发布时间, 文档入库时间)
    available_at        TIMESTAMPTZ NOT NULL,
    published_at        TIMESTAMPTZ,                      -- 来源文档发布时间（审计用）
    ingested_at         TIMESTAMPTZ,                      -- 来源文档入库时间（审计用）
    -- ---- 版本化（P3-2）----
    factor_version      TEXT NOT NULL DEFAULT 'v1',       -- 因子计算逻辑版本
    prompt_version      TEXT NOT NULL DEFAULT 'v2',       -- 理解层 prompt 版本（LLM 重跑会变）
    computed_at         TIMESTAMPTZ DEFAULT now()
);

-- 版本化不覆盖：同 symbol/date/factor 下，不同 (factor_version, prompt_version) 各存一行，
-- 保证 LLM 重跑（新 prompt_version）不会改写旧版本行的 available_at 与可见性。
CREATE UNIQUE INDEX IF NOT EXISTS idx_intel_fv_unique
    ON intel.factor_values(symbol, trade_date, factor_code, factor_version, prompt_version);

-- 回测/查询常用路径
CREATE INDEX IF NOT EXISTS idx_intel_fv_code_date
    ON intel.factor_values(factor_code, trade_date);
CREATE INDEX IF NOT EXISTS idx_intel_fv_symbol_date
    ON intel.factor_values(symbol, trade_date);
-- 防前视过滤：按可用时间裁剪
CREATE INDEX IF NOT EXISTS idx_intel_fv_available
    ON intel.factor_values(available_at);

COMMENT ON TABLE intel.factor_values IS
    'intel 因子值（P3）。available_at = max(published_at, ingested_at)，回测只可看见 available_at <= T 的行；版本化不覆盖。';
COMMENT ON COLUMN intel.factor_values.available_at IS
    '因子对回测可见的最早时刻 = max(published_at, ingested_at)，防前视核心。';

-- ---- INTL_* 因子注册（P3-3 按序实现：从简到繁）----
-- 现有 factor.definitions 因子靠 formula 现算；INTL_* 是 LLM 产出无法现算，故预存本表。
INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, status, description)
VALUES
    ('INTL_MENTION_HEAT_5',   '资讯提及热度(5日)',   'intel', 'Daily', '', '["intel.doc_mentions"]', 'intel', 'active',
     '近 5 日该标的被资讯提及的次数（P3-3 首个因子）'),
    ('INTL_SENTIMENT_10',     '资讯情绪(10日)',     'intel', 'Daily', '', '["intel.doc_mentions"]', 'intel', 'active',
     '近 10 日该标的提及的加权情绪倾向（bullish 正 / bearish 负 / neutral 0）'),
    ('INTL_RESONANCE_5',      '资讯共振(5日)',      'intel', 'Daily', '', '["intel.doc_mentions"]', 'intel', 'active',
     '近 5 日多来源共同提及同一标的的共振强度'),
    ('INTL_FIRST_MENTION',    '首次提及',           'intel', 'Daily', '', '["intel.doc_mentions"]', 'intel', 'active',
     '该标的近期首次被提及（新信息冲击）'),
    ('INTL_AUTHOR_CONVICTION','作者信念度',         'intel', 'Daily', '', '["intel.doc_mentions"]', 'intel', 'active',
     '提及该标的作者的历史准确度加权信念度（依赖 P2 accuracy）'),
    ('INTL_STYLE_MATCH',      '风格匹配度',         'intel', 'Daily', '', '["intel.author_profiles"]', 'intel', 'active',
     '标的走势特征与作者风格的匹配度（依赖 P2 画像）')
ON CONFLICT (code) DO NOTHING;
