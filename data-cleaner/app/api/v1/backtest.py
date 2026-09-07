"""脚本策略回测接口（data-cleaner 持有 A 股行情与计算）

- ``POST /api/v1/backtest/script``  跑一次脚本策略回测
- ``GET  /api/v1/backtest/styles``  口径表与复权口径（供前端下拉）

为什么放 dc：A 股行情在 ``factor.raw_bars``，backend 拿不到。
撮合逻辑见 ``app/backtest/``（R2 落地），本文件只做三件事：
**收参数 → 调 service → 把拒绝翻译成 422**。

状态码约定（backend 转发时照此透传）：

- ``422`` 闸口拒绝 / 取数失败 —— **这是「这个请求不成立」，不是服务端出错**。
  detail 固定为 ``{"reason": ..., "remedy": ...}``，前端直接展示。
- ``500`` 其余异常。

受 X-API-Key 保护（与 factor / strategy 一致）。
"""
import asyncio
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.backtest.data import BacktestDataError
from app.backtest.gate import PRICE_FIELDS, PRICE_FIELD_NOTE, STYLES
from app.backtest.market_rules import DATA_SOURCE, SYMBOL_HINTS
from app.backtest.service import BacktestRefused, ScriptBacktestRequest, run_script_backtest
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/backtest", tags=["脚本策略回测"])


class ScriptBacktestPayload(BaseModel):
    symbol: str = Field(..., description=f"标的代码，如 {SYMBOL_HINTS}")
    start: str = Field(..., description="起始日期 YYYY-MM-DD")
    end: str = Field(..., description="结束日期 YYYY-MM-DD")
    code: str = Field(..., description="策略代码（Python，可用 buy/sell/on_data）")
    style: str = Field("swing", description="口径：long / swing / intraday")
    initial_capital: float = Field(100_000.0, gt=0)
    allow_short: bool = False
    price_field: str = Field("qfq", description="复权口径：qfq / hfq")
    apply_market_rules: bool = Field(True, description="False = 不套市场规则（仅供纯算法验证）")


@router.post("/script")
async def script_backtest(payload: ScriptBacktestPayload) -> Dict[str, Any]:
    """跑一次脚本策略回测。

    计算是同步阻塞的（读 PG + 逐 bar 撮合），丢线程池执行，
    不能把事件循环堵在请求里。
    """
    req = ScriptBacktestRequest(
        symbol=payload.symbol,
        start=payload.start,
        end=payload.end,
        code=payload.code,
        style=payload.style,
        initial_capital=payload.initial_capital,
        allow_short=payload.allow_short,
        price_field=payload.price_field,
        apply_market_rules=payload.apply_market_rules,
    )

    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, run_script_backtest, req)
    except BacktestRefused as e:
        # 闸口拦下：422 而不是 500/400 —— 这是「这个回测不成立」
        raise HTTPException(status_code=422, detail=e.as_dict()) from e
    except BacktestDataError as e:
        raise HTTPException(status_code=422, detail=e.as_dict()) from e
    except Exception as e:  # noqa: BLE001
        logger.exception(f"脚本策略回测失败: {e}")
        raise HTTPException(status_code=500, detail=f"回测失败: {e}") from e


@router.get("/styles")
async def styles() -> Dict[str, Any]:
    """口径与复权口径表 —— 前端下拉与说明文案的唯一来源。

    闸口认哪些值、每个口径最少要多少根 bar，都写在 ``gate.py``；
    这里只是把它暴露出去，避免前端另抄一份然后跟闸口对不上。
    """
    return {
        "data_source": DATA_SOURCE,
        "symbol_hints": SYMBOL_HINTS,
        "styles": [
            {
                "key": s.key,
                "label": s.label,
                "holding": s.holding,
                "min_bars": s.min_bars,
                "why_min": s.why_min,
            }
            for s in STYLES.values()
        ],
        "price_fields": [
            {"key": pf, "note": PRICE_FIELD_NOTE[pf]} for pf in PRICE_FIELDS
        ],
    }
