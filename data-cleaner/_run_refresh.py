"""一次性补齐 6.2–6.6 所需数据：eps(财务)/amount(成交额)/行业+上市日/股息。"""
import sys
import time

sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()

from app.core.logging import get_logger

logger = get_logger("refresh_all")

from app.tasks import amount_refresh, metadata_refresh
from app.tasks.fundamental_refresh import refresh_financial_indicator


def main():
    t0 = time.time()
    logger.info("=== [1/4] 财务指标(含 eps/net_profit, start_year=2020) ===")
    r1 = refresh_financial_indicator(start_year="2020")
    logger.info("财务指标完成", extra=r1)

    logger.info("=== [2/4] 成交额 amount(pandadata) ===")
    r2 = amount_refresh.refresh_amount()
    logger.info("amount 完成", extra=r2)

    logger.info("=== [3/4] 行业 + 上市日期(akshare) ===")
    r3 = metadata_refresh.refresh_industry_listdate()
    logger.info("行业/上市日完成", extra=r3)

    logger.info("=== [4/4] 股息(akshare, 尽力) ===")
    r4 = metadata_refresh.refresh_dividend()
    logger.info("股息完成", extra=r4)

    logger.info("全部刷新完成", extra={"duration_s": round(time.time() - t0, 1)})


if __name__ == "__main__":
    main()
