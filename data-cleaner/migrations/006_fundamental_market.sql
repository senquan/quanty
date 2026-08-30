-- 006: 截面基础数据 / 交易状态 / 财报（换手、市值、成长、涨跌停、停牌的原始数据）
-- 与价量主表 raw_bars 解耦：来源（daily_basic / stk_limit / suspend_d / fina_indicator）
-- 与更新频率不同，独立成表，供因子计算与引擎过滤离线读取（不随每次调仓调用外部 API）。

-- 日频截面基础数据（估值/换手/市值），来源 tushare daily_basic
CREATE TABLE IF NOT EXISTS factor.daily_basic (
    symbol          VARCHAR(20) NOT NULL,
    trade_date      DATE        NOT NULL,
    pe              DOUBLE PRECISION,
    pe_ttm          DOUBLE PRECISION,
    pb              DOUBLE PRECISION,
    ps_ttm          DOUBLE PRECISION,
    dv_ttm          DOUBLE PRECISION,      -- 股息率(TTM)，因子层映射为 div_yield
    turnover_rate   DOUBLE PRECISION,      -- 换手率(%)
    turnover_rate_f DOUBLE PRECISION,      -- 自由流通换手率(%)
    total_mv        DOUBLE PRECISION,      -- 总市值(万元)
    circ_mv         DOUBLE PRECISION,      -- 流通市值(万元)
    float_share     DOUBLE PRECISION,      -- 流通股本(万股)
    updated_at      TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (symbol, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_daily_basic_date ON factor.daily_basic (trade_date);

-- 日频交易状态（涨跌停/涨跌幅/停牌），来源 tushare stk_limit + suspend_d
CREATE TABLE IF NOT EXISTS factor.trading_status (
    symbol     VARCHAR(20) NOT NULL,
    trade_date DATE        NOT NULL,
    limit_up   DOUBLE PRECISION,
    limit_down DOUBLE PRECISION,
    pct_chg    DOUBLE PRECISION,
    suspended  BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (symbol, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_trading_status_date ON factor.trading_status (trade_date);

-- 财务报告（按披露日 ann_date 对齐到日线，防前视；用于成长因子）
CREATE TABLE IF NOT EXISTS factor.finance_reports (
    symbol         VARCHAR(20) NOT NULL,
    report_period  DATE        NOT NULL,   -- 期末日（tushare end_date）
    ann_date       DATE        NOT NULL,   -- 披露日（实际可获知日，防前视的对齐基准）
    rev_growth_yoy DOUBLE PRECISION,       -- 营业收入同比(%)
    eps_growth_yoy DOUBLE PRECISION,       -- 归母净利润同比(%)
    revenue        DOUBLE PRECISION,
    net_profit     DOUBLE PRECISION,
    eps            DOUBLE PRECISION,
    updated_at     TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (symbol, report_period)
);
CREATE INDEX IF NOT EXISTS idx_finance_ann ON factor.finance_reports (symbol, ann_date);
