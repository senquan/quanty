-- P2 精度/复权：原始行情表落库复权因子，支持 qfq/hfq 两套价格
--
-- 背景：原 raw_bars.close 由接入层按"窗口内最新一日"做前复权（窗口局部基准），
-- 多次增量拉取后不同窗口的复权基准不一致，导致跨越分红/送转日的收益、动量误差。
--
-- 改动：
--   1) 新增 adj_factor（复权因子）与 hfq_close（后复权收盘价）两列。
--   2) 接入层改为按"全历史最新 adj_factor"归一化，close 即全局一致的 qfq；
--      hfq_close = close * (f_latest / f_first)，可由 close + adj_factor 反推。
--   3) upsert 存储过程增加 p_adj_factor / p_hfq_close 参数（默认 NULL，向后兼容）。

ALTER TABLE factor.raw_bars
    ADD COLUMN IF NOT EXISTS adj_factor  DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS hfq_close  DOUBLE PRECISION;

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
        adj_factor = EXCLUDED.adj_factor,
        hfq_close  = EXCLUDED.hfq_close;
END;
$$ LANGUAGE plpgsql;
