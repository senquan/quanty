"""阶段 B 端到端验证（临时脚本，跑完即删）"""
import asyncio
import httpx

BASE = "http://127.0.0.1:8001"
CLEANER = "http://127.0.0.1:8100"


def extract(j):
    """兼容 Response 包裹 / 裸 list"""
    return j.get("data") if isinstance(j, dict) and "data" in j else j


async def main():
    async with httpx.AsyncClient(timeout=10) as c:
        # 1) 登录
        r = await c.post(f"{BASE}/api/v1/auth/login",
                          data={"username": "vben", "password": "123456"})
        print("login:", r.status_code, r.json().get("code"))
        token = r.json()["data"]["access_token"]
        h = {"Authorization": f"Bearer {token}"}

        # 2) 注册清洗服务
        body = {"service_code": "cleaner_dev2", "name": "开发清洗服务2",
                "base_url": CLEANER, "api_key": "dev-key"}
        r = await c.post(f"{BASE}/api/v1/cleaner", json=body, headers=h)
        print("register:", r.status_code, r.json())

        # 3) 列表
        r = await c.get(f"{BASE}/api/v1/cleaner", headers=h)
        print("list raw:", r.status_code, r.text[:400])

        # 4) sync 因子
        r = await c.post(f"{BASE}/api/v1/cleaner/cleaner_dev2/sync", headers=h)
        print("sync:", r.status_code, r.json())

        # 5) 聚合底册
        r = await c.get(f"{BASE}/api/v1/cleaner/factors/registry", headers=h)
        print("registry raw:", r.status_code, r.text[:500])
        rows = extract(r.json()) or []
        print("registry count:", len(rows), "sample:", (rows[0] if rows else None))

        # 6) 批量勾选（全部）
        r = await c.post(f"{BASE}/api/v1/cleaner/cleaner_dev2/factors/enable",
                         json={"service_code": "cleaner_dev2", "is_enabled": True}, headers=h)
        print("enable:", r.status_code, r.json())

        # 7) 仅查已勾选
        r = await c.get(f"{BASE}/api/v1/cleaner/factors/registry?only_enabled=true", headers=h)
        print("enabled registry count:", len(extract(r.json())))


asyncio.run(main())
