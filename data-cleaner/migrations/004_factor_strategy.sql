-- 004: 行业分类缓存表（供因子行业中性化使用）
-- 数据来自 tushare stock_basic，由行业刷新定时任务写入/更新，计算时直接读表。

CREATE TABLE IF NOT EXISTS factor.industries (
    symbol      VARCHAR(50) PRIMARY KEY,
    name        VARCHAR(200),
    industry    VARCHAR(100),
    list_status VARCHAR(10),
    list_date   DATE,
    updated_at  TIMESTAMPTZ DEFAULT now()
);
