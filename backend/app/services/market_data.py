"""行情取数（yahoo / ccxt）。

2026-09-06 R5:从 ``backtest_engine.py`` 拆出。

**为什么留它**:``GET /quant/market-data`` 还在用它看行情,
那是「取数展示」,不是回测 —— 回测的撮合已经全部迁去 dc 了。

⚠️ **这里没有 A 股**。A 股行情在 dc 的 ``factor.raw_bars``;
yahoo / ccxt 只覆盖港美股与加密。回测不要走这条路(§0 归口决策)。
"""
from abc import ABC, abstractmethod

import ccxt
import pandas as pd
import yfinance as yf


class DataSource(ABC):
    """数据源抽象基类"""

    @abstractmethod
    def get_historical_data(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        pass


class YahooFinanceDataSource(DataSource):
    """Yahoo Finance数据源"""

    def get_historical_data(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        try:
            ticker = yf.Ticker(symbol)
            data = ticker.history(start=start_date, end=end_date)
            data.columns = [col.lower().replace(' ', '_') for col in data.columns]
            return data
        except Exception as e:
            raise ValueError(f"Failed to fetch data from Yahoo Finance: {str(e)}")


class CCXTDataSource(DataSource):
    """加密货币数据源"""

    def __init__(self, exchange_name: str = 'binance'):
        self.exchange = getattr(ccxt, exchange_name)()

    def get_historical_data(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        try:
            # 转换时间格式
            since = self.exchange.parse8601(start_date)
            limit = 1000  # CCXT限制

            ohlcv = self.exchange.fetch_ohlcv(symbol, '1d', since, limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            return df
        except Exception as e:
            raise ValueError(f"Failed to fetch data from CCXT: {str(e)}")


class DataManager:
    """数据管理器"""

    def __init__(self):
        self.sources = {
            'yahoo': YahooFinanceDataSource(),
            'crypto': CCXTDataSource()
        }

    def get_data(self, source: str, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """获取历史数据"""
        if source not in self.sources:
            raise ValueError(f"Unsupported data source: {source}")

        return self.sources[source].get_historical_data(symbol, start_date, end_date)
