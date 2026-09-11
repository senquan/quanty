# 数据基础设施：数据清洗服务实现方案

> 版本: v1.1
> 创建日期: 2026-08-24
> 更新日期: 2026-08-31（v1.1：更正服务边界与部署形态，见 §1.4、§2.2、§12）
> 目标读者: 实施 subagent（后端开发 / 运维 / 量化研究员）
> 关联文档: `2026-06-11.factor-api.md`（因子库 API 需求）
>           `2026-08-31.rebalance-orchestration-migration.md`（调仓编排迁出至 backend）

---

## 1. 背景与目标

### 1.1 项目现状

本项目（lab.Quant）为量化交易系统，现有技术栈：

- **后端**: FastAPI 0.121 + SQLAlchemy 2.0 (async) + PostgreSQL 15
- **数据处理**: pandas 2.2 + numpy 2.4 + scipy + TA-Lib
- **数据源**: yfinance、ccxt（加密货币）、华泰模拟交易接口
- **部署**: Docker + docker-compose（已含 postgres / backend 服务）
- **已有基础**: `backend/app/services/technical_indicators.py` 已实现 15+ 技术指标（SMA/EMA/RSI/MACD/布林带/随机指标/ATR 等）

### 1.2 服务目标

构建**独立部署的数据清洗服务（data-cleaner）**，职责：

1. 接入多源原始行情数据（日线 / 分钟线 / 财务数据）
2. 执行标准化清洗流水线（去重、缺失值处理、异常值检测、复权对齐、时间对齐）
3. 基于清洗后数据**批量生成多类别因子数据**（动量 / 波动率 / 价值 / 成长 / 情绪 / 技术）
4. 提供 REST API 供主后端与前端因子库消费（对接 `2026-06-11.factor-api.md`）
5. **发信号**：基于因子值计算目标持仓（`POST /strategy/scores`），供 backend 调仓使用
6. **行情中继**：为 backend 提供批量最新收盘价（`POST /raw/latest-prices`）
7. 支持 Docker 一键部署，可独立扩展、独立迭代

> 第 5、6 项为 v1.1 补充：backend 与 data-cleaner 分库后，backend 无法直连 `factor` 库，
> "买什么"（目标持仓）与"多少钱"（最新价）只能经本服务的只读接口获取。

### 1.3 非目标（Out of Scope）

- 实时流式计算（本期只做 T+1 批处理，实时化留待 Phase 3）
- 因子回测引擎（由主后端 `backtest_engine.py` 负责）
- **交易执行与调仓编排**（由 backend 交易中心负责；本服务不持有持仓、账户，也不下单）

### 1.4 服务边界（与 backend 的分工）

> 2026-08-31 明确。此前调仓编排与下单曾落在 data-cleaner 内（职责越界），
> 现已迁回 backend，详见 `2026-08-31.rebalance-orchestration-migration.md`。

| 服务 | 定位 | 职责 | 不做 |
|---|---|---|---|
| **data-cleaner**（本服务） | 无状态服务，可独立多实例部署 | 数据清洗 / 因子计算 / 回测 / 发信号 / 行情中继 | **不碰交易**：不编排调仓、不下单、不持有持仓与账户 |
| **backend** | 控制中心 + 交易中心 | 策略编排 / 持仓与现金 / 风控 / 下单 / 调仓记录 / 定时调度 | — |

关键约束：**backend 与 data-cleaner 分属独立 Postgres 实例**，二者只能通过 HTTP 接口交互。

---

## 2. 总体架构

### 2.1 架构图

