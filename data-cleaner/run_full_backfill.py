"""全量 A 股日线历史回填（后台任务）

用法：python run_full_backfill.py
- 获取全 A 股代码池（get_a_share_universe）
- backfill_universe(source=alphafeed, full=True) 全量 2010 起
- 含 429 限频退避；进度日志写 data/backfill_full.log
"""
import sys
import logging
import datetime as dt

sys.path.insert(0, ".")

logging.basicConfig(
    filename="data/backfill_full.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("full_backfill")

from app.ingestion.universe import get_a_share_universe
from app.tasks import backfill as b

def main():
    logger.info("=== 全量回填开始 ===")
    symbols = get_a_share_universe()
    logger.info(f"universe={len(symbols)}")
    summary = b.backfill_universe(source="alphafeed", symbols=symbols, full=True)
    logger.info(f"=== 全量回填结束 {dt.date.today()} ===")
    logger.info(str(summary))

if __name__ == "__main__":
    main()
