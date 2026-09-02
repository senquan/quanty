"""临时：验证因子底册本地化（dc 宕机场景）。用完即删。"""
import asyncio
import logging
import time

logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.WARNING)

import httpx  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app.api.api_v1.endpoints.auth import get_current_user  # noqa: E402
from app.core.database import AsyncSessionLocal  # noqa: E402
from app.core.dependencies import get_current_user as deps_get_current_user  # noqa: E402
from app.models.cleaner import CleanerService, FactorRegistry  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services import cleaner_gateway as gw  # noqa: E402
from main import app  # noqa: E402


def fake_user():
    u = User()
    u.id = 1
    u.username = "verify"
    u.email = "v@t.com"
    return u


# factor_library 从 app.core.dependencies 导入，factor_strategy 从 auth 导入，两个都要覆盖
app.dependency_overrides[get_current_user] = fake_user
app.dependency_overrides[deps_get_current_user] = fake_user


async def probe(c, label, url, **kw):
    s = time.perf_counter()
    try:
        r = await c.get(url, **kw)
        body = r.json()
        data = body.get("data") if isinstance(body, dict) else None
        shown = data if not isinstance(data, list) else f"[{len(data)} items] {str(data[:1])[:200]}"
        print(f"PROBE|{label}|HTTP {r.status_code}|{time.perf_counter() - s:.2f}s|{shown}")
    except Exception as e:  # noqa: BLE001
        print(f"PROBE|{label}|FAIL|{time.perf_counter() - s:.2f}s|{str(e)[:120]}")


async def main():
    await gw.ensure_cleaner_tables()

    # 1) 确认表结构与现有服务
    async with AsyncSessionLocal() as s:
        svcs = (await s.execute(select(CleanerService))).scalars().all()
        print("SERVICES|", [(x.service_code, x.base_url, x.status) for x in svcs])
        regs = (await s.execute(select(FactorRegistry))).scalars().all()
        print("REGISTRY_ROWS|", len(regs))
        cols = (
            await s.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='factor_registry' ORDER BY 1"
                )
            )
        ).fetchall()
        print("COLUMNS|", [c[0] for c in cols])

        svc_code = svcs[0].service_code if svcs else "dc-local"

        # 2) 没有登记服务时先造一个（指向已僵死的 dc 8100）
        if not svcs:
            s.add(
                CleanerService(
                    service_code=svc_code,
                    name="本地 data-cleaner",
                    base_url="http://127.0.0.1:8100",
                    api_key="",
                    status="unknown",
                    is_active=True,
                )
            )
            await s.commit()
            print("SERVICE_CREATED|" + svc_code)

        # 3) 造一条"已入库"因子，模拟用户已完成入库
        exists = (
            await s.execute(
                select(FactorRegistry).where(FactorRegistry.factor_code == "MOM_RET_20")
            )
        ).scalar_one_or_none()
        if not exists:
            s.add(
                FactorRegistry(
                    service_code=svc_code,
                    factor_code="MOM_RET_20",
                    name="20日动量",
                    category="momentum",
                    frequency="Daily",
                    formula="close.pct_change(20)",
                    data_source="adj_close",
                    is_enabled=True,
                    metrics={
                        "as_of_date": "2026-08-30",
                        "ic_mean": 0.031,
                        "ir": 0.42,
                    },
                )
            )
            await s.commit()
            print("REGISTRY_SEEDED|MOM_RET_20")

    # 4) 打接口（dc 僵死，验证不超时）
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t", timeout=60.0
    ) as c:
        await probe(c, "GET /factors", "/api/v1/factors")
        await probe(c, "GET /factors/services", "/api/v1/factors/services")
        await probe(
            c,
            "GET /factor-strategies/factors/availability",
            "/api/v1/factor-strategies/factors/availability",
        )

    # 5) 清理造的数据
    async with AsyncSessionLocal() as s:
        row = (
            await s.execute(
                select(FactorRegistry).where(FactorRegistry.factor_code == "MOM_RET_20")
            )
        ).scalar_one_or_none()
        if row:
            await s.delete(row)
            await s.commit()
            print("CLEANED|seed row removed")


asyncio.run(main())