```
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│  yfinance    │   │    ccxt      │   │  CSV/本地上传 │
│  (股票/ETF)  │   │  (加密货币)   │   │   (自定义)    │
└──────┬───────┘   └──────┬───────┘   └──────┬───────┘
       │                  │                  │
       └──────────────────┼──────────────────┘
                          ▼
              ┌───────────────────────┐
              │   数据接入层 Ingestion │  ← 统一拉取/接收原始数据
              └───────────┬───────────┘
                          ▼
              ┌───────────────────────┐
              │   清洗流水线 Pipeline  │  ← 6 步标准化处理（见 §4）
              │  (dedup→fill→outlier  │
              │   →adjust→align→valid)│
              └───────────┬───────────┘
                          ▼
              ┌───────────────────────┐
              │   因子工厂 FactorLib   │  ← 6 大类因子计算（见 §5）
              └───────────┬───────────┘
                          ▼
       ┌──────────────────┼──────────────────┐
       ▼                  ▼                  ▼
┌─────────────┐   ┌──────────────┐   ┌──────────────┐
│ PostgreSQL  │   │  Parquet文件  │   │  Redis缓存    │
│ (因子元数据) │   │ (因子值矩阵)  │   │ (热数据加速)  │
└─────────────┘   └──────────────┘   └──────────────┘
                          ▲
              ┌───────────┴───────────┐
              │   FastAPI 服务层       │  ← REST API（见 §7）
              └───────────────────────┘
                          ▲
              ┌───────────┴───────────┐
              │  主后端 / 前端因子库   │
              └───────────────────────┘
```

### 2.2 部署形态

新增独立服务 `data-cleaner`，与现有 `backend` 平级：

> ⚠️ **v1.1 更正**：原方案假设"通过共享的 PostgreSQL 与 Redis 交换数据"，现明确为
> **各自独立的 Postgres 实例**，两服务只能通过 HTTP 接口交互
> （data-cleaner 持有 `factor` schema，backend 持有业务表与交易表）。
> data-cleaner 为**无状态**服务，可水平扩展多实例；
> backend 为控制中心，其定时调度器只在单一实例启用（`ENABLE_TRADING_SCHEDULER`）。

```
lab.Quant/
├── backend/                  # 主后端：控制中心 + 交易中心（含调仓编排与定时调度）
├── data-cleaner/             # ★ 新增：数据清洗服务（本方案主体）
│   ├── app/
│   │   ├── main.py
│   │   ├── api/v1/           # REST 路由
│   │   ├── ingestion/        # 数据接入
│   │   ├── pipeline/         # 清洗流水线
│   │   ├── factors/          # 因子库
│   │   ├── storage/          # 存储层
│   │   ├── core/             # 配置/日志/异常
│   │   └── tasks/            # 定时任务
│   ├── tests/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── .env.example
└── docker-compose.yml        # 编排文件追加 data-cleaner 服务
```

---

## 3. 技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 服务框架 | FastAPI 0.121 | 与主后端一致，团队零学习成本 |
| 数据计算 | pandas + numpy + TA-Lib | 已在 requirements.txt 中，复用现有代码 |
| 任务调度 | APScheduler 3.x | 轻量、进程内调度，无需引入 Celery（Phase 1） |
| 缓存 | Redis 7 | 缓存热因子数据，降低 DB 压力 |
| 时序存储 | Parquet + PostgreSQL | 因子值矩阵（宽表）存 Parquet；元数据/索引存 PG |
| 数据校验 | pandera | DataFrame schema 校验，比手写 assert 更声明式 |
| 数据库 | PostgreSQL 15（**独立实例**，非复用 backend 库） | 与 backend 分库部署；库内用独立 schema `factor` 组织 |
| 部署 | Docker + docker-compose | 与现有部署方式一致 |

---

## 4. 数据清洗流水线设计

### 4.1 输入数据规范

统一原始数据为 `RawBar` 结构（OHLCV）：

```python
# data-cleaner/app/ingestion/schemas.py
from datetime import datetime
from pydantic import BaseModel

class RawBar(BaseModel):
    symbol: str        # 标的代码，如 "AAPL" / "BTCUSDT" / "600519.SH"
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str        # 数据来源: yfinance / ccxt / csv
    freq: str = "1d"   # 频率: 1d / 1h / 1m
```

### 4.2 六步清洗流水线

每步实现为独立 `Transformer` 类，可插拔、可单测：

