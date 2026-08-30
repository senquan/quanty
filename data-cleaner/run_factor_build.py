"""全市场因子库构建（后台任务）

用法：
    python run_factor_build.py                          # 全市场全区间重建
    python run_factor_build.py --start 2026-08-01       # 只重建近期
    python run_factor_build.py --category momentum      # 只算某一类
    python run_factor_build.py --symbols 600519.SH,000001.SZ

进度日志：data/factor_build.log
"""
import argparse
import datetime as dt
import logging
import sys

sys.path.insert(0, ".")

logging.basicConfig(
    filename="data/factor_build.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("factor_build")

from app.tasks import factor_build as fb  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--category", default=None, help="逗号分隔，如 momentum,technical")
    parser.add_argument("--symbols", default=None, help="逗号分隔标的代码")
    args = parser.parse_args()

    categories = args.category.split(",") if args.category else None
    symbols = args.symbols.split(",") if args.symbols else None

    logger.info(f"=== 因子库构建开始 {dt.datetime.now()} {vars(args)} ===")
    summary = fb.build_factor_library(
        symbols=symbols, start=args.start, end=args.end, categories=categories
    )
    logger.info(f"=== 因子库构建结束 {dt.datetime.now()} ===")
    logger.info(str(summary))


if __name__ == "__main__":
    main()
