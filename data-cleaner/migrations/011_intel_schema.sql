-- 011: 市场情报模块（intel）schema 与基础表
-- 设计见 docs/memo/2026-09-07.intel-module-design.md §9
-- 独立 intel schema 与 factor schema 物理隔离，但共用同一 PG 实例。
-- 本迁移为纯 DDL；INTEL_ENABLED=false 时也照常执行（建空表零运行成本，
--   与 factor schema 始终存在一致，开关仅门控路由/调度/重型依赖）。

CREATE SCHEMA IF NOT EXISTS intel;

-- L0 摄取源清单（对应 Vibe rss_sources.json 的策展结构）
-- 注意：url 唯一性不用表内 UNIQUE（对迁移器已建表的旧环境不生效），见下方 uq_intel_sources_url 唯一索引
CREATE TABLE IF NOT EXISTS intel.sources (
    id                 BIGSERIAL PRIMARY KEY,
    source_type        TEXT NOT NULL,            -- rss / web / manual / wechat
    name               TEXT NOT NULL,
    url                TEXT,
    credibility        TEXT,                     -- high / medium / low
    poll_interval_sec  INTEGER DEFAULT 300,
    robots_ok          BOOLEAN DEFAULT TRUE,
    enabled            BOOLEAN DEFAULT FALSE,
    created_at         TIMESTAMPTZ DEFAULT now()
);

-- L1 规范化文档（原文不可变落盘 + content_hash 去重入口）
CREATE TABLE IF NOT EXISTS intel.documents (
    id            BIGSERIAL PRIMARY KEY,
    source_id     BIGINT REFERENCES intel.sources(id),
    external_id   TEXT,
    url           TEXT,
    canonical_url TEXT,                         -- 转载去重键
    title         TEXT,
    content       TEXT,
    content_hash  TEXT,                          -- 全文 simhash/sha，去重与变更检测
    raw_path      TEXT,                          -- 原文落盘路径（磁盘契约）
    published_at  TIMESTAMPTZ,
    ingested_at   TIMESTAMPTZ DEFAULT now(),
    available_at  TIMESTAMPTZ,                   -- 防前视：max(published, ingested)
    author        TEXT,
    redline       JSONB,                         -- 命中红线词列表
    simhash       BIGINT,                        -- 64 位 simhash（纯文本），转载识别
    duplicate_of_id BIGINT REFERENCES intel.documents(id),  -- 转载指向首发；NULL=首发
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- 兼容性补列：011 的骨架版（documents 无 simhash/duplicate_of_id）曾在本库应用过，
-- CREATE TABLE IF NOT EXISTS 对已存在的表是 no-op，列必须用 ALTER 幂等补齐；
-- 对全新环境（建表已含两列）这两句同样是无害的 no-op。
ALTER TABLE intel.documents ADD COLUMN IF NOT EXISTS simhash         BIGINT;
ALTER TABLE intel.documents ADD COLUMN IF NOT EXISTS duplicate_of_id BIGINT;
-- 旧环境经上面 ALTER 补出的 duplicate_of_id 不带自引用外键（新建表路径有），
-- 幂等补上。注意：迁移切分器按行切分且只保护 CREATE FUNCTION，DO 块必须单行书写。
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint c JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey) WHERE c.contype = 'f' AND c.conrelid = 'intel.documents'::regclass AND a.attname = 'duplicate_of_id') THEN ALTER TABLE intel.documents ADD CONSTRAINT fk_intel_documents_duplicate_of FOREIGN KEY (duplicate_of_id) REFERENCES intel.documents(id); END IF; END $$;

-- 数据源健康巡检（对应 Vibe datasources/health.py 思路）
CREATE TABLE IF NOT EXISTS intel.feed_health (
    id           BIGSERIAL PRIMARY KEY,
    source_id    BIGINT REFERENCES intel.sources(id),
    checked_at   TIMESTAMPTZ DEFAULT now(),
    status       TEXT,                           -- ok / partial / failed
    latency_ms   INTEGER,
    error        TEXT,
    last_item_at TIMESTAMPTZ
);

-- 标的别名表（P1 预筛灌种子：symbol/简称/俗称 → 标准 symbol，如 茅台→600519.SH）
CREATE TABLE IF NOT EXISTS intel.symbol_alias (
    id          BIGSERIAL PRIMARY KEY,
    alias       TEXT NOT NULL,
    symbol      TEXT NOT NULL,                  -- 标准代码，与 factor.stock_info.symbol 对齐
    alias_type  TEXT DEFAULT 'name',            -- code / name / nickname
    source      TEXT DEFAULT 'seed',            -- seed / manual / auto
    created_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE(alias, symbol)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_intel_sources_url    ON intel.sources(url);
CREATE INDEX IF NOT EXISTS idx_intel_documents_source     ON intel.documents(source_id);
CREATE INDEX IF NOT EXISTS idx_intel_documents_canonical  ON intel.documents(canonical_url);
CREATE INDEX IF NOT EXISTS idx_intel_documents_available  ON intel.documents(available_at);
CREATE INDEX IF NOT EXISTS idx_intel_documents_simhash    ON intel.documents(simhash);
CREATE INDEX IF NOT EXISTS idx_intel_feed_health_source   ON intel.feed_health(source_id);
CREATE INDEX IF NOT EXISTS idx_intel_symbol_alias_alias   ON intel.symbol_alias(alias);
