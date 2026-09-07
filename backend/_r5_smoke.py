"""R5 冒烟：旧引擎退役后,backend 的 /quant/backtest 仍然跑得通。

R3 验的是 dc 侧,R4 验的是契约字段,R5 验的是**删掉模块之后 backend 没被删残**:
路由还在、导入链完整、转发仍然拿到结果。

用真实 HTTP 转发到在跑的 dc(8100),用真实库查 service 与 strategy,
但**不落库** —— 历史记录写入被换成 noop,避免冒烟污染 backtest_results。

用法：cd backend && .venv/Scripts/python.exe _r5_smoke.py
"""
import asyncio

import httpx
from sqlalchemy import select

from app.api.api_v1.endpoints.auth import get_current_user
from app.core.database import AsyncSessionLocal, get_db
from app.models.quant import Strategy
from app.models.user import User
from main import app

MA_CROSS = """
def on_data(data, context):
    close = data['close']
    sma_20 = close.rolling(window=20).mean()
    sma_50 = close.rolling(window=50).mean()
    for i in range(50, len(close)):
        if sma_20.iloc[i-1] <= sma_50.iloc[i-1] and sma_20.iloc[i] > sma_50.iloc[i]:
            buy(close.iloc[i])
        elif sma_20.iloc[i-1] >= sma_50.iloc[i-1] and sma_20.iloc[i] < sma_50.iloc[i]:
            pos = get_position()
            if pos > 0:
                sell(close.iloc[i], pos)
""".strip()


class NoCommitSession:
    """转发真实查询,但丢弃所有写入 —— 冒烟不该在 backtest_results 里留垃圾。

    ⚠️ 不要在 ``execute`` 里预先消费结果(``res.scalars().all()``):
    消费掉之后 endpoint 再调 ``.scalars().first()`` 就是空,
    表现为莫名其妙的 404 / 502。改 code 的事交给 identity map。
    """

    def __init__(self, real):
        self._real = real

    async def execute(self, *a, **k):
        return await self._real.execute(*a, **k)

    async def commit(self):
        return None

    async def refresh(self, *a, **k):
        return None

    def add(self, *a, **k):
        return None


async def pick_context(session):
    """在同一个 session 里取策略与所有者 —— 后面 endpoint 用的也是它。

    用的是**库里真实的 code**,不再做任何替换:
    id=14 原先存的是因子策略配置 JSON(跑脚本回测必然失败),
    2026-09-07 已换成双均线交叉脚本(见 _seed_script_strategy.py)。
    这样冒烟才真正在验「库里的策略能跑」。
    """
    strategy = (await session.execute(
        select(Strategy).order_by(Strategy.id)
    )).scalars().first()
    if strategy is None:
        return None, None
    user = (await session.execute(
        select(User).where(User.id == strategy.user_id)
    )).scalars().first()
    return strategy, user


async def main() -> int:
    # 全程只用**一个**事件循环、一个 session：
    # ① AsyncSessionLocal 的连接池绑在创建它的 loop 上,先 asyncio.run 再让
    #    TestClient 另起 loop 去用同一个池,asyncpg 会报
    #    'NoneType' object has no attribute 'send'；
    # ② strategy.code 的替换靠 identity map,必须 endpoint 与这里共用同一 session。
    real = AsyncSessionLocal()
    try:
        strategy, user = await pick_context(real)
        if strategy is None:
            print("库里没有策略,跳过(endpoint 的鉴权分支已由单测覆盖)")
            return 0

        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_db] = make_db(real)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://smoke") as client:
            ok = await _run_cases(client, strategy)
    finally:
        # 不 commit —— 上面若改了 ORM 对象,随连接关闭一起回滚
        await real.rollback()
        await real.close()

    print("=" * 72)
    print("全部通过" if ok else "存在失败用例")
    return 0 if ok else 1


def make_db(real):
    """必须是 async generator **函数**本身 —— 包一层 lambda 的话 FastAPI 认不出来,
    会把它当普通依赖直接 await,db 就变成 async_generator（报没有 execute）。"""
    async def _db():
        yield NoCommitSession(real)

    return _db


async def _run_cases(client, strategy) -> bool:
    ok = True

    # ① 口径表：验证 styles 端点转发仍通
    r = await client.get("/api/v1/quant/backtest/styles")
    print("=" * 72)
    print(f"GET /api/v1/quant/backtest/styles  [{r.status_code}]")
    if r.status_code == 200:
        body = r.json()["data"]
        print(f"  styles      : {[s['key'] for s in body['styles']]}")
        print(f"  price_fields: {[p['key'] for p in body['price_fields']]}")
        print("  ✓ 转发正常")
    else:
        print("  ✗", r.text[:300])
        ok = False

    # ② 回测：验证删掉 backtest_engine 之后,撮合改由 dc 承担仍然出结果
    payload = {
        "strategy_id": strategy.id,
        "symbol": "600519.SH",
        "start_date": "2021-10-08T00:00:00",
        "end_date": "2026-09-04T00:00:00",
        "initial_capital": 1_000_000.0,
        "style": "swing",
        "price_field": "qfq",
        "apply_market_rules": True,
    }
    r = await client.post("/api/v1/quant/backtest", json=payload)
    print("=" * 72)
    print(f"POST /api/v1/quant/backtest  600519.SH  [{r.status_code}]")
    if r.status_code == 200:
        d = r.json()["data"]
        meta = d.get("data") or {}
        print(f"  bars={meta.get('bars')}  {meta.get('first_date')} ~ {meta.get('last_date')}")
        print(f"  总收益={d['total_return']}%  成交={d['total_trades']}"
              f"  期末价值={d['final_capital']}  现金={d['cash']}  持仓={d['final_position']}")
        print(f"  limits={len(d.get('limits', []))} 条  warnings={len(d.get('warnings', []))} 条")
        print("  ✓ 端到端通(backend 只剩转发,撮合在 dc)")
    else:
        print("  ✗", r.text[:300])
        ok = False

    # ③ 闸口拒绝：dc 的 422 必须原样透传,不能被包成 500
    r = await client.post("/api/v1/quant/backtest", json={**payload, "symbol": "AAPL"})
    print("=" * 72)
    print(f"POST /api/v1/quant/backtest  AAPL(非A股)  [{r.status_code}]")
    if r.status_code == 422:
        # 统一包装后 dict 形态的 detail 落在 data 里,不是 detail 字段
        detail = r.json().get("data") or r.json().get("detail") or {}
        print(f"  msg={r.json().get('msg')}")
        print(f"  reason={detail.get('reason')}")
        print(f"  remedy={detail.get('remedy')}")
        assert detail.get("reason") and detail.get("remedy"), "闸口理由没传过来"
        print("  ✓ 422 原样透传(reason/remedy 都在)")
    else:
        print("  ✗ 期望 422,实际", r.status_code, r.text[:300])
        ok = False

    return ok


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
