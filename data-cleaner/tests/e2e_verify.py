"""Phase 1 端到端验证脚本（不依赖 shell 转义）

用法: python tests/e2e_verify.py  (需服务已启动于 8100)
"""
import json
import urllib.request

BASE = "http://127.0.0.1:8100"


def get(path):
    with urllib.request.urlopen(BASE + path) as r:
        return r.status, json.loads(r.read())


def post(path, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req) as r:
        return r.status, json.loads(r.read())


def main():
    s, b = get("/api/v1/health")
    print("health:", s, b)

    s, factors = get("/api/v1/factor/")
    cats = sorted({f["category"] for f in factors})
    print(f"factor list: {s}, count={len(factors)}, categories={cats}")

    s, b = get("/api/v1/factor/TECH_RSI_14")
    print("factor detail RSI:", s, b["code"])

    s, b = post(
        "/api/v1/pipeline/run",
        {
            "source": "csv",
            "start": "2023-01-01",
            "end": "2023-12-31",
            "freq": "1d",
            "csvPath": "tests/fixtures/sample_bars.csv",
        },
    )
    print("pipeline run:", s, b)

    s, b = get("/api/v1/pipeline/status")
    print("pipeline status:", s, b)

    # 验证因子 Parquet 已落盘
    import os

    fac_dir = "data/factors"
    if os.path.exists(fac_dir):
        for root, _, files in os.walk(fac_dir):
            for f in files:
                print("parquet file:", os.path.join(root, f))
    else:
        print("no parquet dir")


if __name__ == "__main__":
    main()
