"""数据源适配器注册表"""
from app.core.config import settings
from app.ingestion.base import BaseSource
from app.ingestion.ccxt_source import CcxtSource
from app.ingestion.csv_source import CsvSource
from app.ingestion.akshare_source import AkshareSource
from app.ingestion.alphafeed_source import AlphafeedSource
from app.ingestion.fundamental_source import FundamentalSource
from app.ingestion.pandadata_source import PandadataSource
from app.ingestion.tushare_source import TushareSource
from app.ingestion.yfinance_source import YFinanceSource

_SOURCES: dict[str, BaseSource] = {
    "yfinance": YFinanceSource(),
    "ccxt": CcxtSource(),
    "csv": CsvSource(),
    "fundamental": FundamentalSource(token=getattr(settings, "TUSHARE_TOKEN", None)),
    "tushare": TushareSource(),
    "alphafeed": AlphafeedSource(),
    # R1c（2026-09-06）：A 股日线主力源改为 pandadata —— 实测全市场 5214 只单日约 23s，
    # 而 alphafeed 长期限频（库里仅覆盖 33 只）、tushare adj_factor 限频 1 次/分钟。
    "pandadata": PandadataSource(),
    # 北交所唯一可用源（pandadata SDK 明确不支持 .BJ），同时是复权序列备用源。
    "akshare": AkshareSource(),
}


def get_source(name: str) -> BaseSource:
    """按来源名称获取适配器实例"""
    if name not in _SOURCES:
        raise KeyError(f"未知数据源: {name}，可选: {list(_SOURCES)}")
    return _SOURCES[name]


def list_sources() -> list[str]:
    return list(_SOURCES)
