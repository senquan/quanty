"""Docker 容器内 API 冒烟验证（服务已在 8100 运行）"""
import json
import urllib.request

BASE = "http://127.0.0.1:8100"


def get(path):
    with urllib.request.urlopen(BASE + path) as r:
        return r.status, json.loads(r.read())


def main():
    s, b = get("/api/v1/health")
    print("health:", s, b)
    assert s == 200 and b["status"] == "healthy"

    s, factors = get("/api/v1/factor/")
    cats = sorted({f["category"] for f in factors})
    print(f"factor list: {s}, count={len(factors)}, categories={cats}")
    assert s == 200 and len(factors) >= 15

    s, b = get("/api/v1/factor/TECH_RSI_14")
    print("factor detail:", s, b["code"])
    assert s == 200

    s, b = get("/api/v1/pipeline/status")
    print("pipeline status:", s, b)
    assert s == 200

    print("\n✅ Docker 冒烟验证全部通过")


if __name__ == "__main__":
    main()
