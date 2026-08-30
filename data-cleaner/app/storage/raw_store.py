"""原始行情历史库（增量保存）

设计：
- 主存储 PostgreSQL factor.raw_bars（backend 直读，见 migrations/002_raw_bars.sql）
- 本地降级/加速 parquet data/raw/{symbol}.parquet（PG 不可用时仍可落盘与读取）

对外提供统一 RawBarRepository：upsert（增量合并）、get_latest_date、load（区间读取）。
"""
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_RAW_DIR = Path(getattr(settings, "DATA_DIR", "data")) / "raw"
_RAW_DIR.mkdir(parents=True, exist_ok=True)


class RawBarRepository:
    def __init__(self) -> None:
        self._engine = self._connect_pg()

    # ---------- 底层连接 ----------
    def _connect_pg(self):
        url = getattr(settings, "DATABASE_URL", None)
        if not url:
            return None
        try:
            from sqlalchemy import create_engine

            # 若 .env 的 DATABASE_URL 使用 async driver（asyncpg），降级为同步 driver
            # 以便本服务用同步 SQLAlchemy 写 raw_bars（不影响 backend 的 async 用法）
            if "+asyncpg" in url:
                url = url.replace("+asyncpg", "+psycopg2")
            elif not url.startswith("postgresql+psycopg2") and url.startswith(
                "postgresql"
            ):
                url = url.replace("postgresql", "postgresql+psycopg2", 1)
            engine = create_engine(url, pool_pre_ping=True, future=True)
            # 探测连通
            with engine.connect() as c:
                c.execution_options()
            return engine
        except Exception as e:  # noqa: BLE001
            logger.warning(f"raw_bars PG 连接失败，仅用 parquet: {e}")
            return None

    # ---------- parquet 助手 ----------
    def _pq_path(self, symbol: str) -> Path:
        safe = symbol.replace(".", "_")
        return _RAW_DIR / f"{safe}.parquet"

    def _pq_load(self, symbol: str) -> pd.DataFrame | None:
        p = self._pq_path(symbol)
        if not p.exists():
            return None
        try:
            df = pd.read_parquet(p)
            return df
        except Exception as e:  # noqa: BLE001
            logger.warning(f"parquet read {symbol} 失败: {e}")
            return None

    def _pq_upsert(self, df: pd.DataFrame) -> None:
        sym = df["symbol"].iloc[0]
        old = self._pq_load(sym)
        if old is not None and not old.empty:
            df = pd.concat([old, df], ignore_index=True)
        df = df.drop_duplicates(subset=["timestamp"], keep="last").sort_values(
            "timestamp"
        )
        df.to_parquet(self._pq_path(sym), index=False)

    # ---------- 公共 API ----------
    def upsert(self, df: pd.DataFrame) -> int:
        """增量写入原始行情，返回写入行数。优先 PG，降级 parquet。"""
        if df.empty:
            return 0
        df = df.copy()
        if "freq" not in df.columns:
            df["freq"] = "1d"
        written = len(df)

        # PG upsert（调用 migrations 中的 factor.upsert_raw_bars 存储过程）
        if self._engine is not None:
            try:
                from sqlalchemy import text

                with self._engine.begin() as conn:
                    for r in df.itertuples(index=False):
                        ts = (
                            r.timestamp.to_pydatetime()
                            if hasattr(r.timestamp, "to_pydatetime")
                            else r.timestamp
                        )
                        conn.execute(
                            text(
                                "SELECT factor.upsert_raw_bars("
                                ":s,:t,:o,:h,:l,:c,:v,:src,:f)"
                            ),
                            {
                                "s": r.symbol,
                                "t": ts,
                                "o": float(r.open),
                                "h": float(r.high),
                                "l": float(r.low),
                                "c": float(r.close),
                                "v": float(getattr(r, "volume", 0) or 0),
                                "src": r.source,
                                "f": r.freq,
                            },
                        )
            except Exception as e:  # noqa: BLE001
                logger.error(f"PG upsert 失败，回退 parquet: {e}")
                self._pq_upsert(df)
        else:
            self._pq_upsert(df)

        return written

    def get_latest_date(self, symbol: str, freq: str = "1d") -> str | None:
        if self._engine is not None:
            try:
                from sqlalchemy import text

                with self._engine.connect() as conn:
                    row = conn.execute(
                        text(
                            "SELECT MAX(timestamp) FROM factor.raw_bars "
                            "WHERE symbol=:s AND freq=:f"
                        ),
                        {"s": symbol, "f": freq},
                    ).fetchone()
                if row and row[0]:
                    return pd.to_datetime(row[0]).strftime("%Y-%m-%d")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"PG latest 失败，试 parquet: {e}")
        # parquet 降级
        df = self._pq_load(symbol)
        if df is not None and not df.empty:
            return pd.to_datetime(df["timestamp"]).max().strftime("%Y-%m-%d")
        return None

    def load_all(
        self,
        start: str | None = None,
        end: str | None = None,
        symbols: list[str] | None = None,
        freq: str = "1d",
    ) -> pd.DataFrame:
        """一次性读取全市场区间行情（PG 优先），供因子库批量构建使用。

        单条 SQL 拉取，避免逐标的查询（5551 只时逐条查询过慢）。
        """
        if self._engine is not None:
            try:
                from sqlalchemy import bindparam, text

                sql = (
                    "SELECT symbol,timestamp,open,high,low,close,volume,source,freq "
                    "FROM factor.raw_bars WHERE freq=:f"
                )
                params: dict = {"f": freq}
                if start:
                    sql += " AND timestamp >= :st"
                    params["st"] = start
                if end:
                    sql += " AND timestamp <= :en"
                    params["en"] = end
                if symbols:
                    sql += " AND symbol = ANY(:syms)"
                    params["syms"] = list(symbols)
                sql += " ORDER BY symbol, timestamp"
                with self._engine.connect() as conn:
                    df = pd.read_sql(
                        text(sql), conn,
                        params=params,
                        parse_dates=["timestamp"],
                    )
                # PG 的 timestamptz 返回带时区的 datetime64[ns, UTC]，
                # 而清洗流水线/schema 要求朴素 datetime64[ns]（北京时间）
                if not df.empty and str(df["timestamp"].dtype).endswith(", UTC]"):
                    df["timestamp"] = (
                        df["timestamp"].dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
                    )
                return df
            except Exception as e:  # noqa: BLE001
                logger.warning(f"PG load_all 失败: {e}")
        return pd.DataFrame()

    def latest_day_coverage(
        self, freq: str = "1d", days: int = 2
    ) -> list[tuple[str, int]]:
        """最近 N 个交易日的标的覆盖数，PG 优先。返回 [(date, count), ...] 由新到旧。"""
        if self._engine is not None:
            try:
                from sqlalchemy import text

                sql = (
                    "SELECT timestamp::date AS d, COUNT(DISTINCT symbol) AS c "
                    "FROM factor.raw_bars WHERE freq=:f "
                    "GROUP BY 1 ORDER BY 1 DESC LIMIT :n"
                )
                with self._engine.connect() as conn:
                    rows = conn.execute(text(sql), {"f": freq, "n": days}).fetchall()
                return [(str(r[0]), int(r[1])) for r in rows]
            except Exception as e:  # noqa: BLE001
                logger.warning(f"PG 覆盖度查询失败: {e}")
        return []

    def load(
        self, symbol: str, start: str | None = None, end: str | None = None
    ) -> pd.DataFrame:
        """读取某标的区间历史行情。优先 PG。"""
        if self._engine is not None:
            try:
                from sqlalchemy import text

                sql = (
                    "SELECT symbol,timestamp,open,high,low,close,volume,source,freq "
                    "FROM factor.raw_bars WHERE symbol=:s"
                )
                params: dict = {"s": symbol}
                if start:
                    sql += " AND timestamp >= :st"
                    params["st"] = start
                if end:
                    sql += " AND timestamp <= :en"
                    params["en"] = end
                sql += " ORDER BY timestamp"
                with self._engine.connect() as conn:
                    df = pd.read_sql(text(sql), conn, params=params)
                if df is not None and not df.empty:
                    df["timestamp"] = pd.to_datetime(df["timestamp"])
                    # 与 load_all 一致：timestamptz -> 朴素北京时间
                    if str(df["timestamp"].dtype).endswith(", UTC]"):
                        df["timestamp"] = (
                            df["timestamp"]
                            .dt.tz_convert("Asia/Shanghai")
                            .dt.tz_localize(None)
                        )
                    return df
            except Exception as e:  # noqa: BLE001
                logger.warning(f"PG load 失败，试 parquet: {e}")
        df = self._pq_load(symbol)
        if df is None or df.empty:
            return pd.DataFrame()
        if start:
            df = df[pd.to_datetime(df["timestamp"]) >= pd.to_datetime(start)]
        if end:
            df = df[pd.to_datetime(df["timestamp"]) <= pd.to_datetime(end)]
        return df.sort_values("timestamp")


# 模块级单例（与 ParquetStore 风格一致）
repository = RawBarRepository()
