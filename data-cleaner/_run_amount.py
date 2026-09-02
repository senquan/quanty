"""6.4 amount 回填（akshare，高并发+重试），独立后台进程。"""
import sys
import time

sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()

from app.core.logging import get_logger
logger = get_logger("amount_backfill")
from app.tasks import amount_refresh


def main():
    t0 = time.time()
    r = amount_refresh.refresh_amount()
    logger.info("amount 完成", extra=r)
    logger.info("amount 总耗时", extra={"duration_s": round(time.time() - t0, 1)})


if __name__ == "__main__":
    main()
