"""数据接入层公共模型"""
from datetime import datetime

from pydantic import BaseModel


class RawBar(BaseModel):
    """统一原始行情结构（OHLCV）

    所有数据源适配器最终都转换为该结构，进入清洗流水线。
    """

    symbol: str  # 标的代码，如 AAPL / BTCUSDT / 600519.SH
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str  # 数据来源: yfinance / ccxt / csv
    freq: str = "1d"  # 频率: 1d / 1h / 1m

    model_config = {"ser_json_datetime": "iso"}
