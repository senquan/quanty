"""Pandadata 数据源适配器（SDK: panda_data==0.0.12）

用途
----
为 data-cleaner 提供 A 股日线（含前/后复权）与交易日历数据，主要用于补齐
**价量历史深度**：当前 factor.raw_bars 仅约 100 个交易日（每标的最多 103 根 bar），
导致所有 >=250 日窗口的因子（如 GRO_PRICE_MOMENTUM）全部为 NaN。

鉴权
----
SDK 0.0.12 在 init_token() 成功前调用 get_* 会抛 ClientNotInitializedError，
故本适配器在进程内做一次性懒加载鉴权。凭据由 settings 注入（.env）：
    PANDADATA_USERNAME / PANDADATA_PASSWORD / PANDADATA_BASE_URL
未配置时 configured() 为 False、调用会抛 RuntimeError，**不影响其他数据源**。

返回列与 factor.raw_bars 对齐（可直接交给 raw_store.repository.upsert）：
    symbol, timestamp, open, high, low, close, volume, source, freq
另附 amount / pre_close / limit_up / limit_down / trade_status，
其中 limit_up/limit_down/trade_status 可用于填补 trading_status（涨跌停/停牌）。
"""
import pandas as pd

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# adjust -> SDK 方法名
_ADJUST_METHOD = {
    None: "get_stock_daily",
    "": "get_stock_daily",
    "pre": "get_stock_daily_pre",
    "post": "get_stock_daily_post",
}

COLS = [
    "symbol", "timestamp", "open", "high", "low", "close", "volume",
    "amount", "pre_close", "limit_up", "limit_down", "trade_status",
    "source", "freq",
]


def _col(df: pd.DataFrame, name: str):
    """取列；缺失时用 pd.NA 标量（在 DataFrame 构造中会自动广播）。"""
    return df[name] if name in df.columns else pd.NA


class PandadataSource:
    """Pandadata 客户端封装：进程内一次性 init_token，之后复用。"""

    name = "pandadata"

    def __init__(self) -> None:
        self._sdk = None
        self._initialized = False

    @staticmethod
    def configured() -> bool:
        """凭据是否已配置（未配置时不应调用，调用方需自行降级）。

        注：SDK 内置默认服务地址（init_token 的 base_url 默认值），
        PANDADATA_BASE_URL 仅在指向自建/内网服务时才需要配置。
        """
        return bool(
            getattr(settings, "PANDADATA_USERNAME", None)
            and getattr(settings, "PANDADATA_PASSWORD", None)
        )

    def _client(self):
        """懒加载 SDK 并鉴权。"""
        if self._initialized:
            return self._sdk
        if not self.configured():
            raise RuntimeError(
                "未配置 PANDADATA_USERNAME / PANDADATA_PASSWORD，pandadata 数据源不可用"
            )
        import panda_data

        kwargs: dict = {
            "username": settings.PANDADATA_USERNAME,
            "password": settings.PANDADATA_PASSWORD,
        }
        # 未显式配置服务地址时，沿用 SDK 内置默认地址
        if getattr(settings, "PANDADATA_BASE_URL", None):
            kwargs["base_url"] = settings.PANDADATA_BASE_URL
        panda_data.init_token(**kwargs)
        self._sdk = panda_data
        self._initialized = True
        logger.info("pandadata 初始化完成", extra={"source": self.name})
        return self._sdk

    @staticmethod
    def _ymd(d: str) -> str:
        """'2026-09-01' -> '20260901'（SDK 日期格式为 YYYYMMDD）。"""
        return str(d).replace("-", "").replace("/", "")

    def fetch_daily(
        self,
        symbols: list[str],
        start: str,
        end: str,
        adjust: str | None = None,
    ) -> pd.DataFrame:
        """拉取 A 股日线（区间不得超过 5 年，SDK 限制）。

        :param symbols: 标的代码列表，如 ['000001.SZ', '600000.SH']
        :param start/end: 起止日期，'YYYY-MM-DD' 或 'YYYYMMDD'
        :param adjust: None=不复权, 'pre'=前复权, 'post'=后复权
        """
        sdk = self._client()
        method_name = _ADJUST_METHOD.get(adjust, "get_stock_daily")
        fn = getattr(sdk, method_name, None)
        if fn is None:
            raise RuntimeError(f"panda_data 缺少方法: {method_name}")

        df = fn(
            symbol=list(symbols),
            start_date=self._ymd(start),
            end_date=self._ymd(end),
            fields=[],
        )
        if df is None or df.empty:
            return pd.DataFrame(columns=COLS)

        out = pd.DataFrame({
            "symbol": df["symbol"],
            "timestamp": pd.to_datetime(df["date"]),
            "open": _col(df, "open"),
            "high": _col(df, "high"),
            "low": _col(df, "low"),
            "close": _col(df, "close"),
            "volume": _col(df, "volume"),
            "amount": _col(df, "amount"),
            "pre_close": _col(df, "pre_close"),
            "limit_up": _col(df, "limit_up"),
            "limit_down": _col(df, "limit_down"),
            "trade_status": _col(df, "trade_status"),
            "source": self.name,
            "freq": "1d",
        })
        # 量纲对齐：pandadata 的 volume 单位是「股」，而 factor.raw_bars 既有约定
        # （alphafeed / akshare 的 成交量）是「手」，实测两者相差 100 倍
        # （例：000001.SZ 2026-08-27 alphafeed=975702 手，pandadata=97570170 股）。
        # 统一折算为「手」，避免同一列混用两种量纲。
        out["volume"] = pd.to_numeric(out["volume"], errors="coerce") / 100.0
        return out[COLS]

    def last_trade_date(self, exchange: str = "SH"):
        """最新交易日（连通性探针常用）。"""
        sdk = self._client()
        return sdk.get_last_trade_date(exchange=exchange)
