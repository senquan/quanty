"""每日盘后流水线（手动触发 / 后台任务）

顺序执行：增量拉取行情 → 因子库更新 → 因子效能评估。

用法：
    python run_daily_pipeline.py
    python run_daily_pipeline.py --wait-rounds 1 --wait-minutes 0   # 不等待数据源，跑一轮即止

进度日志：data/daily_pipeline.log
"""
import argparse
import datetime as dt
import logging
import sys

sys.path.insert(0, ".")

logging.basicConfig(
    filename="data/daily_pipeline.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("daily_pipeline")

from app.tasks import daily_pipeline as dp  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="alphafeed")
    parser.add_argument("--wait-rounds", type=int, default=dp.DEFAULT_WAIT_ROUNDS)
    parser.add_argument("--wait-minutes", type=int, default=dp.DEFAULT_WAIT_MINUTES)
    parser.add_argument(
        "--symbols", default=None, help="逗号分隔，仅拉取指定标的（调试用）"
    )
    parser.add_argument(
        "--force", action="store_true", help="非交易日也强制执行"
    )
    args = parser.parse_args()

    logger.info(f"=== 每日盘后流水线开始 {dt.datetime.now()} {vars(args)} ===")
    summary = dp.run_daily_pipeline(
        source=args.source,
        wait_rounds=args.wait_rounds,
        wait_minutes=args.wait_minutes,
        symbols=args.symbols.split(",") if args.symbols else None,
        force=args.force,
    )
    logger.info(f"=== 每日盘后流水线结束 {dt.datetime.now()} ===")
    logger.info(str(summary))


if __name__ == "__main__":
    main()
