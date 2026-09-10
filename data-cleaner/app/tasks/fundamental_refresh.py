"""截面基础数据 / 交易状态 / 财报的在线刷新任务（迁移 006）

与价量 backfill 分离：这些表由本任务在线拉取（tushare），factor_build 离线读表。
- daily_basic / trading_status：按交易日（默认最新行情日）全市场刷新
- finance_reports：按报告期（默认最近 N 期）刷新，含 ann_date 供防前视对齐
"""
from datetime import date, datetime
from time import sleep

import json
from pathlib import Path

from app.core.config import settings
from app.core.logging import get_logger
from app.ingestion.fundamental_source import FundamentalSource
from app.ingestion.universe import get_a_share_universe
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
        # 优先批量业绩报表（每报告期 1 次调用，分钟级）；失败则回退逐标的利润表
        df = src.fetch_growth_akshare_bulk(periods)
        if df is None or df.empty:
            logger.warning("akshare 批量成长为空，回退逐标的利润表")
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


def refresh_financial_indicator(
    symbols: list[str] | None = None, start_year: str = "2020", batch: int = 500
) -> dict:
    """刷新财报 ROE/负债率/总资产（akshare 财务指标），ann_date 与成长报表 COALESCE 合并。

    全市场分块拉取并逐块 upsert（避免长事务、便于进度监控与断点续跑）。
    symbols 为 None 时取宇宙（优先 tushare/akshare，降级 factor.raw_bars）；
    start_year 控制回看起点。
    """
    src = _source()
    if symbols is None:
        try:
            symbols = get_a_share_universe()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"无法获取宇宙，跳过财务指标刷新: {e}")
            return {"status": "skipped", "reason": str(e)}
    total = 0
    n = len(symbols)
    for i in range(0, n, batch):
        chunk = symbols[i : i + batch]
        df = src.fetch_financial_indicator_akshare(symbols=chunk, start_year=start_year)
        total += fundamental_store.upsert_finance_reports(_rows(df))
        logger.info(
            "财务指标刷新进度",
            extra={"task": "fundamental_refresh", "done": min(i + batch, n),
                   "total": n, "upserted": total, "start_year": start_year},
        )
    logger.info(
        "财务指标刷新完成",
        extra={"task": "fundamental_refresh", "rows": total, "start_year": start_year, "universe": n},
    )
    return {"status": "done", "rows": total, "start_year": start_year, "universe": n}


def refresh_fundamental(trade_date: str | None = None) -> dict:
    """每日基础数据刷新入口（daily_basic + trading_status + 财报 + 财务指标）。"""
    t0 = datetime.now()
    daily = refresh_daily_fundamental(trade_date)
    growth = refresh_growth()
    indicator = refresh_financial_indicator()
    return {
        "status": "done",
        "daily": daily,
        "growth": growth,
        "indicator": indicator,
        "duration_s": round((datetime.now() - t0).total_seconds(), 1),
    }


# --------------------------------------------------------------------------- #
# 历史回补（一次性）：finance_reports 从指定年份起所有报告期补全
# --------------------------------------------------------------------------- #
_GROWTH_HISTORY_STATE = (
    Path(__file__).resolve().parent.parent.parent / "data" / "growth_history_state.json"
)


def all_periods_since(start_year: int) -> list[str]:
    """枚举 start_year 起所有财报期末日(0331/0630/0930/1231, YYYYMMDD)，含当年。"""
    today = date.today()
    ends = [(3, 31), (6, 30), (9, 30), (12, 31)]
    periods: list[str] = []
    for y in range(start_year, today.year + 1):
        for m, d in ends:
            p = date(y, m, d)
            if p < today:
                periods.append(f"{y}{m:02d}{d:02d}")
    return sorted(periods)