```python
# data-cleaner/app/pipeline/base.py
from abc import ABC, abstractmethod
import pandas as pd

class Transformer(ABC):
    name: str
    @abstractmethod
    def transform(self, df: pd.DataFrame) -> pd.DataFrame: ...
```

| 步骤 | Transformer | 处理逻辑 |
|------|-------------|----------|
| 1 | `DeduplicateTransformer` | 按 `(symbol, timestamp, freq)` 去重，保留最新 source 记录 |
| 2 | `MissingValueTransformer` | OHLC 缺失：前值填充（limit=3）；volume 缺失：填 0 并打标记列 `volume_imputed`；连续缺失 >5 根则丢弃该 symbol 该段 |
| 3 | `OutlierTransformer` | 基于滚动 Z-Score（window=20, threshold=5）检测价格跳变；异常值替换为滚动中位数，打标记列 `price_outlier_fixed` |
| 4 | `AdjustTransformer` | 复权处理：加载分红拆股事件，生成前复权 `adj_close` 等列；无事件数据时 `adj_*` = 原始值 |
| 5 | `TimeAlignTransformer` | 按交易日历对齐（美股用 NYSE、A股用上交所、加密用 7×24）；补齐缺失交易时段为 NaN 行（供下游识别） |
| 6 | `ValidateTransformer` | 用 pandera 校验输出 schema：`high >= low`、`volume >= 0`、时间单调递增；不通过则抛 `PipelineValidationError` 并写入失败日志表 |

### 4.3 流水线编排

```python
# data-cleaner/app/pipeline/runner.py
class CleaningPipeline:
    def __init__(self, transformers: list[Transformer]):
        self.transformers = transformers

    def run(self, df: pd.DataFrame) -> pd.DataFrame:
        for t in self.transformers:
            df = t.transform(df)
            # 每步记录行数变化到清洗报告（见 §8 可观测性）
        return df
```

---

## 5. 因子库设计

### 5.1 因子分类与清单

因子代码统一命名规范：`{类别前缀}_{指标}_{窗口}`，如 `MOM_RET_20`。

| 类别 | 前缀 | 因子清单（Phase 1 必做） |
|------|------|--------------------------|
| 动量 | `MOM_` | RET_5 / RET_20 / RET_60（N日收益率）、MOM_ACCEL（动量加速度）、REL_STR_20（相对基准强度） |
| 波动率 | `VOL_` | STD_20（收益率标准差）、ATR_14、PARKINSON_20（高低价波动率）、SKEW_60（收益偏度） |
| 技术 | `TECH_` | RSI_14、MACD_DIF / MACD_DEA / MACD_HIST、BB_POS（布林带位置）、KDJ_K / KDJ_D、MA_BIAS_20（均线乖离率） |
| 价值 | `VAL_` | PE_TTM、PB、PS_TTM、DIV_YIELD（股息率）——依赖财务数据源，Phase 2 |
| 成长 | `GRO_` | REV_GROWTH_YOY（营收同比）、EPS_GROWTH_YOY——Phase 2 |
| 情绪 | `SENT_` | VOL_RATIO_5（量比）、TURNOVER_20（换手率）、AMOUNT_RANK（成交额市场分位） |

### 5.2 因子注册机制

所有因子实现统一接口并自动注册，新增因子只需添加一个文件：

```python
# data-cleaner/app/factors/base.py
from abc import ABC, abstractmethod
import pandas as pd

class Factor(ABC):
    code: str          # 因子代码，如 "MOM_RET_20"
    name: str          # 中文名
    category: str      # momentum / volatility / value / growth / sentiment / technical
    frequency: str     # Daily / Weekly / Monthly
    data_sources: list[str]  # 依赖字段，如 ["close"]

    @abstractmethod
    def compute(self, df: pd.DataFrame) -> pd.Series: ...

# data-cleaner/app/factors/registry.py
_REGISTRY: dict[str, Factor] = {}

def register(cls):
    _REGISTRY[cls.code] = cls()
    return cls

def get_factor(code: str) -> Factor: ...
def list_factors(category: str | None = None) -> list[Factor]: ...
```

示例实现：

