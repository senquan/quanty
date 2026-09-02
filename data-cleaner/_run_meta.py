"""6.5 行业 + 上市日期回填（tushare 行业 + akshare 上市日），独立后台进程。"""
import sys
import time

sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()

from app.core.logging import get_logger
logger = get_logger("meta_backfill")
from app.tasks import metadata_refresh


def main():
    t0 = time.time()
    r = metadata_refresh.refresh_industry_listdate()
    logger.info("industry/list_date 完成", extra=r)
    logger.info("meta 总耗时", extra={"duration_s": round(time.time() - t0, 1)})


if __name__ == "__main__":
    main()
