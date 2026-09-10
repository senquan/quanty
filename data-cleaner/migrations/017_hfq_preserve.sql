-- D-2 修复：增量源不提供复权因子时，不得用 NULL 覆盖已回填的 hfq_close / adj_factor
--
-- 背景：每日增量入库走 pandadata（无复权因子接口，只写 qfq 的 close），调用
-- factor.upsert_raw_bars 时 p_hfq_close 恒为 NULL。而 007 版存储过程写的是
--     hfq_close  = EXCLUDED.hfq_close
-- 于是每次重拉同一交易日，都会把已回填的 hfq_close 冲回 NULL。
-- 实测 2026-09-10 —— 断供 3 天共 14098 行；手动回填归零后几分钟内又被冲掉 160 行。
--
-- 改动：adj_factor 与 hfq_close 改用 COALESCE(EXCLUDED.x, factor.raw_bars.x)。
--   新值非 NULL 时照常更新（akshare 等能提供复权序列的源不受影响）；
--   新值为 NULL 时保留库内已有值，不再被冲掉。
-- 回填本身仍由 app/tasks/hfq_refresh.py 负责（定时任务 18:10 + CLI），本迁移只保证
-- "已回填的值不会被后续增量写入冲掉"。

CREATE OR REPLACE FUNCTION factor.upsert_raw_bars(
    p_symbol    VARCHAR(20),
    p_timestamp TIMESTAMPTZ,
    p_open      DOUBLE PRECISION,
    p_high      DOUBLE PRECISION,
    p_low       DOUBLE PRECISION,
    p_close     DOUBLE PRECISION,
    p_volume    DOUBLE PRECISION,
    p_source    VARCHAR(20),
    p_freq      VARCHAR(10),
    p_adj_factor DOUBLE PRECISION DEFAULT NULL,
    p_hfq_close  DOUBLE PRECISION DEFAULT NULL
) RETURNS VOID AS $$
BEGIN
    INSERT INTO factor.raw_bars
        (symbol, timestamp, open, high, low, close, volume, source, freq,
         adj_factor, hfq_close)
    VALUES (p_symbol, p_timestamp, p_open, p_high, p_low, p_close, p_volume,
            p_source, p_freq, p_adj_factor, p_hfq_close)
    ON CONFLICT (symbol, timestamp, freq) DO UPDATE SET
        open      = EXCLUDED.open,
        high      = EXCLUDED.high,
        low       = EXCLUDED.low,
        close     = EXCLUDED.close,
        volume    = EXCLUDED.volume,
        source    = EXCLUDED.source,
        adj_factor = COALESCE(EXCLUDED.adj_factor, factor.raw_bars.adj_factor),
        hfq_close  = COALESCE(EXCLUDED.hfq_close,  factor.raw_bars.hfq_close);
END;
$$ LANGUAGE plpgsql;