```python
# data-cleaner/app/factors/momentum.py
from app.factors.registry import register
from app.factors.base import Factor

@register
class MomentumReturn20(Factor):
    code = "MOM_RET_20"
    name = "20日动量"
    category = "momentum"
    frequency = "Daily"
    data_sources = ["adj_close"]

    def compute(self, df):
        return df["adj_close"].pct_change(20)
```

### 5.3 因子效能评估

每个因子计算后自动生成评估指标（对接前端 Factor 类型）：

```python
# data-cleaner/app/factors/evaluator.py
class FactorEvaluator:
    """计算 IC / IR / 分层收益等效能指标"""
    def evaluate(self, factor_values: pd.Series, forward_returns: pd.Series) -> dict:
        # ic_mean, ic_std, ir = ic_mean/ic_std
        # 分层回测: 按因子值 10 分位分组，longReturns/shortReturns 累计净值
        # sharpe_ratio, max_drawdown, win_rate
        ...
```

---

## 6. 存储设计

### 6.1 PostgreSQL（schema: `factor`）

```sql
-- 因子元数据表
CREATE TABLE factor.definitions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code        VARCHAR(50) UNIQUE NOT NULL,   -- MOM_RET_20
    name        VARCHAR(200) NOT NULL,
    category    VARCHAR(20) NOT NULL,
    frequency   VARCHAR(20) NOT NULL,
    formula     TEXT,
    data_sources JSONB,
    author      VARCHAR(20) DEFAULT 'system',
    status      VARCHAR(20) DEFAULT 'active',
    created_at  TIMESTAMPTZ DEFAULT now()
);

-- 因子效能指标表（每日刷新）
CREATE TABLE factor.metrics (
    factor_code VARCHAR(50) REFERENCES factor.definitions(code),
    as_of_date  DATE NOT NULL,
    ic_mean     DOUBLE PRECISION,
    ic_std      DOUBLE PRECISION,
    ir          DOUBLE PRECISION,
    sharpe_ratio DOUBLE PRECISION,
    max_drawdown DOUBLE PRECISION,
    win_rate    DOUBLE PRECISION,
    PRIMARY KEY (factor_code, as_of_date)
);

-- 清洗任务执行日志
CREATE TABLE factor.pipeline_runs (
    id          BIGSERIAL PRIMARY KEY,
    started_at  TIMESTAMPTZ DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status      VARCHAR(20),     -- success / failed
    rows_in     INTEGER,
    rows_out    INTEGER,
    report      JSONB            -- 每步 transformer 的行数/修复数统计
);
```

### 6.2 因子值矩阵（Parquet）

因子值数据量大、列式查询友好，存 Parquet 文件，按 `category/日期` 分区：

```
data/factors/
├── momentum/
│   ├── 2026-08-24.parquet    # 行=symbol, 列=[MOM_RET_20, MOM_RET_60, ...]
├── volatility/
│   └── 2026-08-24.parquet
```

挂载卷 `./data/factors:/data/factors` 持久化。元数据中记录文件路径。

### 6.3 Redis 缓存

- Key 规范：`factor:latest:{code}` → 最新一期因子值（JSON）
- `factor:status` → 流水线最近运行状态
- TTL: 24h，流水线完成后主动刷新

---

## 7. API 设计（对接 factor-api 需求）

服务端口 `8100`，路由前缀 `/api/v1`。完整实现 `2026-06-11.factor-api.md` 的 P0/P1 接口：

### 7.1 因子 / 数据 / 流水线接口

