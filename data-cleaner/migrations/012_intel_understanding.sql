-- 012: intel 理解层（P1）—— doc_mentions / doc_style / llm_runs / quarantine
-- 设计见 docs/memo/intel-module-design.md §9；列定义 §9 数据模型表。
-- 全部 IF NOT EXISTS 纯 DDL，幂等；不使用表内 UNIQUE（对已应用过的环境不生效，
--   唯一性一律走 CREATE UNIQUE INDEX / 唯一索引承载）。
-- 时间戳纪律：ingested 类时间戳一律 DB now() 同语句产生，不混 Python 时钟。

CREATE TABLE IF NOT EXISTS intel.doc_mentions (
    id            BIGSERIAL PRIMARY KEY,
    doc_id        BIGINT NOT NULL REFERENCES intel.documents(id),
    symbol        TEXT NOT NULL,                   -- 标准代码 600519.SH
    stance        TEXT NOT NULL,                   -- bullish / neutral / bearish
    confidence    DOUBLE PRECISION,                -- 作者表达确定程度 0-1
    horizon       TEXT,                            -- short / mid / long / event
    thesis        TEXT,                            -- 一句话论点（LLM 推断）
    evidence      TEXT,                            -- 原文摘录（事实）
    span_start    INTEGER,
    span_end      INTEGER,
    method        TEXT NOT NULL DEFAULT 'llm',     -- rule / llm
    prompt_version TEXT,                           -- 抽取时 prompt 版本（版本化不覆盖）
    llm_version   TEXT,                            -- provider 与 model（如 deepseek-chat）
    -- ⚠️ 注释内勿写 "冒号+紧跟字母/数字"（如 provider:model），
    --    经 SQLAlchemy text() 执行时会被当成 bind parameter 报错。
    computed_at   TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS intel.doc_style (
    id            BIGSERIAL PRIMARY KEY,
    doc_id        BIGINT NOT NULL REFERENCES intel.documents(id),
    style_dims    JSONB,                           -- value/growth/momentum/contrarian/event/quality 0-1
    prompt_version TEXT,
    llm_version   TEXT,
    computed_at   TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS intel.llm_runs (
    id             BIGSERIAL PRIMARY KEY,
    doc_id         BIGINT,                         -- 可空：预算探测等非文档调用
    provider       TEXT NOT NULL,                  -- deepseek / openai / ollama ...
    model          TEXT NOT NULL,
    prompt_version TEXT,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    cost_cny       DOUBLE PRECISION,               -- 按价目折算的人民币成本
    latency_ms     INTEGER,
    status         TEXT NOT NULL,                  -- ok / schema_fail / api_fail / budget_skip
    error          TEXT,
    created_at     TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS intel.quarantine (
    id          BIGSERIAL PRIMARY KEY,
    doc_id      BIGINT,                            -- 关联文档（可空）
    stage       TEXT NOT NULL,                     -- schema / span / json
    reason      TEXT NOT NULL,
    payload     JSONB,                             -- 被拒原始输出（供 P1-Gate 复盘）
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_intel_mentions_doc     ON intel.doc_mentions(doc_id);
CREATE INDEX IF NOT EXISTS idx_intel_mentions_symbol  ON intel.doc_mentions(symbol);
CREATE INDEX IF NOT EXISTS idx_intel_mentions_ver     ON intel.doc_mentions(prompt_version);
CREATE INDEX IF NOT EXISTS idx_intel_style_doc        ON intel.doc_style(doc_id);
CREATE INDEX IF NOT EXISTS idx_intel_llm_runs_created ON intel.llm_runs(created_at);
CREATE INDEX IF NOT EXISTS idx_intel_quarantine_doc   ON intel.quarantine(doc_id);
