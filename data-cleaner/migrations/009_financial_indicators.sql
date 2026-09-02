-- 009: 财报扩展 ROE / 负债率 / 总资产（来源 akshare stock_financial_analysis_indicator）
-- 与成长指标(rev_growth_yoy/eps_growth_yoy，来源 yjbb)同表，按 (symbol, report_period) 合并，
-- upsert 时对各类列做 COALESCE，互不覆盖。

ALTER TABLE factor.finance_reports
    ADD COLUMN IF NOT EXISTS roe          DOUBLE PRECISION,  -- 净资产收益率(%)
    ADD COLUMN IF NOT EXISTS debt_ratio   DOUBLE PRECISION,  -- 资产负债率(%)
    ADD COLUMN IF NOT EXISTS total_assets DOUBLE PRECISION;  -- 总资产(元)
