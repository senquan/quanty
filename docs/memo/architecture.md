# lab.Quant(Quanty)系统架构备忘

> 用途:长期架构记忆。定位关键模块、数据流、存储与调度,便于后续开发与排障。
> 最后整理:2026-09-04

---

## 1. 总览

A 股量化平台,**前端 → 主后端(网关/交易中心)→ 因子引擎(dc)** 三层微服务,共享 PostgreSQL(两库隔离)+ Redis,行情/因子数据落 Parquet。

```
┌─────────────────────────────────────────────────────────────┐
│ 前端 Vue3 + Vben Admin(web-ele :5777,vite 代理 /api→:8000)  │
└───────────────────────────┬─────────────────────────────────┘
                            │ /api/v1/* (JWT)
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ backend 主后端 :8000   FastAPI + async SQLAlchemy(asyncpg)  │
│  DB: quant_db (:5432)  Redis (:6379)                        │
│  职责:认证/RBAC、旧版回测、模拟/实盘交易、调仓 rebalance、    │
│        多清洗服务网关(cleaner/factor_library/factor_strategy)│
└───────────────┬─────────────────────────────────────────────┘
                │ HTTP(httpx,+X-API-Key)
                ▼
┌─────────────────────────────────────────────────────────────┐
│ data-cleaner 因子引擎 :8100   FastAPI + APScheduler + Redis  │
│  DB: factor_db (:5433,schema `factor`)  Parquet 因子库        │
│  职责:数据接入→清洗→因子计算/评估→因子选股/回测引擎           │
└─────────────────────────────────────────────────────────────┘
```

- **端口约定**:`:8000` 仅 backend,`:8100` 仅 data-cleaner(严禁互占),前端 `:5777`。
- **交互原则**:backend 与 data-cleaner **仅通过 HTTP** 交互;两库(`quant_db`/`factor_db`)隔离。
- `dataserver/` 目前为**空占位目录**,无角色。

---

## 2. backend —— 主后端 / 交易中心(`:8000`)

- 入口 `backend/main.py`(lifespan 启停交易调度器);配置 `app/core/config.py`。
- 路由(`app/api/api_v1/api.py`,统一 `/api/v1`):
  - `auth` / `user` / `role` / `menu` / `role_permission`:认证与 RBAC。
  - `quant`:旧版 buy/sell 脚本回测引擎(`app/services/backtest_engine.py`、`technical_indicators.py`、`performance_analyzer.py`,yfinance/ccxt + TA 指标)。
  - `trading`:模拟/实盘账户、持仓、订单(`endpoints/trading.py`)。
  - `cleaner`:**多清洗服务网关**——注册/健康/QoS/同步多个 data-cleaner 实例(`endpoints/cleaner.py` + `services/cleaner_gateway.py`)。
  - `factor_library`:聚合因子底册,源自本地 `factor_registry` 表(`endpoints/factor_library.py`)。
  - `factor_strategy`:因子选股策略 CRUD(元数据本地),**计算转发给 dc**(`endpoints/factor_strategy.py` + `services/factor_strategy_proxy.py`)。
  - `watchlist`:自选。
- 关键服务:
  - `services/factor_strategy_proxy.py`:httpx 转发策略/scores/backtest 到已注册的 dc(取 `cleaner_services` 行,带 `X-API-Key`)。
  - `services/rebalance_service.py`:**调仓编排**——取活跃策略 → dc `POST /strategy/scores`(目标持仓)→ dc `/raw/latest-prices`(行情中继)→ 按现金 95%、100 股整手下单 → `trading_coordinator` 下单 → 幂等写调仓记录。
  - `services/broker/`:券商抽象,`simulated.py`(paper)与 `mx.py`(东财妙想,dry-run 开关)。
- 调度(`app/tasks/scheduler.py`,环境开关门控):策略调仓扫描(交易日 09:00–15:00 每 15min)、因子底册同步、QoS 轮询(30s)、组合 EOD 估值(交易日 15:30 写 `portfolio_daily_values`)。`ENABLE_TRADING_SCHEDULER` 仅单实例为 true。
- 表(`app/models/`):`cleaner_services`、`factor_registry`(cleaner.py);`trading_accounts/positions/orders/trades/rebalance_records`、`portfolio_daily_values`、`instruments`(trading.py);及 `user/role/menu/role_permission/quant`。

