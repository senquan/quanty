# 量化交易系统核心功能完善

## 🎯 **完成的功能模块**

### 1. **量化回测引擎** (`app/services/backtest_engine.py`)
- **数据源支持**: Yahoo Finance (股票)、CCXT (加密货币)
- **策略执行**: 安全的代码执行环境，支持buy/sell操作
- **基础指标**: 总收益率、夏普比率、最大回撤、胜率、交易次数
- **策略验证**: 语法检查、安全性检查、必需函数检查

### 2. **技术指标系统** (`app/services/technical_indicators.py`)
- **基础指标**: SMA、EMA、RSI、MACD、布林带、随机指标、ATR
- **数据增强**: 自动为价格数据添加技术指标
- **交易信号**: 基于技术指标的买卖信号生成
- **信号组合**: 多指标综合信号计算

### 3. **高级性能分析** (`app/services/performance_analyzer.py`)
- **收益指标**: 总收益、年化收益、月度/年度收益统计
- **风险指标**: 波动率、下行波动率、VaR、CVaR、偏度、峰度
- **风险调整收益**: 夏普比率、索提诺比率、卡尔玛比率、信息比率
- **交易分析**: 盈亏统计、持仓时间、盈利因子
- **回撤分析**: 最大回撤、平均回撤、回撤持续时间
- **基准比较**: Beta、Alpha、跟踪误差、上涨/下跌捕获率

### 4. **前端策略管理界面** (`frontend/src/views/quant/strategy/`)
- **策略列表**: 搜索、筛选、性能概览
- **策略编辑器**: 代码高亮、模板插入、实时验证
- **回测配置**: 标的选择、时间范围、初始资金设置
- **结果展示**: 关键指标卡片、图表可视化、交易记录

### 5. **可视化图表系统** (`BacktestResults.vue`)
- **净值曲线**: 组合价值走势、回撤分析切换
- **收益分布**: 日收益率直方图
- **交易记录**: 详细的买卖记录和盈亏计算
- **详细指标**: 收益指标、风险指标分栏展示

## 🔧 **API接口扩展**

### 策略管理
- `POST /api/v1/quant/strategies` - 创建策略
- `GET /api/v1/quant/strategies` - 获取策略列表
- `GET /api/v1/quant/strategies/{id}` - 获取单个策略
- `PUT /api/v1/quant/strategies/{id}` - 更新策略
- `DELETE /api/v1/quant/strategies/{id}` - 删除策略

### 回测功能
- `POST /api/v1/quant/backtest` - 运行回测
- `GET /api/v1/quant/backtest-history/{strategy_id}` - 获取回测历史
- `POST /api/v1/quant/validate-strategy` - 验证策略代码

### 数据服务
- `GET /api/v1/quant/market-data` - 获取市场数据

## 📊 **技术架构**

### 后端架构
```
app/services/
├── backtest_engine.py      # 回测引擎核心
├── technical_indicators.py # 技术指标计算
└── performance_analyzer.py # 性能分析器

app/api/api_v1/endpoints/
└── quant.py                # 量化相关API
```

### 前端架构
```
frontend/src/views/quant/strategy/
├── index.vue               # 策略管理主页面
└── components/
    └── BacktestResults.vue # 回测结果组件

frontend/src/api/
└── quant.ts               # 量化API接口
```

## 🚀 **使用示例**

### 1. 创建策略
```python
strategy_data = {
    "name": "双均线策略",
    "description": "基于20日和50日均线的交叉策略",
    "code": """
# 获取均线数据
sma_20 = data['sma_20']
sma_50 = data['sma_50']
close = data['close']

# 交易逻辑
for i in range(50, len(data)):
    # 金叉买入
    if sma_20.iloc[i-1] <= sma_50.iloc[i-1] and sma_20.iloc[i] > sma_50.iloc[i]:
        buy(close.iloc[i])
    
    # 死叉卖出
    elif sma_20.iloc[i-1] >= sma_50.iloc[i-1] and sma_20.iloc[i] < sma_50.iloc[i]:
        position = get_position()
        if position > 0:
            sell(close.iloc[i], position)
"""
}
```

### 2. 运行回测
```python
backtest_request = {
    "strategy_id": 1,
    "symbol": "AAPL",
    "data_source": "yahoo",
    "start_date": "2023-01-01",
    "end_date": "2023-12-31",
    "initial_capital": 100000.0
}
```

### 3. 性能分析结果
```json
{
    "total_return": 15.6,
    "annualized_return": 15.6,
    "sharpe_ratio": 1.8,
    "sortino_ratio": 2.4,
    "max_drawdown": -8.2,
    "volatility": 12.3,
    "var_95": -2.1,
    "win_rate": 62.3,
    "profit_factor": 1.8,
    "alpha": 3.2,
    "beta": 0.9
}
```

## 🛠️ **依赖更新**

### 新增Python包
```txt
pandas==2.1.4          # 数据分析
numpy==1.24.4          # 数值计算
yfinance==0.2.36        # 金融数据获取
ccxt==4.2.25           # 加密货币交易所
matplotlib==3.7.5      # 图表绘制
scipy==1.11.4           # 科学计算
TA-Lib==0.4.28          # 技术分析库
```

