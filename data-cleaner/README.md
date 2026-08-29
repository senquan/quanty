# data-cleaner 数据清洗服务

量化数据清洗与因子生产服务。负责从多源拉取行情/基本面数据，经清洗流水线产出多种技术面、情绪面、价值面、成长面与分钟级因子，落地到 Parquet（及可选 PostgreSQL），并通过 REST API 对外提供因子定义、计算、评估与 AI 生成能力。

> 所属项目：`lab.Quant`。本服务独立于主后端（`backend/`）部署，复用其 PostgreSQL（独立 `factor` schema）与 Redis 缓存。

---

## 1. 功能概览

| 模块 | 说明 |
|------|------|
| 数据接入 | yfinance（美股）、ccxt（加密）、csv（本地回测）、fundamental（tushare/akshare 财务，缺失时优雅降级） |
| 清洗流水线 | 6 个 Transformer：行情对齐 → 复权 → 去极值/缺失 → 类型校验 → 范围校验 → 结构校验(pandera) |
| 因子库 | 29 个因子，6 大类：动量 `MOM_`、波动率 `VOL_`、技术 `TECH_`、情绪 `SENT_`、价值 `VAL_`、成长 `GRO_`，另含分钟级 `INTRADAY_` |
| 存储 | Parquet 分区落地（按日期+类别）；可选 PostgreSQL 保存因子定义/效能/运行日志 |
| 调度 | APScheduler：交易日 18:00 全量日线因子，周六 09:00 效能重算，每 30s 心跳写 Redis |
| 实时缓存 | 流水线完成后刷新 Redis（`factor:status` / `factor:latest:{code}`），无 Redis 时自动降级 |
| 可观测 | 结构化 JSON 日志、Prometheus 风格 `/api/v1/metrics`、`pipeline_runs` 表、失败输入快照 `data/quarantine/` |
| AI 因子 | `POST /api/v1/factor/ai-generate` 自然语言→formula（沙箱 AST 校验，杜绝注入） |

---

## 2. 目录结构

```
data-cleaner/
├── app/
│   ├── main.py                 # FastAPI 入口（lifespan: 启停调度器/Redis）
│   ├── api/v1/                 # 路由：health/factor/analytics/data/pipeline/metrics
│   ├── core/                   # config/logging/metrics/exceptions
│   ├── ingestion/              # 数据源适配器（yfinance/ccxt/csv/fundamental）
│   ├── pipeline/               # 清洗流水线编排 + pandera 校验
│   ├── factors/                # 因子基类/注册表/各类因子/公式沙箱/AI生成
│   ├── storage/                # parquet 存储 + Redis 缓存 + DB
│   └── tasks/                  # APScheduler 定时任务
├── migrations/                 # 数据库迁移（因子定义/效能/运行日志表）
├── tests/                      # pytest 套件（32 项）
├── data/                       # 运行时产物：factors/ quarantine/（自动创建）
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml              # ruff 配置
└── requirements.txt
```

---

## 3. 部署

### 3.1 Docker Compose（推荐）

独立部署 data-cleaner + Redis（PostgreSQL 默认复用既有 `quant_db`，也可取消注释内置 PG 段）。

```bash
cd data-cleaner

# 可选：覆盖数据库连接（默认指向 compose 内 postgres）
# export DATABASE_URL=postgresql+asyncpg://quant_user:quant_password@postgres:5432/quant_db

docker compose up -d --build
```

- 服务端口：`8100`（API）
- Redis：`:6379`（健康探针通过后才启动服务）
- 数据持久化：`./data/factors`、`./data/quarantine` 挂载到容器 `/data/*`
- 因子数据目录容器内为 `/data/factors`，失败快照 `/data/quarantine`

健康检查：

```bash
curl http://localhost:8100/api/v1/health
# {"status":"healthy","service":"data-cleaner","time":"..."}
```

### 3.2 本地运行（venv）

```bash
cd data-cleaner
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env        # 按需编辑
uvicorn app.main:app --host 0.0.0.0 --port 8100
# 开发热重载（务必显式带 --port 8100，否则回退默认 8000 会与 backend 冲突）
uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload
```

> ⚠️ **端口冲突警告**：data-cleaner **必须**使用 `:8100`，**严禁占用 `:8000`**。`:8000` 是主后端（`backend/`）的专用端口，二者同机部署时若 data-cleaner 落到 8000，会挤掉 backend 导致 `/api/v1/user/info` 等接口全部 404。即使使用 `.env` 中 `PORT=8100`，也请始终在 `uvicorn` 命令里显式写 `--port 8100`（`.env` 的 `PORT` 仅在某些启动方式下生效）。

> 注意：当前因子实现均为纯 pandas/numpy，`requirements.txt` 默认不含 `TA-Lib`。若未来需接入 TA-Lib，需在镜像内安装其 C 库（`apt-get install -y libta-lib-dev` 或源码编译）后再取消 `requirements.txt` 中注释行。

