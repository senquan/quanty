-- factor schema 初始化（Phase 1 步骤5）
-- 独立 schema 隔离主业务，主后端只读不写

CREATE SCHEMA IF NOT EXISTS factor;

-- 因子元数据表
CREATE TABLE IF NOT EXISTS factor.definitions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code         VARCHAR(50) UNIQUE NOT NULL,
    name         VARCHAR(200) NOT NULL,
    category     VARCHAR(20) NOT NULL,
    frequency    VARCHAR(20) NOT NULL,
    formula      TEXT,
    data_sources JSONB,
    author       VARCHAR(20) DEFAULT 'system',
    status       VARCHAR(20) DEFAULT 'active',
    created_at   TIMESTAMPTZ DEFAULT now()
);

-- 因子效能指标表（每日刷新）
CREATE TABLE IF NOT EXISTS factor.metrics (
    factor_code  VARCHAR(50) REFERENCES factor.definitions(code),
    as_of_date   DATE NOT NULL,
    ic_mean      DOUBLE PRECISION,
    ic_std       DOUBLE PRECISION,
    ir           DOUBLE PRECISION,
    sharpe_ratio DOUBLE PRECISION,
    max_drawdown DOUBLE PRECISION,
    win_rate     DOUBLE PRECISION,
    PRIMARY KEY (factor_code, as_of_date)
);

-- 清洗任务执行日志
CREATE TABLE IF NOT EXISTS factor.pipeline_runs (
    id           BIGSERIAL PRIMARY KEY,
    started_at   TIMESTAMPTZ DEFAULT now(),
    finished_at  TIMESTAMPTZ,
    status       VARCHAR(20),
    rows_in      INTEGER,
    rows_out     INTEGER,
    report       JSONB
);
