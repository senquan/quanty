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

    logger.info(f"=== 缺口补回填开始 {dt.datetime.now()} missing={len(missing)} ===")
    summary = b.backfill_universe(source="alphafeed", symbols=missing, full=True)
    logger.info(f"=== 缺口补回填结束 {dt.datetime.now()} ===")
    logger.info(str(summary))

    # 刷新缺口清单
    try:
        uni = get_a_share_universe()
        have = pg_symbols()
        rest = [s for s in uni if s not in have]
        json.dump(rest, open("data/missing_symbols.json", "w"))
        logger.info(f"剩余缺口={len(rest)}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"刷新缺口失败: {e}")


if __name__ == "__main__":
    main()
