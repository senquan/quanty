"""补回填：只处理 PG 中缺失的标的（断点续跑）

用法：python run_missing_backfill.py [--all]
- 默认读取 data/missing_symbols.json（由原脚本或手动生成）中的缺口列表
- --all：重新对比 universe 与 PG，现场计算缺口
- 进度日志写 data/backfill_missing.log
"""
import sys
import json
import logging
import datetime as dt

sys.path.insert(0, ".")

logging.basicConfig(
    filename="data/backfill_missing.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("missing_backfill")

from sqlalchemy import create_engine, text

from app.core.config import settings
from app.ingestion.universe import get_a_share_universe
from app.tasks import backfill as b


def pg_symbols() -> set:
    url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    eng = create_engine(url, future=True)
    with eng.connect() as c:
        return set(r[0] for r in c.execute(text("SELECT DISTINCT symbol FROM factor.raw_bars")))


def gap_symbols(target_date: str) -> list[str]:
    """返回最新日期早于 target_date 的标的（即缺某一天数据的）。"""
    from sqlalchemy import text

    from app.core.config import settings

    url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    eng = create_engine(url, future=True)
    sql = """
        SELECT symbol FROM factor.raw_bars
        GROUP BY symbol
        HAVING MAX(timestamp)::date < DATE :d
        ORDER BY symbol
    """
    with eng.connect() as c:
        return [r[0] for r in c.execute(text(sql), {"d": target_date})]


def market_latest_date() -> str:
    from sqlalchemy import text

    from app.core.config import settings

    url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    eng = create_engine(url, future=True)
    with eng.connect() as c:
        return str(c.execute(text("SELECT MAX(timestamp)::date FROM factor.raw_bars")).scalar())


def main():
    recompute = "--all" in sys.argv
    if recompute:
        uni = get_a_share_universe()
        have = pg_symbols()
        missing = [s for s in uni if s not in have]
        json.dump(missing, open("data/missing_symbols.json", "w"))
        logger.info(f"重算缺口 universe={len(uni)} have={len(have)} missing={len(missing)}")
    else:
        try:
            missing = json.load(open("data/missing_symbols.json"))
        except FileNotFoundError:
            logger.error("缺少 data/missing_symbols.json，请加 --all 重算")
            return

    # 1) 完全无数据的标的：全量补历史
    logger.info(f"=== 缺史补回填开始 {dt.datetime.now()} missing={len(missing)} ===")
    summary = b.backfill_universe(source="alphafeed", symbols=missing, full=True)
    logger.info(f"=== 缺史补回填结束 {dt.datetime.now()} ===")
    logger.info(str(summary))

    # 2) 有历史但最新日期落后于市场最新交易日的标的：增量补齐
    target = market_latest_date()
    gap = gap_symbols(target)
    logger.info(f"=== 日期缺口补齐开始 {dt.datetime.now()} target={target} gap={len(gap)} ===")
    gap_summary = b.backfill_universe(source="alphafeed", symbols=gap, full=False)
    logger.info(f"=== 日期缺口补齐结束 {dt.datetime.now()} ===")
    logger.info(str(gap_summary))

    # 刷新缺口清单
    try:
        uni = get_a_share_universe()
        have = pg_symbols()
        rest = [s for s in uni if s not in have]
        json.dump(rest, open("data/missing_symbols.json", "w"))
        logger.info(f"剩余缺史={len(rest)} | 剩余日期缺口={len(gap_symbols(market_latest_date()))}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"刷新缺口失败: {e}")


if __name__ == "__main__":
    main()
