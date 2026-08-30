"""行业分类缓存

从 tushare `stock_basic` 拉取全 A 股行业分类与上市日期，落库到 `factor.industries`，
供因子行业中性化与上市天数过滤使用。计算时直接读表，不在每次调仓时调用外部 API。

降级：tushare 不可用时尝试 akshare（仅能拿到名称，行业字段为空，中性化会退化
为全市场标准化，由调用方处理）。
"""
from app.core.config import settings
from app.core.logging import get_logger
from app.storage import db

logger = get_logger(__name__)


def refresh_industries() -> dict:
    """刷新行业分类缓存。返回 {count, source, updated_at}。"""
    token = getattr(settings, "TUSHARE_TOKEN", None)
    rows: list[dict] = []
    source = ""

    if token:
        try:
            import tushare as ts

            pro = ts.pro_api(token)
            df = pro.stock_basic(
                exchange="",
                list_status="L",
                fields="ts_code,symbol,name,industry,list_status,list_date",
            )
            if df is not None and not df.empty:
                rows = [
                    {
                        "symbol": r["ts_code"],
                        "name": r["name"],
                        "industry": (r.get("industry") or "") or None,
                        "list_status": r.get("list_status") or None,
                        "list_date": _to_date(r.get("list_date")),
                    }
                    for _, r in df.iterrows()
                ]
                source = "tushare"
        except Exception as e:  # noqa: BLE001
            logger.warning(f"tushare 行业分类拉取失败，尝试 akshare: {e}")

    if not rows:
        try:
            import akshare as ak

            df = ak.stock_info_a_code_name()
            df["code"] = df["code"].astype(str)
            from app.ingestion.universe import _from_akshare_code

            rows = [
                {
                    "symbol": _from_akshare_code(c),
                    "name": nm,
                    "industry": None,
                    "list_status": "L",
                    "list_date": None,
                }
                for c, nm in zip(df["code"], df["name"], strict=False)
            ]
            source = "akshare"
        except Exception as e:  # noqa: BLE001
            logger.error(f"akshare 行业分类拉取也失败: {e}")
            raise RuntimeError("无法获取行业分类：tushare 与 akshare 均不可用")

    if not rows:
        raise RuntimeError("行业分类为空")

    db.run_async(_upsert_all(rows))
    logger.info("行业分类刷新完成", extra={"count": len(rows), "source": source})
    return {"count": len(rows), "source": source}


def _to_date(v):
    if not v:
        return None
    try:
        return __import__("datetime").datetime.strptime(str(v), "%Y%m%d").date()
    except Exception:  # noqa: BLE001
        return None


async def _upsert_all(rows: list[dict]) -> None:
    from datetime import datetime

    async with db.current_session() as session:
        for r in rows:
            await session.execute(
                db.text(
                    """
                    INSERT INTO factor.industries
                        (symbol, name, industry, list_status, list_date, updated_at)
                    VALUES (:symbol, :name, :industry, :list_status, :list_date, :ts)
                    ON CONFLICT (symbol) DO UPDATE SET
                        name = EXCLUDED.name,
                        industry = EXCLUDED.industry,
                        list_status = EXCLUDED.list_status,
                        list_date = EXCLUDED.list_date,
                        updated_at = EXCLUDED.updated_at
                    """
                ),
                {
                    "symbol": r["symbol"],
                    "name": r["name"],
                    "industry": r["industry"],
                    "list_status": r["list_status"],
                    "list_date": r["list_date"],
                    "ts": datetime.now(),
                },
            )
        await session.commit()


async def get_industry_map() -> dict[str, str]:
    """返回 {symbol: industry}（行业为空的归类为 '未知'）。"""
    async with db.current_session() as session:
        res = await session.execute(
            db.text("SELECT symbol, industry FROM factor.industries")
        )
        return {r[0]: (r[1] or "未知") for r in res.all()}


async def get_meta_map() -> dict[str, dict]:
    """返回 {symbol: {name, industry, list_status, list_date}}。"""
    async with db.current_session() as session:
        res = await session.execute(
            db.text(
                "SELECT symbol, name, industry, list_status, list_date "
                "FROM factor.industries"
            )
        )
        return {
            r[0]: {
                "name": r[1],
                "industry": r[2] or "未知",
                "list_status": r[3],
                "list_date": r[4],
            }
            for r in res.all()
        }


# --------------------------------------------------------------------------- #
# 同步包装（供因子引擎等同步上下文调用，复用 db.run_async 固定事件循环）
# --------------------------------------------------------------------------- #
def _sync_industry_map() -> dict[str, str]:
    return db.run_async(get_industry_map())


def _sync_meta_map() -> dict[str, dict]:
    return db.run_async(get_meta_map())


def _run(coro_func, *args, **kwargs):
    return db.run_async(coro_func(*args, **kwargs))
