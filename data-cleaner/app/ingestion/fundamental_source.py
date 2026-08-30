"""财务基本面数据源适配器（tushare / akshare）

价值/成长因子依赖财务报表字段（PE/PB/PS/股息率/营收同比/净利润同比）。
真实环境需配置 TUSHARE_TOKEN 或安装 akshare；无凭证/无网络时优雅降级：
返回空 DataFrame，由因子层对缺失财务列做 NaN 处理（不影响价格类因子流水线）。
"""
import io
from contextlib import redirect_stderr

import pandas as pd

from app.core.exceptions import IngestionError
from app.core.logging import get_logger
from app.ingestion.base import BaseSource
from app.ingestion.universe import _from_akshare_code, get_a_share_universe

logger = get_logger(__name__)


class FundamentalSource(BaseSource):
    name = "fundamental"

    def __init__(self, provider: str = "tushare", token: str | None = None):
        self.provider = provider
        self.token = token

    # ================= 市场级抓取（供迁移 006 三张表的离线刷新） =================

    def _pro_client(self):
        """返回 tushare pro 客户端；无 token/库时抛错（由调用方降级为空）。"""
        if not self.token:
            raise IngestionError("缺少 TUSHARE_TOKEN")
        try:
            import tushare as ts
        except ImportError as e:
            raise IngestionError("未安装 tushare") from e
        return ts.pro_api(self.token)

    @staticmethod
    def _ymd(d: str) -> str:
        return d.replace("-", "").replace("/", "")

    @staticmethod
    def _to_akshare_symbol(ts_code: str) -> str:
        """tushare 代码(600519.SH) -> akshare 代码(SH600519)。"""
        code, _, ex = ts_code.partition(".")
        return f"{ex.upper()}{code}"

    def fetch_daily_basic_market(self, trade_date: str) -> "pd.DataFrame":
        """按交易日抓取全市场 daily_basic（估值/换手/市值）。

        返回列：symbol, trade_date, pe, pe_ttm, pb, ps_ttm, dv_ttm,
                turnover_rate, turnover_rate_f, total_mv, circ_mv, float_share
        失败返回空 DataFrame（列齐全），由调用方决定降级。
        """
        cols = [
            "symbol", "trade_date", "pe", "pe_ttm", "pb", "ps_ttm", "dv_ttm",
            "turnover_rate", "turnover_rate_f", "total_mv", "circ_mv", "float_share",
        ]
        try:
            pro = self._pro_client()
            df = pro.daily_basic(
                trade_date=self._ymd(trade_date),
                fields="ts_code,trade_date,pe,pe_ttm,pb,ps_ttm,dv_ttm,"
                       "turnover_rate,turnover_rate_f,total_mv,circ_mv,float_share",
            )
            if df is None or df.empty:
                return pd.DataFrame(columns=cols)
            out = pd.DataFrame(
                {
                    "symbol": df["ts_code"],
                    "trade_date": pd.to_datetime(df["trade_date"]).dt.date,
                    "pe": df["pe"], "pe_ttm": df["pe_ttm"], "pb": df["pb"],
                    "ps_ttm": df["ps_ttm"], "dv_ttm": df["dv_ttm"],
                    "turnover_rate": df["turnover_rate"],
                    "turnover_rate_f": df["turnover_rate_f"],
                    "total_mv": df["total_mv"], "circ_mv": df["circ_mv"],
                    "float_share": df["float_share"],
                }
            )
            return out
        except Exception as e:  # noqa: BLE001
            logger.warning(f"daily_basic 抓取失败({trade_date}): {e}")
            return pd.DataFrame(columns=cols)

    def fetch_trading_status_market(self, trade_date: str, provider: str | None = None) -> "pd.DataFrame":
        """按交易日抓取全市场交易状态（涨跌停/涨跌幅/停牌）。

        返回列：symbol, trade_date, limit_up, limit_down, pct_chg, suspended
        provider="auto"（默认）优先 tushare，缺失权限时兜底 akshare。
        """
        provider = provider or self.provider
        if provider in ("tushare", "auto"):
            df = self._fetch_trading_status_tushare(trade_date)
            if df is not None and not df.empty:
                return df
        if provider in ("akshare", "auto"):
            return self.fetch_trading_status_akshare(trade_date)
        cols = ["symbol", "trade_date", "limit_up", "limit_down", "pct_chg", "suspended"]
        return pd.DataFrame(columns=cols)

    def _fetch_trading_status_tushare(self, trade_date: str) -> "pd.DataFrame":
        """tushare 实现：stk_limit + daily(pct_chg) + suspend_d。各子接口失败互不影响。"""
        cols = ["symbol", "trade_date", "limit_up", "limit_down", "pct_chg", "suspended"]
        try:
            pro = self._pro_client()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"trading_status(tushare) 失败({trade_date}): {e}")
            return pd.DataFrame(columns=cols)

        merged: pd.DataFrame | None = None
        # 涨跌停价
        try:
            lim = pro.stk_limit(trade_date=self._ymd(trade_date))
            if lim is not None and not lim.empty:
                merged = pd.DataFrame(
                    {
                        "symbol": lim["ts_code"],
                        "limit_up": lim["up_limit"],
                        "limit_down": lim["down_limit"],
                    }
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"stk_limit 抓取失败({trade_date}): {e}")
        # 涨跌幅
        try:
            daily = pro.daily(trade_date=self._ymd(trade_date), fields="ts_code,pct_chg")
            if daily is not None and not daily.empty:
                chg = pd.DataFrame({"symbol": daily["ts_code"], "pct_chg": daily["pct_chg"]})
                merged = chg if merged is None else merged.merge(chg, on="symbol", how="outer")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"daily(pct_chg) 抓取失败({trade_date}): {e}")
        # 停牌
        suspended: set = set()
        try:
            sus = pro.suspend_d(trade_date=self._ymd(trade_date), fields="ts_code")
            if sus is not None and not sus.empty:
                suspended = set(sus["ts_code"].astype(str))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"suspend_d 抓取失败({trade_date}): {e}")

        if merged is None or merged.empty:
            return pd.DataFrame(columns=cols)
        merged["trade_date"] = pd.to_datetime(self._ymd(trade_date)).date()
        merged["suspended"] = merged["symbol"].astype(str).isin(suspended)
        for c in cols:
            if c not in merged.columns:
                merged[c] = None
        return merged[cols]

    def fetch_trading_status_akshare(self, trade_date: str) -> "pd.DataFrame":
        """akshare 实现：涨停池/跌停池/停复牌 三接口拼出交易状态。

        akshare 涨跌停池只含当日处于涨跌停的标的（无全市场逐标的限价表），
        故仅对涨跌停/停牌标的落记录；普通标的缺失该行，引擎按"未限/未停"处理。
        涨停价≈涨停池最新价，跌停价≈跌停池最新价。
        """
        cols = ["symbol", "trade_date", "limit_up", "limit_down", "pct_chg", "suspended"]
        try:
            import akshare as ak
        except ImportError:
            logger.warning("未安装 akshare，交易状态降级为空")
            return pd.DataFrame(columns=cols)

        d = self._ymd(trade_date)
        rows: dict[str, dict] = {}

        def _merge(code, **kw) -> None:
            sym = _from_akshare_code(str(code))
            base = rows.get(
                sym,
                {"symbol": sym, "trade_date": trade_date, "limit_up": None,
                 "limit_down": None, "pct_chg": None, "suspended": False},
            )
            base.update(kw)
            rows[sym] = base

        # 涨停股池
        try:
            buf = io.StringIO()
            with redirect_stderr(buf):
                zt = ak.stock_zt_pool_em(date=d)
            if zt is not None and not zt.empty:
                for _, r in zt.iterrows():
                    _merge(r["代码"], limit_up=float(r["最新价"]), pct_chg=float(r["涨跌幅"]))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"akshare 涨停池失败({trade_date}): {e}")
        # 跌停股池
        try:
            buf = io.StringIO()
            with redirect_stderr(buf):
                dt = ak.stock_zt_pool_dtgc_em(date=d)
            if dt is not None and not dt.empty:
                for _, r in dt.iterrows():
                    _merge(r["代码"], limit_down=float(r["最新价"]), pct_chg=float(r["涨跌幅"]))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"akshare 跌停池失败({trade_date}): {e}")
        # 停复牌（停牌标的集合）
        try:
            buf = io.StringIO()
            with redirect_stderr(buf):
                tfp = ak.stock_tfp_em(date=d)
            if tfp is not None and not tfp.empty:
                for _, r in tfp.iterrows():
                    _merge(r["代码"], suspended=True)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"akshare 停复牌失败({trade_date}): {e}")

        if not rows:
            return pd.DataFrame(columns=cols)
        return pd.DataFrame(list(rows.values()))[cols]

    def fetch_growth_by_ann_date(self, ann_date: str, provider: str | None = None) -> "pd.DataFrame":
        """按披露日抓取 fina_indicator（营收/净利同比，含 ann_date，防前视）。"""
        provider = provider or self.provider
        if provider in ("tushare", "auto"):
            df = self._fetch_growth({"ann_date": self._ymd(ann_date)})
            if df is not None and not df.empty:
                return df
        if provider in ("akshare", "auto"):
            # akshare 无按 ann_date 拉取，取全量后按 ann_date 过滤
            df = self.fetch_growth_akshare(None)
            if df is not None and not df.empty and ann_date:
                ad = self._ymd(ann_date)
                df = df[df["ann_date"].astype(str).str.replace("-", "") == ad]
            return df if df is not None else pd.DataFrame(
                columns=["symbol", "report_period", "ann_date", "rev_growth_yoy",
                         "eps_growth_yoy", "revenue", "net_profit", "eps"]
            )
        return pd.DataFrame(
            columns=["symbol", "report_period", "ann_date", "rev_growth_yoy",
                     "eps_growth_yoy", "revenue", "net_profit", "eps"]
        )

    def fetch_growth_by_period(self, period: str, provider: str | None = None) -> "pd.DataFrame":
        """按报告期（期末日，如 20240630）抓取 fina_indicator（营收/净利同比）。

        provider="auto"（默认）优先 tushare，缺失权限时兜底 akshare（逐标的遍历）。
        """
        provider = provider or self.provider
        if provider in ("tushare", "auto"):
            df = self._fetch_growth({"period": self._ymd(period)})
            if df is not None and not df.empty:
                return df
        if provider in ("akshare", "auto"):
            return self.fetch_growth_akshare([period])
        return self._empty_growth()

    def _empty_growth(self) -> "pd.DataFrame":
        return pd.DataFrame(
            columns=["symbol", "report_period", "ann_date", "rev_growth_yoy",
                     "eps_growth_yoy", "revenue", "net_profit", "eps"]
        )

    def prefer_growth_provider(self) -> str:
        """决定成长数据来源：显式 provider 优先；auto 时探测 tushare 权限。"""
        if self.provider == "akshare":
            return "akshare"
        if self.provider == "tushare":
            return "tushare"
        # auto：有 token 则探测 fina_indicator 是否有权限，否则走 akshare
        if self.token:
            try:
                pro = self._pro_client()
                probe = pro.fina_indicator(
                    ts_code="600519.SH", period="20240630", fields="ts_code,or_yoy"
                )
                if probe is not None and not probe.empty:
                    return "tushare"
            except Exception as e:  # noqa: BLE001
                logger.info(f"tushare fina_indicator 探测失败，转向 akshare: {e}")
        return "akshare"

    def fetch_growth_akshare(self, periods: list[str] | None = None) -> "pd.DataFrame":
        """akshare 实现：逐标的调利润表（东财）拼全市场成长数据。

        一次遍历全市场，每个标的取全部报告期（成本集中在网络 IO），
        再按 periods 过滤。periods=None 表示保留全部。返回列与 finance_reports 对齐。
        """
        cols = ["symbol", "report_period", "ann_date", "rev_growth_yoy",
                "eps_growth_yoy", "revenue", "net_profit", "eps"]
        try:
            import akshare as ak
        except ImportError:
            logger.warning("未安装 akshare，成长数据降级为空")
            return pd.DataFrame(columns=cols)
        period_set = set(periods or [])
        try:
            universe = get_a_share_universe()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"akshare 成长：无法获取代码池: {e}")
            return pd.DataFrame(columns=cols)

        frames: list[pd.DataFrame] = []
        ok = fail = 0
        for ts_code in universe:
            ak_code = self._to_akshare_symbol(ts_code)
            try:
                buf = io.StringIO()
                with redirect_stderr(buf):
                    df = ak.stock_profit_sheet_by_report_em(symbol=ak_code)
            except Exception as e:  # noqa: BLE001
                fail += 1
                if fail <= 5:
                    logger.debug(f"akshare 利润表失败({ts_code}): {e}")
                continue
            if df is None or df.empty:
                continue
            mapped = self._map_profit_sheet(df, ts_code, period_set)
            if not mapped.empty:
                frames.append(mapped)
                ok += 1
        logger.info(
            "akshare 成长遍历完成",
            extra={"task": "ingest", "ok": ok, "fail": fail, "periods": len(period_set)},
        )
        if not frames:
            return pd.DataFrame(columns=cols)
        return pd.concat(frames, ignore_index=True)[cols]

    @staticmethod
    def _map_profit_sheet(df: "pd.DataFrame", ts_code: str, period_set: set) -> "pd.DataFrame":
        """将东财利润表一行集合映射为 finance_reports 列（兼容列名差异）。"""
        g = df.get
        rep = g("REPORT_DATE")
        if rep is None:
            return pd.DataFrame()
        ann = g("NOTICE_DATE")
        out = pd.DataFrame(
            {
                "symbol": ts_code,
                "report_period": pd.to_datetime(rep, errors="coerce").dt.date,
                "ann_date": pd.to_datetime(ann, errors="coerce").dt.date,
                "rev_growth_yoy": g("TOTAL_OPERATE_INCOME_YOY"),
                "eps_growth_yoy": g("PARENT_NETPROFIT_YOY"),
                "revenue": g("TOTAL_OPERATE_INCOME"),
                "net_profit": g("PARENT_NETPROFIT"),
                "eps": g("BASIC_EPS"),
            }
        )
        # 披露日缺失时以报告期兜底（防前视至少不超前于报告期）
        ann_na = out["ann_date"].isna()
        if ann_na.any():
            out.loc[ann_na, "ann_date"] = out.loc[ann_na, "report_period"]
        # 过滤报告期（period_set 为 YYYYMMDD 字符串）
        if period_set:
            rp = out["report_period"].astype(str).str.replace("-", "")
            out = out[rp.isin(period_set)]
        return out

    def _fetch_growth(self, params: dict) -> "pd.DataFrame":
        """fina_indicator 统一抓取（营收/净利同比，含 ann_date，防前视）。

        返回列：symbol, report_period, ann_date, rev_growth_yoy, eps_growth_yoy,
                revenue, net_profit, eps
        """
        cols = [
            "symbol", "report_period", "ann_date", "rev_growth_yoy",
            "eps_growth_yoy", "revenue", "net_profit", "eps",
        ]
        try:
            pro = self._pro_client()
            df = pro.fina_indicator(
                **params,
                fields="ts_code,ann_date,end_date,or_yoy,netprofit_yoy,"
                       "total_revenue,n_income,basic_eps",
            )
            if df is None or df.empty:
                return pd.DataFrame(columns=cols)
            out = pd.DataFrame(
                {
                    "symbol": df["ts_code"],
                    "report_period": pd.to_datetime(df["end_date"]).dt.date,
                    "ann_date": pd.to_datetime(df["ann_date"]).dt.date,
                    "rev_growth_yoy": df["or_yoy"],
                    "eps_growth_yoy": df["netprofit_yoy"],
                    "revenue": df["total_revenue"],
                    "net_profit": df["n_income"],
                    "eps": df["basic_eps"],
                }
            )
            return out
        except Exception as e:  # noqa: BLE001
            logger.warning(f"fina_indicator 抓取失败({params}): {e}")
            return pd.DataFrame(columns=cols)

    def fetch(
        self,
        symbol: str,
        start: str,
        end: str,
        freq: str = "1d",
    ) -> "pd.DataFrame":
        """拉取标的基本面日频序列（PE/PB/PS/股息率/营收同比/净利润同比）

        返回列: symbol, timestamp, pe_ttm, pb, ps_ttm, div_yield,
                rev_growth_yoy, eps_growth_yoy
        """
        if self.provider == "tushare":
            return self._fetch_tushare(symbol, start, end)
        if self.provider == "akshare":
            return self._fetch_akshare(symbol, start, end)
        logger.warning("未知基本面 provider", extra={"provider": self.provider})
        return pd.DataFrame(
            columns=["symbol", "timestamp", "pe_ttm", "pb", "ps_ttm",
                     "div_yield", "rev_growth_yoy", "eps_growth_yoy"]
        )

    def _fetch_tushare(self, symbol, start, end) -> "pd.DataFrame":
        if not self.token:
            logger.warning("TUSHARE_TOKEN 未配置，基本面数据降级为空", extra={"symbol": symbol})
            return pd.DataFrame(
                columns=["symbol", "timestamp", "pe_ttm", "pb", "ps_ttm",
                         "div_yield", "rev_growth_yoy", "eps_growth_yoy"]
            )
        try:
            import tushare as ts
        except ImportError:
            logger.warning("未安装 tushare，基本面数据降级为空")
            return pd.DataFrame(
                columns=["symbol", "timestamp", "pe_ttm", "pb", "ps_ttm",
                         "div_yield", "rev_growth_yoy", "eps_growth_yoy"]
            )
        try:
            pro = ts.pro_api(self.token)
            df = pro.daily_basic(
                ts_code=symbol, start_date=start.replace("-", ""),
                end_date=end.replace("-", ""),
                fields="trade_date,pe_ttm,pb,ps_ttm,dp",
            )
            if df is None or df.empty:
                return pd.DataFrame(
                    columns=["symbol", "timestamp", "pe_ttm", "pb", "ps_ttm",
                             "div_yield", "rev_growth_yoy", "eps_growth_yoy"]
                )
            out = pd.DataFrame(
                {
                    "symbol": symbol,
                    "timestamp": pd.to_datetime(df["trade_date"]),
                    "pe_ttm": df["pe_ttm"],
                    "pb": df["pb"],
                    "ps_ttm": df["ps_ttm"],
                    "div_yield": df["dp"],
                    "rev_growth_yoy": pd.NA,
                    "eps_growth_yoy": pd.NA,
                }
            )
            return out
        except Exception as e:
            logger.error("tushare 拉取失败，降级为空", extra={"error": str(e)})
            return pd.DataFrame(
                columns=["symbol", "timestamp", "pe_ttm", "pb", "ps_ttm",
                         "div_yield", "rev_growth_yoy", "eps_growth_yoy"]
            )

    def _fetch_akshare(self, symbol, start, end) -> "pd.DataFrame":
        try:
            import akshare as ak
        except ImportError:
            logger.warning("未安装 akshare，基本面数据降级为空")
            return pd.DataFrame(
                columns=["symbol", "timestamp", "pe_ttm", "pb", "ps_ttm",
                         "div_yield", "rev_growth_yoy", "eps_growth_yoy"]
            )
        try:
            df = ak.stock_a_indicator_lg(symbol=symbol, start_date=start, end_date=end)
            if df is None or df.empty:
                return pd.DataFrame(
                    columns=["symbol", "timestamp", "pe_ttm", "pb", "ps_ttm",
                             "div_yield", "rev_growth_yoy", "eps_growth_yoy"]
                )
            out = pd.DataFrame(
                {
                    "symbol": symbol,
                    "timestamp": pd.to_datetime(df["trade_date"]),
                    "pe_ttm": df.get("pe_ttm"),
                    "pb": df.get("pb"),
                    "ps_ttm": df.get("ps_ttm"),
                    "div_yield": df.get("div_yield"),
                    "rev_growth_yoy": pd.NA,
                    "eps_growth_yoy": pd.NA,
                }
            )
            return out
        except Exception as e:
            logger.error("akshare 拉取失败，降级为空", extra={"error": str(e)})
            return pd.DataFrame(
                columns=["symbol", "timestamp", "pe_ttm", "pb", "ps_ttm",
                         "div_yield", "rev_growth_yoy", "eps_growth_yoy"]
            )