| Method | Path | 说明 | 优先级 |
|--------|------|------|--------|
| GET | `/api/v1/factor/` | 因子列表（支持 category/author/frequency/search 过滤） | P0 |
| GET | `/api/v1/factor/{id}` | 因子详情（含效能指标与序列） | P0 |
| POST | `/api/v1/factor/` | 创建自定义因子（formula 表达式 → 解析计算 → 存储） | P0 |
| PUT | `/api/v1/factor/{id}` | 更新因子 | P0 |
| DELETE | `/api/v1/factor/{id}` | 删除因子（仅 author=user） | P0 |
| POST | `/api/v1/factor/correlation` | 因子相关性矩阵 | P1 |
| POST | `/api/v1/factor/backtest` | 多因子组合回测 | P1 |
| GET | `/api/v1/data/bars` | 查询清洗后行情（symbol/start/end/freq） | P0（新增） |
| POST | `/api/v1/pipeline/run` | 手动触发清洗+因子计算任务 | P0（新增） |
| GET | `/api/v1/pipeline/status` | 查询最近流水线运行状态与报告 | P0（新增） |

### 7.2 策略与行情中继接口（v1.1 补充）

除上表外，本服务还承载策略与行情中继接口：

| Method | Path | 说明 |
|--------|------|------|
| GET | `/api/v1/strategy/strategies` | 策略列表 |
| POST / GET / PUT / DELETE | `/api/v1/strategy/strategies/{id}` | 策略 CRUD |
| POST | `/api/v1/strategy/strategies/{id}/backtest` | 回测 |
| GET | `/api/v1/strategy/strategies/{id}/backtests` | 回测历史 |
| **POST** | `/api/v1/strategy/scores` | **发信号**：任意配置算目标持仓（纯计算，无副作用） |
| GET | `/api/v1/strategy/strategies/{id}/executions` | 历史调仓记录（仅供对账，已停止写入） |
| GET | `/api/v1/raw/universe` | 全 A 股代码池 |
| GET | `/api/v1/raw/{symbol}` | 单标的区间历史 |
| **POST** | `/api/v1/raw/latest-prices` | **行情中继**：批量取最新收盘价（供 backend 调仓取价） |
| POST | `/api/v1/raw/backfill` | 触发增量 / 全量回填 |

> `POST /strategy/scores` 与 `POST /raw/latest-prices` 是 backend 调仓链路的两个依赖点：
> 前者回答"买什么"，后者回答"多少钱"。二者均为**只读计算，不产生交易副作用**。
>
> 原 `POST /api/v1/strategy/strategies/{id}/rebalance`（手动调仓）已随编排迁出而删除，
> 由 backend 的 `POST /api/v1/trading/rebalances/trigger` 替代。

自定义因子 formula 表达式用受限语法解析（白名单函数：`close/open/high/low/volume/delay/ref/ma/std/max/min/rank/ts_mean/ts_std` 等），**禁止 eval 任意代码**，实现见 §10 安全要求。

---

## 8. 定时任务与可观测性

### 8.1 调度任务（APScheduler）

```python
# data-cleaner/app/tasks/scheduler.py
# - 每个交易日 18:00: 拉取日线 → 清洗 → 计算全部 Daily 因子 → 写存储 → 刷新缓存
# - 每周六 09:00: 重算因子效能指标（需更长回看窗口）
# - 每小时: 心跳写入 Redis factor:status
#
# v1.1：策略调仓扫描任务（原交易日 9-15 点每 15 分钟）已迁出至 backend，
#       本服务不再驱动任何交易行为，保持无状态。
```

### 8.2 日志与监控

- 结构化日志（JSON），统一字段：`ts/level/service/task/symbol_count/duration_ms`
- 每次流水线运行写 `factor.pipeline_runs` 表，含每步行数与修复统计
- 失败时保留输入快照到 `data/quarantine/` 便于排查

---

## 9. Docker 部署

### 9.1 `data-cleaner/Dockerfile`

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# TA-Lib 需要 C 库
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential wget \
 && wget http://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz \
 && tar -xzf ta-lib-0.4.0-src.tar.gz && cd ta-lib \
 && ./configure --prefix=/usr && make && make install \
 && cd .. && rm -rf ta-lib ta-lib-0.4.0-src.tar.gz \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
