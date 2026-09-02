"""A 股全市场代码池获取

提供 get_a_share_universe() 返回全 A 股（沪/深/京）代码列表（带 .SH/.SZ/.BJ 后缀），
供增量历史回填遍历使用。

优先 tushare stock_basic（已配 TUSHARE_TOKEN）；
降级 akshare stock_info_a_code_name；
再降级返回空并告警（避免静默空跑）。
"""
import asyncio

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _to_tushare_code(ts_code: str) -> str:
    """600519.SH 已是标准格式，BJ 市场 akshare 用 .BJ。这里做最小归一。"""
    return ts_code


def _from_akshare_code(code: str) -> str:
    """akshare 返回 6 位纯数字，需补交易所后缀。"""
    if code.startswith("6"):
        return f"{code}.SH"
    if code.startswith(("0", "3")):
        return f"{code}.SZ"
    # 北交所：4/8 老号段 + 920 新号段（2024 起启用）
    if code.startswith(("4", "8", "920")):
        return f"{code}.BJ"
    return code


_MIN_UNIVERSE = 1000  # 外部源返回少于此数视为受限/截断，不可信，降级下一源


def get_a_share_universe(use_cache: bool = True) -> list[str]:
    """返回全 A 股代码列表（带交易所后缀）。失败时抛 RuntimeError。"""
    # 1) tushare
    token = getattr(settings, "TUSHARE_TOKEN", None)
    if token:
        try:
            import tushare as ts

            pro = ts.pro_api(token)
            out: list[str] = []
            for exchange in ("SSE", "SZSE", "BSE"):
                df = pro.stock_basic(
                    exchange=exchange, list_status="L", fields="ts_code"
                )
                if df is not None and not df.empty:
                    out.extend(df["ts_code"].tolist())
            if len(out) >= _MIN_UNIVERSE:
                logger.info("universe from tushare", extra={"count": len(out)})
                return out
            logger.warning(f"tushare universe 过少({len(out)})，尝试 akshare")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"tushare universe 失败，尝试 akshare: {e}")

    # 2) akshare 降级
    try:
        import akshare as ak

        df = ak.stock_info_a_code_name()
        codes = df["code"].astype(str).tolist()
        out = [_from_akshare_code(c) for c in codes]
        if len(out) >= _MIN_UNIVERSE:
            logger.info("universe from akshare", extra={"count": len(out)})
            return out
        logger.warning(f"akshare universe 过少({len(out)})，降级 DB 兜底")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"akshare universe 失败: {e}")

    # 3) DB 兜底：外部源受限（如 akshare stock_basic 频率限制）时，
    # 用已落库的价量代码池 factor.raw_bars 作为宇宙——价量回填后即为可靠全 A 名单。
    try:
        from sqlalchemy import create_engine, text as _text

        url = getattr(settings, "DATABASE_URL", None)
        if url:
            if "+asyncpg" in url:
                url = url.replace("+asyncpg", "+psycopg2")
            elif url.startswith("postgresql") and not url.startswith("postgresql+psycopg2"):
                url = url.replace("postgresql", "postgresql+psycopg2", 1)
            eng = create_engine(url, pool_pre_ping=True, future=True)
            with eng.connect() as c:
                rows = c.execute(_text("SELECT DISTINCT symbol FROM factor.raw_bars")).fetchall()
            out = [r[0] for r in rows if r[0]]
            if out:
                logger.info("universe from factor.raw_bars", extra={"count": len(out)})
                return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"DB universe 兜底失败: {e}")

    raise RuntimeError(
        "无法获取 A 股代码池：tushare / akshare / factor.raw_bars 均不可用"
    )


async def get_a_share_universe_async(use_cache: bool = True) -> list[str]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, get_a_share_universe, use_cache)


# 板块 -> 名称关键词（用于从全 A 股按名称筛选成分，作为 tushare/index_member 不可用时的可靠降级）
_SECTOR_KEYWORDS: dict[str, list[str]] = {
    "电力": ["电力", "发电", "水电", "热电", "供电", "电网"],
    "银行": ["银行"],
    "证券": ["证券"],
    "保险": ["保险"],
}


def get_sector_universe(sector: str) -> list[str]:
    """返回某板块（如 '电力'）的成分代码列表（带后缀）。

    优先 tushare index_member（需权限）；
    降级：从全 A 股按板块名称关键词筛选（akshare 全市场名称，已验证可用）。
    """
    sector = sector.strip()
    # 1) tushare 行业/概念成分（若有对应指数代码或行业字段）
    token = getattr(settings, "TUSHARE_TOKEN", None)
    if token and sector in ("电力",):
        try:
            import tushare as ts

            pro = ts.pro_api(token)
            df = pro.stock_basic(
                exchange="", list_status="L",
                fields="ts_code,symbol,name,industry",
            )
            if df is not None and not df.empty:
                kw = _SECTOR_KEYWORDS.get(sector, [sector])
                sub = df[df["industry"].astype(str).str.contains("|".join(kw), na=False)]
                if not sub.empty:
                    return sub["ts_code"].tolist()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"tushare 板块成分失败，名称筛选降级: {e}")

    # 2) 从全 A 股按名称关键词筛选（akshare 全市场代码+名称）
    kw = _SECTOR_KEYWORDS.get(sector)
    if not kw:
        raise RuntimeError(f"未配置板块 {sector} 的关键词映射")
    try:
        import akshare as ak

        df = ak.stock_info_a_code_name()
        df["code"] = df["code"].astype(str)
        mask = df["name"].astype(str).str.contains("|".join(kw), na=False)
        codes = df.loc[mask, "code"].tolist()
        out = [_from_akshare_code(c) for c in codes]
        if out:
            logger.info(
                "sector universe by name",
                extra={"sector": sector, "count": len(out)},
            )
            return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"akshare 板块名称筛选失败: {e}")

    raise RuntimeError(f"无法获取板块 {sector} 成分：tushare 与 akshare 均不可用")
