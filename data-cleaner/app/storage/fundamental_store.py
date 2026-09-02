"""截面基础数据 / 交易状态 / 财报的存取层（迁移 006）

与 raw_store 一致：使用同步 SQLAlchemy（psycopg2）连接 PG factor schema，
供离线计算（factor_build）与刷新任务（fundamental_refresh）使用。

设计要点：
- 这些表由刷新任务在线拉取（tushare），计算时离线读表，不在每次调仓调用外部 API
- 财报按 ann_date 对齐：load_finance_reports 返回 (symbol, ann_date, ...)，
  由调用方按 "ann_date <= 交易日" 做 as-of 前向填充，避免前视
"""
import pandas as pd
from sqlalchemy import create_engine

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_ENGINE = None
_ENGINE_TRIED = False


def _engine():
    """惰性构建同步 PG 连接（复用 raw_store 的 driver 降级逻辑）。"""
    global _ENGINE, _ENGINE_TRIED
    if _ENGINE_TRIED:
        return _ENGINE
    _ENGINE_TRIED = True
    url = getattr(settings, "DATABASE_URL", None)
    if not url:
        return None
    try:
        if "+asyncpg" in url:
            url = url.replace("+asyncpg", "+psycopg2")
        elif not url.startswith("postgresql+psycopg2") and url.startswith("postgresql"):
            url = url.replace("postgresql", "postgresql+psycopg2", 1)
        _ENGINE = create_engine(url, pool_pre_ping=True, future=True)
        return _ENGINE
    except Exception as e:  # noqa: BLE001
        logger.warning(f"fundamental_store PG 连接失败: {e}")
        _ENGINE = None
        return None


def _to_float(v):
    try:
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return float(v)
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# 写入（upsert）
# --------------------------------------------------------------------------- #
def upsert_daily_basic(rows: list[dict]) -> int:
    """写入 daily_basic。rows 项含 symbol/trade_date/pe/.../float_share。"""
    eng = _engine()
    if eng is None or not rows:
        return 0
    from sqlalchemy import text

    sql = text(
        """
        INSERT INTO factor.daily_basic
            (symbol, trade_date, pe, pe_ttm, pb, ps_ttm, dv_ttm,
             turnover_rate, turnover_rate_f, total_mv, circ_mv, float_share, updated_at)
        VALUES (:symbol, :trade_date, :pe, :pe_ttm, :pb, :ps_ttm, :dv_ttm,
                :turnover_rate, :turnover_rate_f, :total_mv, :circ_mv, :float_share, now())
        ON CONFLICT (symbol, trade_date) DO UPDATE SET
            pe = EXCLUDED.pe, pe_ttm = EXCLUDED.pe_ttm, pb = EXCLUDED.pb,
            ps_ttm = EXCLUDED.ps_ttm, dv_ttm = EXCLUDED.dv_ttm,
            turnover_rate = EXCLUDED.turnover_rate,
            turnover_rate_f = EXCLUDED.turnover_rate_f,
            total_mv = EXCLUDED.total_mv, circ_mv = EXCLUDED.circ_mv,
            float_share = EXCLUDED.float_share, updated_at = now()
        """
    )
    n = 0
    with eng.begin() as conn:
        for r in rows:
            conn.execute(sql, r)
            n += 1
    return n


def upsert_trading_status(rows: list[dict]) -> int:
    """写入 trading_status。rows 项含 symbol/trade_date/limit_up/limit_down/pct_chg/suspended。"""
    eng = _engine()
    if eng is None or not rows:
        return 0
    from sqlalchemy import text

    sql = text(
        """
        INSERT INTO factor.trading_status
            (symbol, trade_date, limit_up, limit_down, pct_chg, suspended, updated_at)
        VALUES (:symbol, :trade_date, :limit_up, :limit_down, :pct_chg, :suspended, now())
        ON CONFLICT (symbol, trade_date) DO UPDATE SET
            limit_up = EXCLUDED.limit_up, limit_down = EXCLUDED.limit_down,
            pct_chg = EXCLUDED.pct_chg, suspended = EXCLUDED.suspended, updated_at = now()
        """
    )
    n = 0
    with eng.begin() as conn:
        for r in rows:
            conn.execute(sql, r)
            n += 1
    return n


def upsert_finance_reports(rows: list[dict]) -> int:
    """写入 finance_reports。rows 项含 symbol/report_period/ann_date/rev_growth_yoy/...。"""
    eng = _engine()
    if eng is None or not rows:
        return 0
    from sqlalchemy import text

    # 全列清单：成长路径与财务指标路径各自只带部分列，统一补齐为全列（缺省 None），
    # 避免 SQLAlchemy 报 "bind parameter required"。COALESCE 保证两类写入互不覆盖。
    _cols = [
        "symbol", "report_period", "ann_date", "rev_growth_yoy", "eps_growth_yoy",
        "revenue", "net_profit", "eps", "roe", "debt_ratio", "total_assets",
    ]
    sql = text(
        """
        INSERT INTO factor.finance_reports
            (symbol, report_period, ann_date, rev_growth_yoy, eps_growth_yoy,
             revenue, net_profit, eps, roe, debt_ratio, total_assets, updated_at)
        VALUES (:symbol, :report_period, :ann_date, :rev_growth_yoy, :eps_growth_yoy,
                :revenue, :net_profit, :eps, :roe, :debt_ratio, :total_assets, now())
        ON CONFLICT (symbol, report_period) DO UPDATE SET
            -- ann_date 优先保留已有真实披露日（成长报表路径），缺失才用财务指标代理
            ann_date = COALESCE(finance_reports.ann_date, EXCLUDED.ann_date),
            rev_growth_yoy = COALESCE(EXCLUDED.rev_growth_yoy, finance_reports.rev_growth_yoy),
            eps_growth_yoy = COALESCE(EXCLUDED.eps_growth_yoy, finance_reports.eps_growth_yoy),
            revenue = COALESCE(EXCLUDED.revenue, finance_reports.revenue),
            net_profit = COALESCE(EXCLUDED.net_profit, finance_reports.net_profit),
            eps = COALESCE(EXCLUDED.eps, finance_reports.eps),
            roe = COALESCE(EXCLUDED.roe, finance_reports.roe),
            debt_ratio = COALESCE(EXCLUDED.debt_ratio, finance_reports.debt_ratio),
            total_assets = COALESCE(EXCLUDED.total_assets, finance_reports.total_assets),
            updated_at = now()
        """
    )
    n = 0
    with eng.begin() as conn:
        for r in rows:
            conn.execute(sql, {c: r.get(c) for c in _cols})
            n += 1
    return n


