"""数据源适配器注册表"""
from app.core.config import settings
from app.ingestion.base import BaseSource
from app.ingestion.ccxt_source import CcxtSource
from app.ingestion.csv_source import CsvSource
from app.ingestion.alphafeed_source import AlphafeedSource
from app.ingestion.fundamental_source import FundamentalSource
from app.ingestion.tushare_source import TushareSource
from app.ingestion.yfinance_source import YFinanceSource

_SOURCES: dict[str, BaseSource] = {
    "yfinance": YFinanceSource(),
    "ccxt": CcxtSource(),
    "csv": CsvSource(),
    "fundamental": FundamentalSource(token=getattr(settings, "TUSHARE_TOKEN", None)),
    "tushare": TushareSource(),
    "alphafeed": AlphafeedSource(),
}


def get_source(name: str) -> BaseSource:
    """按来源名称获取适配器实例"""
    if name not in _SOURCES:
        raise KeyError(f"未知数据源: {name}，可选: {list(_SOURCES)}")
    return _SOURCES[name]


def list_sources() -> list[str]:
    return list(_SOURCES)
