# 因子选股策略配置示例

因子策略的 `config` 以 JSON 形式存于 `strategy.config`（JSONB）。下面是一份完整示例，
包含 P1 阶段新增的「停牌 / 涨停 / 跌停 / 市值下限」过滤字段（均集中在 `filters` 内）。

## 创建接口

```
POST /strategy/strategies
```

```json
{
  "name": "质量成长因子 v1",
  "description": "ROE + 动量 + 低波 三类因子等权，行业中性化，周频调仓",
  "owner": "quant",
  "is_active": false,
  "config": {
    "factor_codes": [
      "VAL_PE_TTM",
      "GRO_REV_GROWTH_YOY",
      "GRO_EPS_GROWTH_YOY",
      "MKT_CAP",
      "MKT_CAP_CIRC",
      "TURNOVER_RATE",
      "TURNOVER_RATE_F",
      "MOM_RET_20"
    ],
    "weights": {},
    "weight_mode": "auto_ir",
    "neutralize": "industry",
    "top_n": 30,
    "rebalance": { "freq": "weekly" },
    "trade_time": "10:00",
    "initial_capital": 1000000,
    "lookback_days": 60,
    "universe": [],
    "custom_codes": [],
    "filters": {
      "exclude_st": true,
      "min_list_days": 60,
      "exclude_suspended": true,
      "exclude_limit_up": true,
      "exclude_limit_down": false,
      "min_cap": 200
    }
  }
}
```

## filters 字段说明

| 字段 | 类型 | 默认 | 含义 |
| --- | --- | --- | --- |
| `exclude_st` | bool | `true` | 名称含 `ST` 的标的剔除 |
| `min_list_days` | int | `60` | 最小上市天数，不足则剔除 |
| `exclude_suspended` | bool | `true` | 排除停牌（`trading_status.suspended=1` 或当日无 bar） |
| `exclude_limit_up` | bool | `true` | **买入侧**：`close >= limit_up`（涨停）剔除，买不进 |
| `exclude_limit_down` | bool | `false` | **卖出侧语义**：`close <= limit_down`（跌停）剔除，避免接飞刀 |
| `min_cap` | number \| null | `null` | 总市值下限（**亿元**）；`total_mv < min_cap×1e5`(千元) 剔除；`null`=不限 |

> 单位提示：`min_cap` 的「亿元」需与数据源 `daily_basic.total_mv` 单位对齐——tushare 的
> `total_mv` 单位为千元，引擎换算 `min_cap × 1e5`；若切换为 akshare 等数据源，接入时请核对
> `total_mv` 单位，避免门槛错配。

## 回测 / 持仓预览

- 回测：`POST /strategy/strategies/{id}/backtest`（`{"start": "...", "end": "..."}`）
- 任意配置算目标持仓（前端选股工作室）：`POST /strategy/scores`（`{"config": {...}, "as_of": "..."}`）

## 缺省与容错

引擎侧 `engine.normalize_filters(config)` 会集中做默认值与类型校验：
所有 key 缺失时用上表「默认」值；字符串形式的数值会被安全转换（`"200"` → `200.0`）；
`min_cap` 为空串 / 非法值时归零为 `null`（即不限）。因此老配置即使不含新字段也能正常回测。

## P1 新增因子代码目录

下表为 P1 因子新增阶段落地的 6 个因子（真实换手 / 市值 / 成长），均依赖迁移 006 的
`factor.daily_basic` 与 `factor.finance_reports`，需先跑 `refresh_fundamental` 再 `build_factor`
重建因子库才有值。其余因子代码见因子底册（`GET /factor/factors`）。

| 因子代码 | 类别 | 数据源 | 含义 / 说明 |
| --- | --- | --- | --- |
| `TURNOVER_RATE` | sentiment | `daily_basic.turnover_rate` | 真实换手率 = 成交量/流通股本(%)，替代旧代理 `SENT_TURNOVER_20` |
| `TURNOVER_RATE_F` | sentiment | `daily_basic.turnover_rate_f` | 自由流通换手率 = 成交量/自由流通股本(%) |
| `MKT_CAP` | size | `daily_basic.total_mv` | 总市值(对数)，对 `total_mv`(千元) 取 `ln` 后截面 z-score |
| `MKT_CAP_CIRC` | size | `daily_basic.circ_mv` | 流通市值(对数)，剔除限售股，更贴近 A 股真实规模效应 |
| `GRO_REV_GROWTH_YOY` | growth | `finance_reports.rev_growth_yoy` | 营收同比增长率，已 `clip(-1, 5)` 稳健化 |
| `GRO_EPS_GROWTH_YOY` | growth | `finance_reports.eps_growth_yoy` | 净利润同比增长率，已 `clip(-1, 5)` 稳健化 |

> 规模/换手为日频截面字段；成长因子按 `ann_date <= 交易日` 做 as-of 前向填充防前视，
> 财报缺失时该因子全为 `NaN`（前端底册显示「不可用」），不影响其他因子计算。

### 数据刷新与重建

```bash
# 1) 拉取 daily_basic（换手/市值）+ trading_status + 财报（成长），写入 PG factor 库
python -c "from app.tasks.fundamental_refresh import refresh_fundamental; print(refresh_fundamental())"

# 2) 重建因子库（含新增的真实换手/市值/成长因子的截面 parquet）
python -c "from app.tasks.factor_build import build_factor_library; build_factor_library()"
```

> 注：上面为实际可调用函数（`refresh_fundamental()`、`build_factor_library()`），
> 定义在 `app/tasks/fundamental_refresh.py` 与 `app/tasks/factor_build.py`；
> 若在服务容器内运行，需先确保 `DATABASE_URL`、因子库路径等环境变量就位。