---

## 3. data-cleaner(dc)—— 数据 & 因子引擎(`:8100`)

- 入口 `app/main.py`(lifespan:`apply_migrations()` → `start_scheduler()`);配置 `app/core/config.py`。
- 独立 `factor_db`,schema `factor`;`storage/db.py` 提供双引擎(请求 loop / 后台 loop 经 `run_async` 切换,规避 asyncpg "attached to a different loop")。
- `app/` 结构:`api/`(health/factor/analytics/data/pipeline/metrics/qos/strategy)、`core/`、`ingestion/`、`pipeline/`、`factors/`、`industry/`、`storage/`、`strategy/`、`tasks/`。

### 3.1 数据接入(`ingestion/`)
主源 **alphafeed**(A 股行情),辅以 **tushare / pandadata / yfinance / ccxt / csv / fundamental**(tushare/akshare 财务,缺 token 优雅降级)。

### 3.2 清洗流水线(`pipeline/`)
6 步 Transformer:行情对齐 → 复权 → 去极值/缺失 → 类型校验 → 范围校验 → 结构校验(pandera,缺库降级手工)。逐标的语义(按单标的连续序列)。

### 3.3 复权口径(关键约定)
- `factor.raw_bars`:`close` 为**全局一致前复权 qfq**,以全历史最新 `adj_factor`(`f_latest`)归一(`scale = f/f_latest`,OHLC 同步),含 `adj_factor`、`hfq_close = close * f_latest/f_first`、`amount`(迁移 002/007/008)。
- 价格类因子统一用 `adj_*` 列(时序可比);**同日多价比值**(如 `high/close`、`(high-low)/open`)前复权与不复权等价(缩放因子约去)。

### 3.4 因子系统(`factors/`)
- 内置因子:继承 `Factor`(`factors/base.py`)+ `@register`(`registry.py`),按 `category` 归类,`compute(df)` 内 `groupby("symbol")` 计算;模块:momentum/volatility/technical/sentiment/liquidity/size/fundamental(value/growth)/intraday。
- 自定义公式因子:`factors/formula.py` 沙箱(AST 白名单:delay/ref/ma/std/max/min/rank/ts_*/abs/log/sign/zscore,列:open/high/low/close/volume/adj_*),存 `factor.definitions.formula`。
- **新增因子两条路**:①写 Factor 类 + `@register`(入版本库,推荐);②自定义 formula 入 `factor.definitions`。
- 本次新增内置因子:`HIGH0`=`adj_high/adj_close`、`KLEN`=`(high-low)/open`、`CORR5`=`close.rolling(5).corr(log(volume+1))`(均 technical),`STD5`=`adj_close.pct_change().rolling(5).std()`(volatility)。

### 3.5 因子库构建(`tasks/factor_build.py`)
`factor.raw_bars` → 逐标的清洗 → 合并基础数据(`daily_basic`/`finance_reports`(as-of 防前视)/`stock_info`)→ 计算全部内置+自定义因子 → 按 **category × 日期** 写横截面:
`{FACTOR_DATA_DIR}/{category}/{YYYY-MM-DD}.parquet`,**index=symbol、列=因子代码**;各类别同日 index 一致,可按 symbol 对齐做相关性/回测。支持 `--symbols/--start/--end/--category` 局部或全量重建。

### 3.6 因子选股/回测引擎(`strategy/`)
与 backend 旧版回测**两套独立实现**。配置存 `factor_strategy.config`(JSONB);过滤字段 `exclude_st/min_list_days/exclude_suspended/exclude_limit_up/exclude_limit_down/min_cap`。dc 暴露 `POST /strategy/scores`(目标持仓)、`/raw/latest-prices`(行情中继);**调仓调度在 backend**。

