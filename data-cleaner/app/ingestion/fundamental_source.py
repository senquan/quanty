"""财务基本面数据源适配器（tushare / akshare）

价值/成长因子依赖财务报表字段（PE/PB/PS/股息率/营收同比/净利润同比）。
真实环境需配置 TUSHARE_TOKEN 或安装 akshare；无凭证/无网络时优雅降级：
返回空 DataFrame，由因子层对缺失财务列做 NaN 处理（不影响价格类因子流水线）。
"""
import pandas as pd

from app.core.logging import get_logger
from app.ingestion.base import BaseSource

logger = get_logger(__name__)


class FundamentalSource(BaseSource):
    name = "fundamental"

    def __init__(self, provider: str = "tushare", token: str | None = None):
        self.provider = provider
        self.token = token

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