EXPOSE 8100
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
```

### 9.2 `docker-compose.yml` 追加服务

> **v1.1：data-cleaner 使用独立的 Postgres 实例**（与 backend 分库），
> 此处新增 `postgres-factor` 服务。`data-cleaner` 无状态，可 `--scale` 多实例；
> `backend` 为控制中心，其调度器只在单一实例启用，**不可用 `--scale` 直接扩容**
> （除非保证只有一个实例设置 `ENABLE_TRADING_SCHEDULER=true`）。

```yaml
  postgres:                 # backend 库：业务表 + 交易表
    image: postgres:15
    container_name: quant-postgres
    environment:
      POSTGRES_DB: quant_db
      POSTGRES_USER: quant_user
      POSTGRES_PASSWORD: quant_password
    ports: ["5432:5432"]
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./backend/init:/docker-entrypoint-initdb.d
    networks: [quant-network]

  postgres-factor:          # data-cleaner 库：factor schema（与 backend 分库）
    image: postgres:15
    container_name: quant-postgres-factor
    environment:
      POSTGRES_DB: factor_db
      POSTGRES_USER: quant_user
      POSTGRES_PASSWORD: quant_password
    ports: ["5433:5432"]
    volumes:
      - postgres_factor_data:/var/lib/postgresql/data
    networks: [quant-network]

  redis:
    image: redis:7-alpine
    container_name: quant-redis
    ports: ["6379:6379"]
    networks: [quant-network]

  data-cleaner:
    build: ./data-cleaner
    container_name: quant-data-cleaner
    environment:
      # 注意：指向独立的 factor 库，不是 backend 的 quant_db
      DATABASE_URL: postgresql+asyncpg://quant_user:quant_password@postgres-factor:5432/factor_db
      REDIS_URL: redis://redis:6379/0
      FACTOR_DATA_DIR: /data/factors
      TZ: Asia/Shanghai
    ports: ["8100:8100"]
    volumes:
      - ./data/factors:/data/factors
    depends_on: [postgres-factor, redis]
    networks: [quant-network]
