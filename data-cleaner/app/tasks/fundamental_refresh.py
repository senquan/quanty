"""截面基础数据 / 交易状态 / 财报的在线刷新任务（迁移 006）

与价量 backfill 分离：这些表由本任务在线拉取（tushare），factor_build 离线读表。
- daily_basic / trading_status：按交易日（默认最新行情日）全市场刷新
- finance_reports：按报告期（默认最近 N 期）刷新，含 ann_date 供防前视对齐
"""
from datetime import date, datetime

from app.core.config import settings
from app.core.logging import get_logger
from app.ingestion.fundamental_source import FundamentalSource
from app.storage import fundamental_store
from app.storage.raw_store import repository

logger = get_logger(__name__)


def _source() -> FundamentalSource:
    return FundamentalSource(
        token=getattr(settings, "TUSHARE_TOKEN", None),
        provider=getattr(settings, "FUNDAMENTAL_PROVIDER", "auto") or "auto",
    )


def _rows(df, *cols) -> list[dict]:
    """DataFrame -> dict 列表，NaN 转 None（NaN 无法写入 PG double）。"""
    if df is None or df.empty:
        return []
    out = []
    for r in df.to_dict("records"):
        out.append({k: (None if (isinstance(v, float) and pd_isna(v)) else v) for k, v in r.items()})
    return out


def pd_isna(v) -> bool:
    try:
        import pandas as pd

        return bool(pd.isna(v))
    except Exception:  # noqa: BLE001
        return False


def _latest_trade_date() -> str | None:
    """当前因子库最新行情日，作为基础数据刷新的目标日期。"""
    cov = repository.latest_day_coverage(days=1)
    return cov[0][0] if cov else None


def recent_periods(n: int = 8) -> list[str]:
    """最近 n 个财报期末日（YYYYMMDD，0331/0630/0930/1231）。"""
    today = date.today()
    ends = [(3, 31), (6, 30), (9, 30), (12, 31)]
    periods: list[str] = []
    for y in range(today.year, today.year - 3, -1):
        for m, d in ends:
            if date(y, m, d) < today:
                periods.append(f"{y}{m:02d}{d:02d}")
    periods.sort(reverse=True)
    return periods[:n]


def refresh_daily_fundamental(trade_date: str | None = None) -> dict:
    """刷新某交易日的 daily_basic + trading_status。返回汇总。"""
    src = _source()
    date_ = trade_date or _latest_trade_date()
    if not date_:
        return {"status": "skipped", "reason": "无最新行情日"}

    # daily_basic 仅 tushare（估值/市值/换手），无 token 则跳过该项
    n_db = 0
    if getattr(settings, "TUSHARE_TOKEN", None):
        db_df = src.fetch_daily_basic_market(date_)
        n_db = fundamental_store.upsert_daily_basic(_rows(db_df))
    else:
        logger.warning("未配置 TUSHARE_TOKEN，跳过 daily_basic（估值/市值因子将缺）")

    # trading_status 走 auto：tushare 缺权限时自动用 akshare（涨停池/跌停池/停复牌）
    ts_df = src.fetch_trading_status_market(date_)
    n_ts = fundamental_store.upsert_trading_status(_rows(ts_df))
    logger.info(
        "基础数据刷新完成",
        extra={
            "task": "fundamental_refresh",
            "trade_date": date_,
            "daily_basic": n_db,
            "trading_status": n_ts,
        },
    )
    return {
        "status": "done",
        "trade_date": date_,
        "daily_basic": n_db,
        "trading_status": n_ts,
    }


def refresh_growth(periods: list[str] | None = None) -> dict:
    """刷新财报（fina_indicator），含 ann_date，防前视。返回汇总。

    自动选择来源：tushare 有 fina_indicator 权限则按报告期批量拉；
    否则（或显式 akshare）逐标的遍历 akshare 利润表一次取全期。
    """
    src = _source()
    periods = periods or recent_periods(8)
    provider = src.prefer_growth_provider()
    total = 0
    if provider == "akshare":
        # akshare 逐标的遍历，一次返回全部请求期，避免按 period 重复拉取
        df = src.fetch_growth_akshare(periods)
        total = fundamental_store.upsert_finance_reports(_rows(df))
    else:
        for p in periods:
            df = src.fetch_growth_by_period(p)
            total += fundamental_store.upsert_finance_reports(_rows(df))
    logger.info(
        "财报刷新完成",
        extra={"task": "fundamental_refresh", "provider": provider, "periods": periods, "rows": total},
    )
    return {"status": "done", "provider": provider, "periods": periods, "rows": total}


def refresh_fundamental(trade_date: str | None = None) -> dict:
    """每日基础数据刷新入口（daily_basic + trading_status + 财报）。"""
    t0 = datetime.now()
    daily = refresh_daily_fundamental(trade_date)
    growth = refresh_growth()
    return {
        "status": "done",
        "daily": daily,
        "growth": growth,
        "duration_s": round((datetime.now() - t0).total_seconds(), 1),
    }