def _load_growth_state() -> dict:
    try:
        if _GROWTH_HISTORY_STATE.exists():
            return json.loads(_GROWTH_HISTORY_STATE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {"done_periods": [], "done_indicator": False}


def _save_growth_state(state: dict) -> None:
    _GROWTH_HISTORY_STATE.parent.mkdir(parents=True, exist_ok=True)
    _GROWTH_HISTORY_STATE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def backfill_growth_history(start_year: int = 2021, period_sleep: float = 1.0) -> dict:
    """用 akshare 把 finance_reports 的成长列(rev/eps 同比, revenue, net_profit, eps)
    从 start_year 起所有报告期补全。可断点续跑(state 记录已完成期)；每期 1 次
    stock_yjbb_em 调用，失败隔离不影响其它期。

    另补全 ROE/负债率/总资产（财务质量列，refresh_financial_indicator 按年遍历）。
    幂等：upsert 按 (symbol, report_period) 冲突合并，重跑安全。
    """
    src = _source()
    state = _load_growth_state()
    done: set = set(state.get("done_periods", []))
    periods = [p for p in all_periods_since(start_year) if p not in done]
    total_rows = 0

    for p in periods:
        try:
            df = src.fetch_growth_akshare_bulk([p])
            if df is None or df.empty:
                logger.warning(f"成长历史回补空({p})，跳过")
            else:
                n = fundamental_store.upsert_finance_reports(_rows(df))
                total_rows += n
                logger.info(f"成长历史回补完成({p}): {n} 行")
            done.add(p)
            state["done_periods"] = sorted(done)
            _save_growth_state(state)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"成长历史回补失败({p}): {e}（留待续跑）")
        sleep(period_sleep)

    if not state.get("done_indicator"):
        try:
            res = refresh_financial_indicator(start_year=str(start_year))
            total_rows += res.get("rows", 0)
            state["done_indicator"] = True
            _save_growth_state(state)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"财务指标历史回补失败: {e}（留待续跑）")

    logger.info(
        "finance_reports 历史回补完成",
        extra={"task": "growth_history", "rows": total_rows, "periods": len(periods)},
    )
    return {"status": "done", "rows": total_rows, "periods_backfilled": len(periods)}


# --------------------------------------------------------------------------- #
# 历史回补（一次性）：估值历史（PB/PE_TTM 日频、股息率年度）经 akshare 补全
# --------------------------------------------------------------------------- #
_VAL_HISTORY_STATE = (
    Path(__file__).resolve().parent.parent.parent / "data" / "valuation_history_state.json"
)


def _clean_num(v):
    """float NaN/inf -> None（PG double 接受 NaN，但批量 upsert 前清理更稳）。"""
    try:
        if v is None:
            return None
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    except Exception:  # noqa: BLE001
        return None


