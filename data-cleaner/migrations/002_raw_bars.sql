-- 原始行情历史库（增量保存全市场日线）
-- 主存储：PostgreSQL factor.raw_bars（backend 直读）
-- 本地降级/加速：parquet data/raw/{symbol}.parquet（见 app.storage.raw_store）

CREATE TABLE IF NOT EXISTS factor.raw_bars (
    symbol    VARCHAR(20)  NOT NULL,
    timestamp TIMESTAMPTZ  NOT NULL,
    open      DOUBLE PRECISION NOT NULL,
    high      DOUBLE PRECISION NOT NULL,
    low       DOUBLE PRECISION NOT NULL,
    close     DOUBLE PRECISION NOT NULL,
    volume    DOUBLE PRECISION DEFAULT 0,
    source    VARCHAR(20)  NOT NULL,
    freq      VARCHAR(10)  DEFAULT '1d',
    PRIMARY KEY (symbol, timestamp, freq)
);

-- 按标的+时间范围查询加速
CREATE INDEX IF NOT EXISTS idx_raw_bars_symbol_time
    ON factor.raw_bars (symbol, timestamp DESC);

-- 增量 upsert（冲突则更新 OHLCV，保留首次来源）
CREATE OR REPLACE FUNCTION factor.upsert_raw_bars(
    p_symbol    VARCHAR(20),
    p_timestamp TIMESTAMPTZ,
    p_open      DOUBLE PRECISION,
    p_high      DOUBLE PRECISION,
    p_low       DOUBLE PRECISION,
    p_close     DOUBLE PRECISION,
    p_volume    DOUBLE PRECISION,
    p_source    VARCHAR(20),
    p_freq      VARCHAR(10)
) RETURNS VOID AS $$
BEGIN
    INSERT INTO factor.raw_bars
        (symbol, timestamp, open, high, low, close, volume, source, freq)
    VALUES (p_symbol, p_timestamp, p_open, p_high, p_low, p_close, p_volume, p_source, p_freq)
    ON CONFLICT (symbol, timestamp, freq) DO UPDATE SET
        open   = EXCLUDED.open,
        high   = EXCLUDED.high,
        low    = EXCLUDED.low,
        close  = EXCLUDED.close,
        volume = EXCLUDED.volume,
        source = EXCLUDED.source;
END;
$$ LANGUAGE plpgsql;