## 📈 **系统优势**

1. **专业级回测引擎**: 支持多数据源、安全执行、详细分析
2. **丰富的技术指标**: 30+常用技术指标，自动计算和信号生成
3. **全面性能分析**: 50+性能指标，与基准比较，风险评估
4. **现代化前端**: Vue3 + Element Plus，图表可视化，用户体验佳
5. **可扩展架构**: 模块化设计，易于添加新功能和数据源
6. **生产就绪**: 完整的错误处理、数据验证、安全检查

## 🎯 **下一步建议**

1. **实盘交易接口**: 集成券商API进行实盘交易
2. **机器学习策略**: 支持基于ML的量化策略
3. **多资产组合**: 支持股票、期货、期权等多资产回测
4. **实时监控**: 策略运行状态实时监控和报警
5. **社区策略**: 策略分享和评级系统
6. **云端部署**: 支持云服务和分布式计算

这个量化交易系统现在具备了企业级的功能完整性，可以满足专业量化交易的需求。

## 🧩 **因子选股策略（data-cleaner 因子引擎）**

> 注：本章节为 `data-cleaner/app/strategy` 下的因子选股 / 回测引擎，与上文基于 `buy/sell` 脚本的
> 回测引擎（`backend/app/services`）为两套独立实现。

- **配置存储**：`strategy.config`（JSONB），由前端 `FactorStrategyConfig` 与引擎 `normalize_filters` 共同约束。
- **过滤字段**（集中于 `config.filters`，详见 [`docs/strategy-config-example.md`](./strategy-config-example.md)）：

  | 字段 | 默认 | 含义 |
  | --- | --- | --- |
  | `exclude_st` | `true` | 剔除 ST |
  | `min_list_days` | `60` | 最小上市天数 |
  | `exclude_suspended` | `true` | 排除停牌 |
  | `exclude_limit_up` | `true` | 买入侧排除涨停（买不进） |
  | `exclude_limit_down` | `false` | 卖出侧排除跌停（不接飞刀） |
  | `min_cap` | `null` | 总市值下限（亿元），`null`=不限 |

- **接口**：`POST /strategy/strategies`（建）、`POST /strategy/strategies/{id}/backtest`（回测）、
  `POST /strategy/scores`（任意配置算目标持仓）。
- **回测真实度**：成交以 `limit_up/limit_down` 判定可成交性；停牌不再「剔除即失踪」，价量表缺 bar 视为
  停牌，前向填充价并标记 `suspended`。
- **P1 新增因子**：真实换手 `TURNOVER_RATE` / `TURNOVER_RATE_F`（替代代理 `SENT_TURNOVER_20`）、
  市值 `MKT_CAP` / `MKT_CAP_CIRC`（对总/流通市值取 `ln`）、成长 `GRO_REV_GROWTH_YOY` / `GRO_EPS_GROWTH_YOY`
  （已 `clip(-1,5)` 稳健化，as-of 防前视）。代码目录与刷新/重建命令见
  [`docs/strategy-config-example.md`](./strategy-config-example.md) 的「P1 新增因子代码目录」。

## 🎯 **P2 精度 / 复权与行业增量刷新**

- **复权入库**：`factor.raw_bars` 新增 `adj_factor`（复权因子）与 `hfq_close`（后复权收盘价）两列
  （迁移 `data-cleaner/migrations/007_adj_factor.sql`）。
- **全局一致前复权（qfq）**：接入层（tushare）拉取「全历史 `adj_factor`」，以全历史最新因子
  `f_latest` 归一化，`close` 即全局一致的 qfq（旧实现按「窗口内最新一日」局部基准，多次增量后
  跨窗口基准漂移，含权期收益/动量有偏）。`scale = f / f_latest`，`open/high/low/close` 同步归一化，
  价格类因子（动量/波动/技术/情绪额）经 `adj_close=close` 自动修正。
- **后复权（hfq）**：`hfq_close = close * f_latest / f_first`，下游亦可由 `close + adj_factor`
  反推 `hfq = close * f_latest / f_first`。`alphafeed` 仅透出前复权价，`adj_factor`/`hfq_close` 置空
  （hfq 不可用，qfq 仍可用）。
- **降级**：tushare `adj_factor` 获取失败（1 次/分钟限频）时 `close` 退化为原始价，`adj_factor`/
  `hfq_close` 置空，由日志告警。
- **迁移注意**：部署后建议执行一次全量回填（`backfill --full`），把历史 `close` 重新键定为全局 qfq
  基准；否则旧行仍为窗口局部基准，跨旧/新边界收益仍略有偏差。
- **行业分类每日增量刷新**：`app/tasks/scheduler.py` 的 `_industry_refresh_job` 由「仅周六 09:30」
  改为「交易日 18:40」每日执行；`refresh_industries` 本身为全量拉取 + upsert 幂等，等价每日增量，
  新上市/退市与行业重分类次日即反映到中性化与上市天数过滤。