"""成交额(amount) 回填任务

pandadata 适配器账户级日流量配额耗尽(错误 500009)，无法取额；改用 akshare
`stock_zh_a_hist`（免费，返回 `成交额`）逐标的回填。仅更新 amount 列（不动
OHLCV/volume，避免改动既有量纲），供流动性因子 LIQ_AMOUNT_20。

窗口：默认最近 ~400 个交易日（约 1.5 年，足够 20 日均额 + 近期回测）。
"""
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

import pandas as pd

from app.core.logging import get_logger
from app.ingestion.universe import get_a_share_universe
from app.storage.raw_store import repository

logger = get_logger(__name__)

_DEFAULT_WINDOW_DAYS = 400
_WORKERS = 6
_MAX_RETRY = 3


def refresh_amount(
    start: str | None = None, end: str | None = None, batch: int = 200
) -> dict:
    """回填 amount。返回汇总。start/end 形如 '20260801'（无横线）。

    来源：akshare `stock_zh_a_daily`（**新浪**后端，返回 `amount`）。实测东财
    push2/push2his 接口在本环境间歇性被代理拒绝(ProxyError)，而新浪后端稳定可用。
    每标的最多重试 _MAX_RETRY 次。
    """
    import akshare as ak

    end = end or datetime.now().strftime("%Y%m%d")
    start = start or (datetime.now() - timedelta(days=_DEFAULT_WINDOW_DAYS)).strftime("%Y%m%d")
    try:
        symbols = get_a_share_universe()
    except Exception as e:  # noqa: BLE001
        return {"status": "skipped", "reason": str(e)}

    n = len(symbols)
    total = 0
    fail = 0

    def _one(ts_code: str) -> list[tuple]:
        ex, code = str(ts_code).split(".")
        sina_sym = f"{ex.lower()}{code}"  # sh600519 / sz000001 / bj...
        d = None
        for attempt in range(_MAX_RETRY):
            try:
                d = ak.stock_zh_a_daily(
                    symbol=sina_sym, start_date=start, end_date=end, adjust=""
                )
                break
            except Exception as e:  # noqa: BLE001
                if attempt < _MAX_RETRY - 1:
                    time.sleep(2)
                else:
                    logger.debug(f"amount 拉取失败 {ts_code}: {e}")
        if d is None or d.empty or "amount" not in d.columns:
            return []
        out = []
        for _, row in d.iterrows():
            amt = row.get("amount")
            if amt is None or pd.isna(amt):
                continue
            ts = pd.to_datetime(row["date"]).to_pydatetime()
            out.append((ts_code, ts, float(amt)))
        return out

    with ThreadPoolExecutor(max_workers=_WORKERS) as ex:
        futs = {ex.submit(_one, s): s for s in symbols}
        done = 0
        for fut in as_completed(futs):
            rows = fut.result() or []
            if rows:
                total += repository.update_amounts(rows)
            else:
                fail += 1
            done += 1
            if done % batch == 0:
                logger.info(
                    "amount 回填进度",
                    extra={"task": "amount_refresh", "done": done, "total": n,
                           "updated": total, "failed": fail},
                )
    logger.info(
        "amount 回填完成",
        extra={"task": "amount_refresh", "start": start, "end": end, "updated": total, "universe": n},
    )
    return {"status": "done", "start": start, "end": end, "updated": total, "universe": n}
