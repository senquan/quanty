-- 010: 股票元数据（行业 / 上市日期 / 近12月每股现金分红）
-- 供三层架构：行业上限(industry_cap)、新股过滤(list_date)、股息率因子(VAL_DIV_YIELD)。
-- 数据来源 akshare（免费、无配额）：东财行业成分 + 沪/深/京上市信息 + 现金分红。

CREATE TABLE IF NOT EXISTS factor.stock_info (
    symbol       VARCHAR(20)  NOT NULL,
    name         VARCHAR(64),
    industry     VARCHAR(64),          -- 东财一级行业
    list_date    DATE,                 -- 上市日期（新股过滤：上市 < 1 年视为次新）
    dividend_ttm DOUBLE PRECISION,     -- 近12月每股现金分红(元)；股息率 = dividend_ttm / 价
    updated_at   TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (symbol)
);
CREATE INDEX IF NOT EXISTS idx_stock_info_industry ON factor.stock_info (industry);
