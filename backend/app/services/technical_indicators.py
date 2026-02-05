import pandas as pd
import numpy as np
from typing import Union, Tuple

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
    def macd(data: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
        """MACD指标"""
        ema_fast = TechnicalIndicators.ema(data, fast)
        ema_slow = TechnicalIndicators.ema(data, slow)
        macd_line = ema_fast - ema_slow
        signal_line = TechnicalIndicators.ema(macd_line, signal)
        histogram = macd_line - signal_line
        
        return {
            'macd': macd_line,
            'signal': signal_line,
            'histogram': histogram
        }
    
    @staticmethod
    def bollinger_bands(data: pd.Series, window: int = 20, num_std: float = 2) -> dict:
        """布林带"""
        sma = TechnicalIndicators.sma(data, window)
        std = data.rolling(window=window).std()
        upper_band = sma + (std * num_std)
        lower_band = sma - (std * num_std)
        
        return {
            'middle': sma,
            'upper': upper_band,
            'lower': lower_band
        }
    
    @staticmethod
    def stochastic(high: pd.Series, low: pd.Series, close: pd.Series, 
                   k_window: int = 14, d_window: int = 3) -> dict:
        """随机指标"""
        lowest_low = low.rolling(window=k_window).min()
        highest_high = high.rolling(window=k_window).max()
        k_percent = 100 * ((close - lowest_low) / (highest_high - lowest_low))
        d_percent = k_percent.rolling(window=d_window).mean()
        
        return {
            'k': k_percent,
            'd': d_percent
        }
    
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
        """添加技术指标到数据框"""
        enriched_df = df.copy()
        
        # 基础技术指标
        enriched_df['sma_20'] = TechnicalIndicators.sma(df['close'], 20)
        enriched_df['sma_50'] = TechnicalIndicators.sma(df['close'], 50)
        enriched_df['ema_20'] = TechnicalIndicators.ema(df['close'], 20)
        enriched_df['rsi'] = TechnicalIndicators.rsi(df['close'])
        
        # MACD
        macd_data = TechnicalIndicators.macd(df['close'])
        enriched_df['macd'] = macd_data['macd']
        enriched_df['macd_signal'] = macd_data['signal']
        enriched_df['macd_histogram'] = macd_data['histogram']
        
        # 布林带
        bb_data = TechnicalIndicators.bollinger_bands(df['close'])
        enriched_df['bb_upper'] = bb_data['upper']
        enriched_df['bb_middle'] = bb_data['middle']
        enriched_df['bb_lower'] = bb_data['lower']
        
        # 随机指标
        stoch_data = TechnicalIndicators.stochastic(df['high'], df['low'], df['close'])
        enriched_df['stoch_k'] = stoch_data['k']
        enriched_df['stoch_d'] = stoch_data['d']
        
        # ATR
        enriched_df['atr'] = TechnicalIndicators.atr(df['high'], df['low'], df['close'])
        
        # 价格变化
        enriched_df['price_change'] = df['close'].pct_change()
        enriched_df['price_change_abs'] = abs(enriched_df['price_change'])
        
        # 成交量指标
        enriched_df['volume_sma'] = df['volume'].rolling(window=20).mean()
        enriched_df['volume_ratio'] = df['volume'] / enriched_df['volume_sma']
        
        return enriched_df
    
    @staticmethod
    def generate_trading_signals(df: pd.DataFrame) -> pd.DataFrame:
        """生成基础交易信号"""
        signals_df = df.copy()
        
        # 移动平均线信号
        signals_df['ma_signal'] = 0
        signals_df.loc[signals_df['sma_20'] > signals_df['sma_50'], 'ma_signal'] = 1  # 金叉买入
        signals_df.loc[signals_df['sma_20'] < signals_df['sma_50'], 'ma_signal'] = -1  # 死叉卖出
        
        # RSI信号
        signals_df['rsi_signal'] = 0
        signals_df.loc[signals_df['rsi'] < 30, 'rsi_signal'] = 1  # 超卖买入
        signals_df.loc[signals_df['rsi'] > 70, 'rsi_signal'] = -1  # 超买卖出
        
        # MACD信号
        signals_df['macd_signal'] = 0
        signals_df.loc[signals_df['macd'] > signals_df['macd_signal'], 'macd_signal'] = 1
        signals_df.loc[signals_df['macd'] < signals_df['macd_signal'], 'macd_signal'] = -1
        
        # 布林带信号
        signals_df['bb_signal'] = 0
        signals_df.loc[signals_df['close'] < signals_df['bb_lower'], 'bb_signal'] = 1  # 下轨支撑买入
        signals_df.loc[signals_df['close'] > signals_df['bb_upper'], 'bb_signal'] = -1  # 上轨阻力卖出
        
        # 综合信号
        signals_df['combined_signal'] = (
            signals_df['ma_signal'] + 
            signals_df['rsi_signal'] + 
            signals_df['macd_signal'] + 
            signals_df['bb_signal']
        )
        
        return signals_df