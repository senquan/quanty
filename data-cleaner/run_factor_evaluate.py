"""因子效能评估（后台任务）

用法：
    python run_factor_evaluate.py                          # 评估因子库中全部因子
    python run_factor_evaluate.py --category momentum       # 只评估某类别
    python run_factor_evaluate.py --codes MOM_RET_20,TECH_RSI_14

进度日志：data/factor_evaluate.log
"""
import argparse
import datetime as dt
import logging
import sys

sys.path.insert(0, ".")

logging.basicConfig(
    filename="data/factor_evaluate.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("factor_evaluate")

from app.tasks import factor_evaluate as fe  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default=None, help="评估基准日，默认今天")
    parser.add_argument("--category", default=None, help="逗号分隔的类别")
    parser.add_argument("--codes", default=None, help="逗号分隔的因子代码")
    args = parser.parse_args()

    categories = args.category.split(",") if args.category else None
    codes = args.codes.split(",") if args.codes else None

    logger.info(f"=== 因子效能评估开始 {dt.datetime.now()} {vars(args)} ===")
    summary = fe.evaluate_all_factors(
        as_of=args.as_of, categories=categories, codes=codes
    )
    logger.info(f"=== 因子效能评估结束 {dt.datetime.now()} ===")
    logger.info(str(summary))


if __name__ == "__main__":
    main()
