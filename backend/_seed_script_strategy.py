"""把 id=14 那条误存的因子策略配置,替换成能跑脚本回测的示例策略。

背景:
  ``strategies`` 表是**脚本策略**表(``code`` 存 Python,id 被
  ``backtest_results.strategy_id`` 外键引用)。id=14 现在存的是一段
  **因子策略配置 JSON**(``{"top_n": 5, "filters": ...}``),而且 ``is_active=False``
  —— 拿它跑脚本回测只会得到「策略执行失败」,是废数据。

做法:
  **原地 UPDATE**(不是删了重建),这样 ``backtest_results`` 里那条历史记录
  的外键仍然有效,不用连带清理。

用法(默认 dry-run,只打印不写库):
  .venv/Scripts/python.exe _seed_script_strategy.py
  .venv/Scripts/python.exe _seed_script_strategy.py --apply
"""
import asyncio
import sys
from datetime import datetime

from sqlalchemy import text

from app.core.database import AsyncSessionLocal

TARGET_ID = 14

# 与前端 edit.vue 的「双均线交叉策略」模板一致,也与 dc 侧 R2–R5 冒烟用的是同一段。
# 选它的理由:它是**已知能跑出结果**的(600519.SH:1,194 bars / -15.41% / 25 笔),
# 不是「看起来像策略」的代码。
CODE = '''# 双均线交叉策略
def on_data(data, context):
    import pandas as pd

    close = data['close']
    sma_20 = close.rolling(window=20).mean()
    sma_50 = close.rolling(window=50).mean()

    for i in range(50, len(close)):
        # 金叉买入
        if sma_20.iloc[i-1] <= sma_50.iloc[i-1] and sma_20.iloc[i] > sma_50.iloc[i]:
            buy(close.iloc[i])

        # 死叉卖出
        elif sma_20.iloc[i-1] >= sma_50.iloc[i-1] and sma_20.iloc[i] < sma_50.iloc[i]:
            position = get_position()
            if position > 0:
                sell(close.iloc[i], position)
'''.strip()

NAME = "双均线交叉（示例脚本策略）"
DESCRIPTION = (
    "脚本回测示例：SMA20/50 金叉买入、死叉卖出。"
    "可直接在回测页选它跑 A 股（如 600519.SH）—— 原 id=14 存的是因子策略配置 JSON，"
    "跑脚本回测必然失败，已替换。"
)


def hr(title: str) -> None:
    print("=" * 72)
    print(title)


async def main(apply: bool) -> int:
    async with AsyncSessionLocal() as s:
        row = (await s.execute(text(
            "SELECT id, name, description, user_id, is_active, code FROM strategies WHERE id=:i"
        ), {"i": TARGET_ID})).first()
        if row is None:
            print(f"strategies 里没有 id={TARGET_ID},不做事")
            return 1

        hr(f"现状 id={TARGET_ID}")
        print(f"  name      : {row[1]}")
        print(f"  description: {row[2]}")
        print(f"  user_id   : {row[3]}   is_active: {row[4]}")
        print(f"  code({len(row[5] or '')} 字符):")
        for line in (row[5] or "").splitlines():
            print(f"    | {line}")

        hr(f"将写入 id={TARGET_ID}")
        print(f"  name      : {NAME}")
        print(f"  description: {DESCRIPTION}")
        print(f"  is_active : True")
        print(f"  code({len(CODE)} 字符):")
        for line in CODE.splitlines():
            print(f"    | {line}")

        # 外键影响面：写之前先摆清楚,别改完才发现挂着的记录失效
        refs = (await s.execute(text(
            "SELECT count(*) FROM backtest_results WHERE strategy_id=:i"
        ), {"i": TARGET_ID})).scalar()
        print()
        print(f"  外键引用：backtest_results.strategy_id={TARGET_ID} → {refs} 行"
              f"（原地 UPDATE 不影响它们）")

        if not apply:
            hr("dry-run：未写库。确认无误后加 --apply")
            return 0

        await s.execute(text(
            "UPDATE strategies SET name=:n, description=:d, code=:c, "
            "is_active=true, updated_at=:u WHERE id=:i"
        ), {"n": NAME, "d": DESCRIPTION, "c": CODE, "u": datetime.utcnow(), "i": TARGET_ID})
        await s.commit()

        after = (await s.execute(text(
            "SELECT id, name, is_active, length(code) FROM strategies WHERE id=:i"
        ), {"i": TARGET_ID})).first()

        hr("已写入")
        print(f"  id={after[0]}  name={after[1]}  is_active={after[2]}  code_len={after[3]}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main("--apply" in sys.argv)))