### 3.3 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_URL` | `postgresql+asyncpg://quant_user:quant_password@localhost:5432/quant_db` | 因子库 PG（asyncpg 驱动） |
| `REDIS_URL` | `redis://localhost:6379/0` | 热因子缓存；不可用时自动降级 no-op |
| `FACTOR_DATA_DIR` | `./data/factors` | 因子 Parquet 目录（自动创建） |
| `QUARANTINE_DIR` | `./data/quarantine` | 失败输入快照目录 |
| `TUSHARE_TOKEN` | 空 | 财务数据源凭证；空则价值/成长因子财务字段返回 NaN |
| `FUNDAMENTAL_PROVIDER` | `tushare` | `tushare` / `akshare` |
| `LLM_API_KEY` | 空 | AI 因子生成 LLM 凭证；空则走内置规则引擎 |
| `DEBUG` | `true` | 生产置 `false` |
| `HOST` / `PORT` | `0.0.0.0` / `8100` | 监听地址 |
| `TZ` | `Asia/Shanghai` | 时区 |
| `API_KEYS` | 空 | 允许访问的 API Key 列表（逗号分隔）。**为空则接口开放**，供主后端网关（`backend/:8000`）探测连通性。配置后受保护接口需带 `X-API-Key` 头。 |

完整模板见 `.env.example`。

### 3.4 被主后端网关纳管（多实例）

data-cleaner 可作为**多个实例**被主后端（`:8000`）统一纳管，由 backend 的 `/api/v1/cleaner/*` 网关做注册、QoS 轮询、因子同步与聚合（详见 `docs/plans/2026-08-26.multi-cleaner-gateway.md` 与根 `README.md` §2.6）。

网关依赖的本服务契约：

- `GET /api/v1/health`：无需 Key，返回 `{"status":"healthy","service":"data-cleaner",...}`
- `GET /api/v1/qos`：无需 Key，返回 `{status, factor_count, cpu/mem, version}`
- `GET /api/v1/factor`：受保护（配置 `API_KEYS` 后需 `X-API-Key`），返回因子列表供网关 `sync` 入库

> 因此本服务的 `:8100` 仅供 backend 网关访问即可；前端不直接连本服务。

---

## 4. API 速览

基址 `/api/v1`。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/factor/` | 因子列表（可按 `?category=` 过滤） |
| GET | `/factor/{code}` | 因子定义 |
| POST | `/factor/` | 注册自定义因子（formula 沙箱校验） |
| PUT | `/factor/{code}` | 更新因子 |
| DELETE | `/factor/{code}` | 删除因子 |
| POST | `/factor/ai-generate` | 自然语言生成因子（返回沙箱校验通过的 formula） |
| POST | `/factor/evaluate` | 单因子效能评估（IC/IR/Sharpe/回撤/胜率） |
| POST | `/factor/batch-evaluate` | 批量因子效能评估（多个因子 + 整体汇总） |
| POST | `/factor/combination` | 多因子等权组合 |
| POST | `/factor/backtest` | 因子组合回测 |
| GET | `/analytics/quality` | 数据质量报告（缺失/异常/范围） |
| GET | `/data/symbols` | 已落地品种列表 |
| POST | `/pipeline/run` | 手动触发清洗→因子流水线 |
| GET | `/pipeline/last-report` | 最近一次运行报告 |
| GET | `/metrics` | Prometheus 风格指标（实际路径 `/api/v1/metrics`） |

### 示例：运行一次流水线

```bash
curl -X POST http://localhost:8100/api/v1/pipeline/run \
  -H 'Content-Type: application/json' \
  -d '{"source":"csv","start":"2023-01-01","end":"2023-12-31",
       "freq":"1d","csvPath":"tests/fixtures/big_bars.csv"}'
```

### 示例：AI 生成因子

```bash
curl -X POST http://localhost:8100/api/v1/factor/ai-generate \
  -H 'Content-Type: application/json' \
  -d '{"description":"过去20天的动量因子"}'
# {"code":"AI_MOMENTUM_20","formula":"adj_close / delay(adj_close, 20) - 1",
#  "source":"rule","saved":true,...}
```

---

## 5. 调度与可观测

- **调度**：服务启动时 `APScheduler` 注册三任务（日级因子、周六效能、30s 心跳）。日志含 `task` 字段便于追踪。
- **指标**：`GET /api/v1/metrics`（注意含 v1 前缀）暴露 `pipeline_runs_total`、`pipeline_runs_failed_total`、`pipeline_duration_seconds`、`factors_registered` 等，可直接被 Prometheus 抓取、Grafana 展示。
- **失败快照**：流水线异常时输入数据落入 `data/quarantine/`（parquet 或 `.empty` 标记），便于复现排查。
- **结构校验**：清洗输出经 `pandera` 校验（列类型/范围/`high>=low`），缺失 pandera 时降级为手工校验。

---

## 6. 测试

```bash
pytest tests/ -q
# 32 passed（因子计算/清洗/校验/API/缓存降级/失败隔离/批量评估）
```

代码规范：`ruff check app/`（配置见 `pyproject.toml`，零错误）。

---

## 7. 故障排查

| 现象 | 排查 |
|------|------|
| 启动报 `Error loading ASGI app` | 确认从 `data-cleaner/` 目录运行，模块为 `app.main:app` |
| 端口 8100 被占用 | `netstat -ano | findstr :8100` 后 `taskkill /F /PID <pid>` |
| 因子财务字段全 NaN | 未配置 `TUSHARE_TOKEN`/无网络，属预期降级；价值/成长代理因子仍可用 |
| Redis 连接失败 | 自动降级为 no-op，日志含 `Redis 不可用` 警告，不影响主流程 |
| `factors_registered` 为 0 | 因子未注册，检查 `app/factors/registry.py` 导入 |
| 流水线 502 | 数据源返回空或接入异常，检查 `data/quarantine/` 快照 |

---

## 8. 设计参考

完整方案见 `docs/plans/data-cleaning-service-design.md`（数据基础设施策划、因子分类、安全规范 §10、Docker 部署 §9）。