def _load_val_state() -> dict:
    try:
        if _VAL_HISTORY_STATE.exists():
            return json.loads(_VAL_HISTORY_STATE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {"done_symbols": [], "done_dividend_years": []}


def _save_val_state(state: dict) -> None:
    _VAL_HISTORY_STATE.parent.mkdir(parents=True, exist_ok=True)
    _VAL_HISTORY_STATE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def backfill_pb_pe_history(
    symbols: list[str] | None = None,
    sleep_sec: float = 0.3,
    buf_size: int = 200_000,
) -> dict:
    """用 akshare 东财个股估值(stock_value_em)把 daily_basic.pb / pe_ttm / ps_ttm
    从全历史补全（日频）。

    逐标的抓取（每标的 1 次 stock_value_em 调用，同时返回 PB/PE_TTM/PS_TTM），
    批量写入 daily_basic。可断点续跑（state 记录已完成 symbol）；单标的失败隔离，
    留待下次续跑。ps_ttm 现经东财可得（VAL_PS_TTM 不再 unavailable）。
    """
    src = FundamentalSource(provider="akshare")
    if symbols is None:
        symbols = fundamental_store.load_universe()
    if not symbols:
        return {"status": "skipped", "reason": "无 universe"}
    state = _load_val_state()
    done: set = set(state.get("done_symbols", []))
    todo = [s for s in symbols if s not in done]
    total = len(symbols)
    ok = skip = fail = 0
    rows_buf: list[dict] = []

    def _flush() -> None:
        nonlocal rows_buf
        if rows_buf:
            fundamental_store.bulk_upsert_daily_basic(rows_buf)
            rows_buf = []

    for i, sym in enumerate(todo, 1):
        try:
            df = src.fetch_valuation_value_em(sym)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"估值历史回补异常({sym}): {e}")
            fail += 1
            continue
        if df is None or df.empty:
            skip += 1
        else:
            for r in df.to_dict("records"):
                rows_buf.append({
                    "symbol": sym,
                    "trade_date": r.get("trade_date"),
                    "pe": None,
                    "pe_ttm": _clean_num(r.get("pe_ttm")),
                    "pb": _clean_num(r.get("pb")),
                    "ps_ttm": _clean_num(r.get("ps_ttm")),
                    "dv_ttm": None,
                    "turnover_rate": None,
                    "turnover_rate_f": None,
                    "total_mv": None,
                    "circ_mv": None,
                    "float_share": None,
                })
            ok += 1
        done.add(sym)
        if len(rows_buf) >= buf_size:
            _flush()
        if i % 200 == 0:
            _flush()
            state["done_symbols"] = sorted(done)
            _save_val_state(state)
            logger.info(
                "估值历史回补进度",
                extra={"task": "valuation_history", "done": i, "total": total,
                       "ok": ok, "skip": skip, "fail": fail},
            )
        if sleep_sec:
            sleep(sleep_sec)
    _flush()
    state["done_symbols"] = sorted(done)
    _save_val_state(state)
    return {"status": "done", "total": total, "ok": ok, "skip": skip, "fail": fail}


def backfill_dividend_yield_history(years: list[int] | None = None) -> dict:
    """用 akshare 东方财富分红送配把 daily_basic.dv_ttm（股息率）按年度补全。

    每年 1 次 stock_fhps_em 调用取全 A 股息率，写入该年首个交易日
    （factor_build 会按 symbol 前向填充到全年及之后）。可断点续跑。
    """
    src = FundamentalSource(provider="akshare")
    if years is None:
        years = list(range(2021, date.today().year + 1))
    trade_dates = fundamental_store.load_trade_dates()
    if not trade_dates:
        return {"status": "skipped", "reason": "无 raw_bars 交易日"}
    first_of_year: dict = {}
    for d in trade_dates:
        first_of_year.setdefault(d.year, d)
    state = _load_val_state()
    done: set = set(state.get("done_dividend_years", []))
    total_rows = 0
    for y in years:
        if y in done:
            continue
        first_day = first_of_year.get(int(y))
        if first_day is None:
            logger.warning(f"无 {y} 交易日，跳过股息率")
            done.add(y)
            continue
        try:
            df = src.fetch_dividend_yield_em(y)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"股息率回补异常({y}): {e}")
            continue
        if df is None or df.empty:
            logger.warning(f"股息率回补空({y})，跳过")
            done.add(y)
            state["done_dividend_years"] = sorted(done)
            _save_val_state(state)
            continue
        rows = [
            {
                "symbol": r["symbol"],
                "trade_date": first_day,
                "pe": None, "pe_ttm": None, "pb": None, "ps_ttm": None,
                "dv_ttm": _clean_num(r.get("dividend_yield")),
                "turnover_rate": None, "turnover_rate_f": None,
                "total_mv": None, "circ_mv": None, "float_share": None,
            }
            for r in df.to_dict("records")
        ]
        fundamental_store.bulk_upsert_daily_basic(rows)
        total_rows += len(rows)
        done.add(y)
        state["done_dividend_years"] = sorted(done)
        _save_val_state(state)
        logger.info(f"股息率回补完成({y}): {len(rows)} 行")
    return {"status": "done", "rows": total_rows, "years": sorted(done)}
