"""补齐 6.4/6.5：amount(akshare 重试) + industry/list_date(tushare 行业 + akshare 上市日)。"""
import sys
import time

sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()

from app.core.logging import get_logger

logger = get_logger("refresh2")

from app.tasks import amount_refresh, metadata_refresh


def main():
    t0 = time.time()
    logger.info("=== [1/2] 成交额 amount (akshare) ===")
    r1 = amount_refresh.refresh_amount()
    logger.info("amount 完成", extra=r1)

    logger.info("=== [2/2] 行业 + 上市日期 ===")
    r2 = metadata_refresh.refresh_industry_listdate()
    logger.info("industry/list_date 完成", extra=r2)

    logger.info("全部完成", extra={"duration_s": round(time.time() - t0, 1)})


if __name__ == "__main__":
    main()
