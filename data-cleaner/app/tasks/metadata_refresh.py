"""股票元数据刷新（迁移 010）：行业 / 上市日期 / 股息。

行业/上市日期为全市场少量调用；股息需逐标的（5000+ 次），较重，建议独立运行。

来源与可靠性（已实测 2026-09-02）：
- 上市日期：akshare `stock_info_{sh,sz,bj}_name_code` ✅ 可用（东财个股信息接口，非被墙的板块接口）。
- 行业：tushare `stock_basic(fields='ts_code,industry')` 一次返回全市场行业；但 tushare 该接口频率限制
        1 次/分，故内部重试（sleep 65s）。akshare 东财板块接口(push2)与本环境代理冲突被墙，
        申万(swsresearch)SSL 不通，cninfo 仅返回行业分类树不含个股映射 → 均不可用。
- 股息：akshare `stock_history_dividend_detail` 本环境返回 '--'（无数值）；tushare `dividend` 按标的
        限频不可全量。故默认跳过，因子 VAL_DIV_YIELD 回退 daily_basic.dv_ttm。待接入 tushare/cninfo 另补。
"""
import time
from datetime import datetime, timedelta

import pandas as pd

from app.core.logging import get_logger
from app.ingestion.universe import _from_akshare_code
from app.storage import fundamental_store

logger = get_logger(__name__)


def _fetch_listdate_map() -> dict[str, str]:
    """akshare 沪/深/京上市信息 → symbol -> 上市日期。"""
    import akshare as ak

    out: dict[str, str] = {}
    for fn_name in ("stock_info_sh_name_code", "stock_info_sz_name_code", "stock_info_bj_name_code"):
        try:
            df = getattr(ak, fn_name)()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"akshare {fn_name} 失败: {e}")
            continue
        code_col = "证券代码" if "证券代码" in df.columns else ("代码" if "代码" in df.columns else None)
        date_col = next((c for c in ("上市日期", "上市时间") if c in df.columns), None)
        if code_col is None or date_col is None:
            logger.warning(f"{fn_name} 列不匹配，跳过: {list(df.columns)}")
            continue
        for _, row in df.iterrows():
            out[_from_akshare_code(str(row[code_col]))] = row[date_col]
    return out


def _fetch_tushare_industry_map(retry: int = 20) -> dict[str, str] | None:
    """tushare stock_basic 一次返回全市场行业；限频 1 次/分，内部重试。"""
    from app.core.config import settings

    token = getattr(settings, "TUSHARE_TOKEN", None)
    if not token:
        return None
    import tushare as ts
    pro = ts.pro_api(token)
    last = None
    for attempt in range(retry):
        try:
            df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,industry")
            if df is not None and not df.empty:
                return {r["ts_code"]: r["industry"] for _, r in df.iterrows()}
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retry - 1:
                time.sleep(65)
    logger.warning(f"tushare 行业获取失败(频率限制): {last}")
    return None


def refresh_industry_listdate() -> dict:
    """刷新行业与上市日期，返回汇总。"""
    listdate_map = _fetch_listdate_map()
    industry_map = _fetch_tushare_industry_map() or {}

    rows = []
    for sym in set(industry_map) | set(listdate_map):
        rows.append({
            "symbol": sym,
            "industry": industry_map.get(sym),
            "list_date": listdate_map.get(sym),
        })
    total = fundamental_store.upsert_stock_info(rows)
    logger.info(
        "行业/上市日刷新完成",
        extra={"industry": len(industry_map), "list_date": len(listdate_map), "upserted": total},
    )
    return {
        "status": "done",
        "industry": len(industry_map),
        "list_date": len(listdate_map),
        "upserted": total,
    }


def refresh_dividend(symbols: list[str] | None = None) -> dict:
    """股息回填：本环境 akshare 分红接口返回 '--'（无数值），tushare 按标的限频不可全量，
    故默认跳过。因子 VAL_DIV_YIELD 回退 daily_basic.dv_ttm。待接入可靠源后启用。"""
    return {"status": "skipped", "reason": "akshare 分红接口本环境返回空值；待 tushare/cninfo 另补"}
