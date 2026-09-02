-- 008: raw_bars 增加成交额(amount)，供流动性因子 LIQ_AMOUNT_20 与硬过滤
-- amount 原本由 pandadata 适配器拉取但落库时被丢弃；本迁移补列，
-- 回填经 raw_store.update_amounts（仅更新 amount，不动 OHLCV，避免改动既有 volume 量纲）。

ALTER TABLE factor.raw_bars
    ADD COLUMN IF NOT EXISTS amount DOUBLE PRECISION;  -- 成交额(元)