```

顶层 `volumes` 需同步追加 `postgres_factor_data:`。

扩容示例（仅 data-cleaner 可水平扩展）：

```bash
docker compose up -d --scale data-cleaner=3
```

### 9.3 `data-cleaner/requirements.txt`

```
fastapi==0.121.1
uvicorn[standard]==0.25.0
sqlalchemy==2.0.23
asyncpg==0.29.0
pydantic==2.7.0
pydantic-settings==2.2.1
pandas==2.2.2
numpy==2.4.2
scipy==1.17.0
TA-Lib>=0.6.8
yfinance==0.2.36
ccxt==4.2.25
pandera>=0.20
redis>=5.0
apscheduler>=3.10
pyarrow>=16.0
python-dotenv==1.0.0
```

---

## 10. 安全与工程规范

1. **formula 沙箱**: 自定义因子表达式只允许白名单 AST 节点（BinOp/Call/Name/Constant + 白名单函数），解析后编译执行；禁止 `import`、`__`、属性访问
2. **配置管理**: 所有密钥走 `.env`（参考 `backend/.env.example` 模式），严禁入库/入仓
3. **类型与校验**: 所有 API 出入参用 Pydantic 模型；DataFrame 用 pandera schema 校验
4. **测试要求**: 每个 Transformer / Factor 必须有 pytest 单测；流水线有端到端集成测试（用 fixtures/sample_bars.csv）
5. **代码风格**: 与主后端保持一致（ruff/black），提交前跑 `ruff check`

---

## 11. 实施步骤（任务分解）

> 按依赖顺序执行；每步完成后须通过对应验收标准再进入下一步。

### Phase 1 — 核心可用（第 1~5 步）

| 步骤 | 任务 | 产出物 | 验收标准 |
|------|------|--------|----------|
| 1 | 项目脚手架 | `data-cleaner/` 目录结构、FastAPI 入口、配置模块、日志、健康检查 `/health` | `uvicorn app.main:app` 启动，访问 `/health` 返回 200 |
| 2 | 数据接入层 | `ingestion/`：YFinanceSource、CcxtSource、CsvSource 三个适配器 + RawBar 模型 | 单元测试 mock 数据源通过；能拉取 AAPL 近 1 年日线 |
| 3 | 清洗流水线 | `pipeline/`：6 个 Transformer + CleaningPipeline 编排器 | 对含脏数据（重复/缺失/异常）的 fixture 运行，输出通过 ValidateTransformer；行数报告正确 |
| 4 | 因子库（动量/波动/技术/情绪） | `factors/`：base + registry + 4 类共 15+ 因子实现 | 每个因子单测通过；`list_factors()` 返回注册表 |
| 5 | 存储层 + 基础 API | PG 建表 SQL、Parquet 读写、`GET /factor/`、`GET /data/bars`、`POST /pipeline/run` | API 冒烟测试通过；跑完一次流水线后能查到因子数据 |

### Phase 2 — 完整对接（第 6~8 步）

| 步骤 | 任务 | 产出物 | 验收标准 |
|------|------|--------|----------|
| 6 | 因子效能评估 + CRUD | FactorEvaluator、factor-api P0 全部接口 | 创建自定义因子后返回 icMean/ir/sharpeRatio 等指标；与前端 Factor 类型字段一致 |
| 7 | Docker 化 | Dockerfile、docker-compose 追加、Redis 缓存接入 | `docker compose up -d` 一键启动 postgres+redis+data-cleaner；流水线在容器内跑通 |
| 8 | 定时调度 + 相关性/组合回测 | APScheduler 任务、P1 接口（correlation/backtest） | 到点自动执行并写 pipeline_runs；correlation 矩阵接口返回对称矩阵 |

### Phase 3 — 增强（后续迭代）

| 步骤 | 任务 | 说明 |
|------|------|------|
| 9 | 价值/成长因子 | 接入财务数据源（tushare/akshare），实现 VAL_/GRO_ 因子 |
| 10 | AI 因子生成 | 对接 factor-api P2 的 ai-generate 接口 |
| 11 | 分钟级与实时化 | 评估引入 Celery + 流式处理 |

---

## 12. 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| TA-Lib C 库在 Windows/容器编译失败 | 阻塞 Docker 构建 | Dockerfile 中固定源码编译步骤；本地开发可用 `ta-lib` 预编译 wheel |
| 数据源限流（yfinance/ccxt） | 拉取失败 | 指数退避重试；多源兜底；拉取结果落地后再进流水线 |
| 因子计算慢（全市场×多因子） | 流水线超时 | pandas 向量化优先；必要时按 symbol 分片并行（Phase 3 引入 multiprocessing） |
| formula 注入 | 安全事故 | AST 白名单沙箱 + 单测覆盖恶意输入用例 |
| ~~与主后端共用 DB 造成耦合~~ | 已消除 | v1.1：两服务**分属独立 PG 实例**，仅经 HTTP 交互，不再有库级耦合 |
| backend 无法直连 `factor` 库取价 | 调仓算不出股数 | 由本服务提供 `POST /raw/latest-prices` 行情中继（批量接口，避免 N 次往返）；取价失败时 backend 记为调仓失败而非盲目下单 |
| data-cleaner 被写入交易职责 | 职责越界、有状态 | 已在 v1.1 纠正：调仓编排与下单归 backend；新增接口须保持只读、无副作用 |

---

## 13. 交付物清单（供 subagent 核对）

- [ ] `data-cleaner/` 完整服务代码（含 tests/）
- [ ] `data-cleaner/Dockerfile`、`requirements.txt`、`.env.example`
- [ ] `docker-compose.yml` 追加 redis + **独立 factor 库（`postgres-factor`）** + data-cleaner 服务
- [ ] PG 建表迁移脚本（`factor` schema 三张表，**在独立 factor 库执行**，非 backend 库）
- [ ] API 文档（FastAPI 自动生成，路径 `/api/docs`）
- [ ] 15+ 因子实现及单测
- [ ] 端到端测试报告（fixture → 流水线 → API 查询）
- [ ] **分库连通性验证**：backend 经 HTTP 调用 `/strategy/scores` 与 `/raw/latest-prices` 成功，
      且不存在任何 backend 直连 `factor` 库的代码路径
