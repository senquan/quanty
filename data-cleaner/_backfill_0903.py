import sys

sys.path.insert(0, ".")

import pandas as pd
from sqlalchemy import text

from app.ingestion import tushare_source as tsmod
from app.storage.raw_store import repository


def main() -> None:
    src = tsmod.TushareSource()
    pro = src._client().pro_api()
    td = "20260903"
    df = pro.daily(trade_date=td, adj="qfq")
    print(f"tushare 9/3 (qfq) fetched: {df.shape}")

    out = pd.DataFrame(
        {
            "symbol": df["ts_code"],
            "timestamp": pd.to_datetime(df["trade_date"], format="%Y%m%d"),
            "open": df["open"].astype(float),
            "high": df["high"].astype(float),
            "low": df["low"].astype(float),
            "close": df["close"].astype(float),
            "volume": df["vol"].astype(float),
            "source": "tushare",
            "freq": "1d",
            "adj_factor": None,
            "hfq_close": None,
            "amount": df["amount"].astype(float),
        }
    )
    written = repository.bulk_upsert(out)
    print(f"bulk_upsert written={written} (rows={len(out)})")

    with repository._engine.connect() as c:
        n = c.execute(
            text(
                "SELECT COUNT(DISTINCT symbol) FROM factor.raw_bars "
                "WHERE timestamp::date='2026-09-03'"
            )
        ).scalar()
        prev = c.execute(
            text(
                "SELECT COUNT(DISTINCT symbol) FROM factor.raw_bars "
                "WHERE timestamp::date='2026-09-02'"
            )
        ).scalar()
    print(f"9/3 symbols now={n}; 9/2 symbols={prev}")


if __name__ == "__main__":
    main()