# --------------------------------------------------------------------------- #
# 读取（供 factor_build 合并）
# --------------------------------------------------------------------------- #
def load_daily_basic(
    start: str | None = None,
    end: str | None = None,
    symbols: list[str] | None = None,
) -> pd.DataFrame:
    """读取区间 daily_basic，返回 (symbol, trade_date, 各字段)。"""
    eng = _engine()
    if eng is None:
        return pd.DataFrame()
    from sqlalchemy import text

    sql = (
        "SELECT symbol, trade_date, pe, pe_ttm, pb, ps_ttm, dv_ttm, "
        "turnover_rate, turnover_rate_f, total_mv, circ_mv, float_share "
        "FROM factor.daily_basic WHERE 1=1"
    )
    params: dict = {}
    if start:
        sql += " AND trade_date >= :st"
        params["st"] = start
    if end:
        sql += " AND trade_date <= :en"
        params["en"] = end
    if symbols:
        sql += " AND symbol = ANY(:syms)"
        params["syms"] = list(symbols)
    try:
        with eng.connect() as conn:
            return pd.read_sql(text(sql), conn, params=params)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"load_daily_basic 失败: {e}")
        return pd.DataFrame()


def load_finance_reports() -> pd.DataFrame:
    """读取全部财报（symbol, report_period, ann_date, rev_growth_yoy, eps_growth_yoy）。"""
    eng = _engine()
    if eng is None:
        return pd.DataFrame()
    from sqlalchemy import text

    sql = (
        "SELECT symbol, report_period, ann_date, rev_growth_yoy, eps_growth_yoy, "
        "revenue, net_profit, eps, roe, debt_ratio, total_assets "
        "FROM factor.finance_reports"
    )
    try:
        with eng.connect() as conn:
            return pd.read_sql(text(sql), conn)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"load_finance_reports 失败: {e}")
        return pd.DataFrame()


def load_trading_status(
    start: str | None = None,
    end: str | None = None,
    symbols: list[str] | None = None,
) -> pd.DataFrame:
    """读取区间 trading_status（涨跌停/涨跌幅/停牌）。"""
    eng = _engine()
    if eng is None:
        return pd.DataFrame()
    from sqlalchemy import text

    sql = (
        "SELECT symbol, trade_date, limit_up, limit_down, pct_chg, suspended "
        "FROM factor.trading_status WHERE 1=1"
    )
    params: dict = {}
    if start:
        sql += " AND trade_date >= :st"
        params["st"] = start
    if end:
        sql += " AND trade_date <= :en"
        params["en"] = end
    if symbols:
        sql += " AND symbol = ANY(:syms)"
        params["syms"] = list(symbols)
    try:
        with eng.connect() as conn:
            return pd.read_sql(text(sql), conn, params=params)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"load_trading_status 失败: {e}")
        return pd.DataFrame()


# --------------------------------------------------------------------------- #
# 股票元数据（行业 / 上市日期 / 近12月每股分红）
# --------------------------------------------------------------------------- #
def upsert_stock_info(rows: list[dict]) -> int:
    """写入 stock_info（行业/上市日/股息）。rows 项含 symbol/name/industry/
    list_date/dividend_ttm。COALESCE 保证分项刷新互不覆盖。"""
    eng = _engine()
    if eng is None or not rows:
        return 0
    from sqlalchemy import text

    _cols = ["symbol", "name", "industry", "list_date", "dividend_ttm"]
    sql = text(
        """
        INSERT INTO factor.stock_info
            (symbol, name, industry, list_date, dividend_ttm, updated_at)
        VALUES (:symbol, :name, :industry, :list_date, :dividend_ttm, now())
        ON CONFLICT (symbol) DO UPDATE SET
            name = EXCLUDED.name,
            industry = COALESCE(EXCLUDED.industry, stock_info.industry),
            list_date = COALESCE(EXCLUDED.list_date, stock_info.list_date),
            dividend_ttm = COALESCE(EXCLUDED.dividend_ttm, stock_info.dividend_ttm),
            updated_at = now()
        """
    )
    n = 0
    with eng.begin() as conn:
        for r in rows:
            conn.execute(sql, {c: r.get(c) for c in _cols})
            n += 1
    return n


def load_stock_info() -> pd.DataFrame:
    """读取全部 stock_info（symbol, name, industry, list_date, dividend_ttm）。"""
    eng = _engine()
    if eng is None:
        return pd.DataFrame()
    from sqlalchemy import text

    sql = (
        "SELECT symbol, name, industry, list_date, dividend_ttm "
        "FROM factor.stock_info"
    )
    try:
        with eng.connect() as conn:
            return pd.read_sql(text(sql), conn)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"load_stock_info 失败: {e}")
        return pd.DataFrame()