### 3.7 dc 调度(`tasks/scheduler.py`,APScheduler,Asia/Shanghai)
| 任务 | 触发 | 说明 |
|---|---|---|
| 盘后流水线 | 交易日 18:30 | 拉数 → 因子更新 → 效能评估(顺序),max_instances=1+coalesce |
| 覆盖度校验 | 次日 08:30 | 校验最近交易日覆盖度,不足自动增量补齐,补齐后联动刷新因子库+效能 |
| 效能重算 | 周六 09:00 | 用落库因子值重算 IC/IR/Sharpe/回撤/胜率 |
| 心跳 | 每 30s | 写 Redis `factor:status` |
| 行业刷新 | 交易日 18:40 | 行业分类全量拉取+upsert(幂等,供行业中性化/上市天数过滤) |

### 3.8 dc 表(`factor` schema,`migrations/001–010`)
- `definitions`:因子元数据 + 自定义 formula(按 code 去重,`author` 区分 user/system)。
- `metrics`:因子效能(ic_mean/ic_std/ir/sharpe_ratio/max_drawdown/win_rate)。
- `pipeline_runs`:流水线运行日志。
- `raw_bars`:日线 OHLCV + `adj_factor` + `hfq_close` + `amount`。
- 基础数据:`daily_basic`(估值/换手/市值)、`finance_reports`(成长/ROE/负债/EPS)、`stock_info`(行业/上市日期/分红)、财务指标(009)。
- `factor_strategy`:策略配置(004/005)。

### 3.9 dc 存储与可观测
Parquet 分区落地(类别/日期);失败输入快照 `data/quarantine/`;结构化 JSON 日志;`/api/v1/metrics`(Prometheus 风格);Redis 热因子缓存(不可用自动降级)。

---

## 4. 前端(`frontend/`)

Vue3 + Vben Admin + Element Plus;量化模块 `views/quant/*`(策略管理/编辑器/回测/结果图表 BacktestResults.vue)、因子底册库(聚合因子/清洗服务 Tab)。开发经 vite 代理到 backend `:8000`,**不直连** data-cleaner。

---

## 5. 端到端数据流

```
行情/财务源(alphafeed/tushare/…)
  → dc.ingestion 拉取(qfq 归一化)→ factor.raw_bars
  → dc.pipeline 6 步清洗
  → dc.factors 计算(内置+自定义)→ Parquet 横截面 + factor.metrics
  → backend.factor_library 聚合(经 cleaner 网关同步 dc 因子口径)
  → 用户配置 factor_strategy → backend 转发 dc /strategy/scores
  → backend.rebalance_service 调仓 → broker(simulated/mx)下单
  → 前端展示(因子底册/策略/回测/持仓)
```

---

## 6. 关键约定速查

- 端口:`8000` backend / `8100` dc / `5777` 前端;两库 `quant_db`(:5432)/ `factor_db`(:5433)。
- 复权:`raw_bars.close` = 全局一致 qfq(最新 `adj_factor` 归一);时序因子用 `adj_*`;同日价比值与不复权等价。
- 因子横截面:`{FACTOR_DATA_DIR}/{category}/{date}.parquet`,index=symbol,列=因子代码。
- 新增因子:Factor 类+@register,或 formula 入 `factor.definitions`。
- 调仓在 backend;dc 只做"算持仓"与"行情中继"。
- 部署:根 `docker-compose.yml`(postgres/postgres-factor/backend/redis/data-cleaner)。

---

## 7. 关键文件指针

- 根:`docker-compose.yml`、`run_daily.sh`(hermes-bot 每日同步/报告,非交易流水线)、`README.md`、`FEATURES.md`。
- backend:`main.py`、`app/api/api_v1/api.py`、`app/services/{factor_strategy_proxy,rebalance_service}.py`、`app/services/broker/`、`app/tasks/scheduler.py`、`app/models/`。
- dc:`app/main.py`、`app/tasks/{scheduler,factor_build,factor_evaluate,daily_pipeline,backfill,fundamental_refresh}.py`、`app/factors/{base,registry,formula}.py`、`app/pipeline/runner.py`、`app/storage/{db,parquet_store,raw_store,fundamental_store}.py`、`app/strategy/engine.py`、`migrations/001–010`。
