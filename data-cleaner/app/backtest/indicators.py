"""技术指标 —— 注入策略执行环境前，先把常用指标算好挂在 DataFrame 上。

迁移自 ``backend/app/services/technical_indicators.py``。
纯 pandas 实现，与 dc 因子库的口径（``app/factors/``）不共享代码 ——
这里的定位很窄：**让策略代码里能直接写 ``data['sma_20']``**，
不是另一套因子计算。真要算因子请走 ``app/factors/``。
"""

from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd


class TechnicalIndicators:
    """技术指标计算类"""

    @staticmethod
    def sma(data: pd.Series, window: int) -> pd.Series:
        """简单移动平均线"""
        return data.rolling(window=window).mean()

    @staticmethod
    def ema(data: pd.Series, window: int) -> pd.Series:
        """指数移动平均线"""
        return data.ewm(span=window).mean()

    @staticmethod
    def rsi(data: pd.Series, window: int = 14) -> pd.Series:
        """相对强弱指数"""
        delta = data.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def macd(data: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, pd.Series]:
        """MACD指标"""
        ema_fast = TechnicalIndicators.ema(data, fast)
        ema_slow = TechnicalIndicators.ema(data, slow)
        macd_line = ema_fast - ema_slow
        signal_line = TechnicalIndicators.ema(macd_line, signal)
        return {
            "macd": macd_line,
            "signal": signal_line,
            "histogram": macd_line - signal_line,
        }

    @staticmethod
    def bollinger_bands(
        data: pd.Series, window: int = 20, num_std: float = 2
    ) -> Dict[str, pd.Series]:
        """布林带"""
        middle = TechnicalIndicators.sma(data, window)
        std = data.rolling(window=window).std()
        return {
            "middle": middle,
            "upper": middle + std * num_std,
            "lower": middle - std * num_std,
        }

    @staticmethod
    def stochastic(
        high: pd.Series, low: pd.Series, close: pd.Series,
        k_window: int = 14, d_window: int = 3,
    ) -> Dict[str, pd.Series]:
        """随机指标"""
        lowest_low = low.rolling(window=k_window).min()
        highest_high = high.rolling(window=k_window).max()
        k_percent = 100 * ((close - lowest_low) / (highest_high - lowest_low))
        return {"k": k_percent, "d": k_percent.rolling(window=d_window).mean()}

    @staticmethod
    def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
        """平均真实范围"""
        high_low = high - low
        high_close = np.abs(high - close.shift())
        low_close = np.abs(low - close.shift())
        true_range = np.maximum(high_low, np.maximum(high_close, low_close))
        return true_range.rolling(window=window).mean()


class DataEnricher:
    """数据增强类"""

    @staticmethod
    def add_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """添加技术指标到数据框。

        缺 high / low / volume 时对应指标跳过而不是报错 ——
        行情来源不同字段完整度不同，为了一个用不到的指标让整个回测失败不值得。
        """
        out = df.copy()
        close = pd.to_numeric(out["close"], errors="coerce")

        out["sma_20"] = TechnicalIndicators.sma(close, 20)
        out["sma_50"] = TechnicalIndicators.sma(close, 50)
        out["ema_20"] = TechnicalIndicators.ema(close, 20)
        out["rsi"] = TechnicalIndicators.rsi(close)

        macd_data = TechnicalIndicators.macd(close)
        out["macd"] = macd_data["macd"]
        out["macd_signal"] = macd_data["signal"]
        out["macd_histogram"] = macd_data["histogram"]

        bb = TechnicalIndicators.bollinger_bands(close)
        out["bb_upper"] = bb["upper"]
        out["bb_middle"] = bb["middle"]
        out["bb_lower"] = bb["lower"]

        if {"high", "low"}.issubset(out.columns):
            high = pd.to_numeric(out["high"], errors="coerce")
            low = pd.to_numeric(out["low"], errors="coerce")
            stoch = TechnicalIndicators.stochastic(high, low, close)
            out["stoch_k"] = stoch["k"]
            out["stoch_d"] = stoch["d"]
            out["atr"] = TechnicalIndicators.atr(high, low, close)

        out["price_change"] = close.pct_change()
        out["price_change_abs"] = out["price_change"].abs()

        if "volume" in out.columns:
            vol = pd.to_numeric(out["volume"], errors="coerce")
            out["volume_sma"] = vol.rolling(window=20).mean()
            out["volume_ratio"] = vol / out["volume_sma"].replace(0, np.nan)

        return out
