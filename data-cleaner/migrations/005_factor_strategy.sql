-- 005: 因子选股策略相关表（factor 域，由 data-cleaner 持有/读写）
-- 主后端只做 CRUD 代理与交易下单，配置与执行记录的统一来源在 data-cleaner。

CREATE TABLE IF NOT EXISTS factor.factor_strategies (
    id          BIGSERIAL PRIMARY KEY,
    name        VARCHAR(200) NOT NULL,
    description TEXT,
    config      JSONB NOT NULL,            -- 因子组合/权重/中性化/topN/调仓/交易时间/过滤
    is_active   BOOLEAN DEFAULT FALSE,
    owner       VARCHAR(64),               -- 创建者（前端传 user id）
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS factor.factor_strategy_backtests (
    id           BIGSERIAL PRIMARY KEY,
    strategy_id  BIGINT REFERENCES factor.factor_strategies(id) ON DELETE CASCADE,
    start_date   DATE,
    end_date     DATE,
    metrics      JSONB,
    nav          JSONB,
    rebalances   JSONB,
    warnings     JSONB,
    created_at   TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS factor.factor_strategy_executions (
    id             BIGSERIAL PRIMARY KEY,
    strategy_id    BIGINT REFERENCES factor.factor_strategies(id) ON DELETE CASCADE,
    rebalance_date DATE NOT NULL,
    trade_date     DATE,
    target_count   INTEGER,
    orders_placed  INTEGER,
    amount         DOUBLE PRECISION,
    status         VARCHAR(20),
    detail         JSONB,
    created_at     TIMESTAMPTZ DEFAULT now(),
    UNIQUE (strategy_id, rebalance_date)
);
