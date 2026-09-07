-- 011: 市场情报模块（intel）schema 与基础表
-- 设计见 docs/memo/intel-module-design.md §9
-- 独立 intel schema 与 factor schema 物理隔离，但共用同一 PG 实例。
-- 本迁移为纯 DDL；INTEL_ENABLED=false 时也照常执行（建空表零运行成本，
--   与 factor schema 始终存在一致，开关仅门控路由/调度/重型依赖）。

CREATE SCHEMA IF NOT EXISTS intel;

-- L0 摄取源清单（对应 Vibe rss_sources.json 的策展结构）
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
    created_at    TIMESTAMPTZ DEFAULT now()
);

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

CREATE INDEX IF NOT EXISTS idx_intel_documents_source     ON intel.documents(source_id);
CREATE INDEX IF NOT EXISTS idx_intel_documents_canonical  ON intel.documents(canonical_url);
CREATE INDEX IF NOT EXISTS idx_intel_documents_available  ON intel.documents(available_at);
CREATE INDEX IF NOT EXISTS idx_intel_feed_health_source   ON intel.feed_health(source_id);
