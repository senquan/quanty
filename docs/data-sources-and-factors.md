# 数据资产与因子映射

- 整理日期：2026-09-08
- 范围：`data-cleaner` 项目（A 股日频因子库）。所有耗时均为**基于代码与现有文档的估算**，
  生产日志中的真实 `duration_s` 可能略有出入，落地后应以日志为准校准。

---

## 1. 数据类型、存储与来源

数据落在 PG `factor` 模式（行情/基本面/状态）与本地 parquet（因子截面）两处。

| 数据类型 | 存储位置 | 主来源 | 补充/回退来源 | 频率 / 增量方式 | 历史覆盖（实测 2026-09-08） |
|---|---|---|---|---|---|
| **行情日线** `raw_bars` | `factor.raw_bars` | pandadata `get_stock_daily`（含前/后复权，全市场主力） | `.BJ` 北交所自动路由 akshare | 日频；增量 `[latest+1, today]` | 2010-01-01 起（全量回补后） |
| **基础数据** `daily_basic` | `factor.daily_basic` | tushare `daily_basic`（**仅最近 ~3 日**，低权限 token 限制） | akshare `stock_a_indicator_lg`（pe/pb/ps/dv 历史，逐标的全历史）；pandadata `get_share_float`×收盘价**推导** total_mv/circ_mv/float_share | 日频；每日刷最新日，历史需回填 | 仅最近 ~168 交易日有行；估值列仅最近 3~5 日有值 |
| **交易状态** `trading_status` | `factor.trading_status` | tushare `stk_limit`+`daily`+`suspend_d`（`auto` 优先） | akshare 涨停池/跌停池/停复牌 | 日频，最新日 | 近期 |
| **财报** `finance_reports` | `factor.finance_reports` | tushare `fina_indicator`（权限受限） | akshare `stock_yjbb_em`（业绩报表，逐报告期，营收/净利同比）；akshare `stock_financial_analysis_indicator`（ROE/负债率/总资产，逐标的） | 报告期；近期若干期 | `roe/debt/eps` 自 2020 较完整；`rev/eps 同比`仅 ~42% |
| **股票元数据** `stock_info` | `factor.stock_info` | tushare `stock_basic` / akshare | — | 静态，偶发刷新 | `industry` 当前**全空**（待修）；`list_date` 全有 |
| **代码池** universe | 内存/缓存 | tushare `stock_basic` / akshare | — | 启动或调度时拉取 | 约 5200–5600 只 A 股（含主板/双创；北交所少量） |
| **因子截面** parquet | `{FACTOR_DATA_DIR}/{category}/{YYYY-MM-DD}.parquet` | 本地计算（`factor_build`，由上述 1–5 派生） | — | 每日构建，按「日期×类别」 | 9 类 × 每日一份 |

> 行情与基础数据通过 `_merge_fundamental`（`app/tasks/factor_build.py:27`）在因子构建时按
> `(symbol, date)` 左连接进清洗后的 panel；`stock_info` 为静态左连。

---

## 2. 每日盘后流水线（`run_daily_pipeline`）

顺序执行，任一步失败不阻断后续（结果记到 `steps`，便于事后定位）：

1. **行情增量** `backfill_universe(full=False)` → pandadata 逐标的拉最新日。
2. **基础数据刷新** `refresh_fundamental(trade_date)` → 写 `daily_basic` / `trading_status` / `finance_reports`。
3. **因子库构建** `build_factor_library()`（无参 → 全量重建，见 §3）。
4. **因子效能评估** `evaluate_all_factors()` → 滚动 IC/IR 等。
5. **变更广播** WS 推送因子版本（backend 经 HTTP 增量拉真实数据）。

---

## 3. 每日增量所需时间（估算）

| 步骤 | 动作 | 数据源 | 估算耗时 | 说明 |
|---|---|---|---|---|
| 1 行情拉取 | `backfill_universe(full=False)` | pandadata（逐标的 ~5214 次请求） | **约 50–55 min** | 现节流至 ~100/min（`_RATE_LIMIT_PER_MIN`，命中 500010 自动退避+收紧）。此前未限流约 23s 但会触发 `500010 每分钟请求次数超限` 漏数（09-07/09-08 复盘根因），已修复。阈值可调。 |
| 1.5 基础刷新 | `refresh_fundamental` | tushare/akshare | **约 1–5 min** | daily_basic（tushare 单日 bulk）+ trading_status + finance_reports（近期 8 期）；akshare 网络波动为主变量 |
| 2 因子构建 | `build_factor_library()` | 本地（读全量 raw_bars） | **随历史规模增长**：当前数据量约数分钟~十余分钟；2010+ 全历史（约 2000 万行）可达数十分钟 | **无参调用 = `load_all()` 加载全部历史后全量重建**，非仅最新日。最重一步。可优化为按最新日增量重建。 |
| 3 效能评估 | `evaluate_all_factors` | 本地 | **约数分钟** | 滚动 IC 等，与因子数线性相关 |
| **合计** | | | **当前约 1 小时出头；全历史后约 1.5–2 小时** | 盘后（~16:00 数据就绪）至 ~17–18:00 完成，对 EOD 调度可接受 |

**已知特征 / 风险**
- 行情节流：`_RATE_LIMIT_PER_MIN=100` 同时作用于**每日增量**（逐标的），把原 23s 路径换成稳定的 ~50min；
  这是为消除 500010 漏数的代价，阈值可按 pandadata 实测上限（~225/min）上移以提速。
- 因子全量重建：每日 `build_factor_library()` 无参重建全历史，数据量增长后将成为主要耗时；
  若改为「仅最新日增量 + 历史按需触发」，每日因子步骤可降至分钟级（建议优化项）。
- 基础数据历史缺口：并非每日任务范畴，需独立历史回填（见 `docs/plans/2026-09-08.fundamental-history-backfill.md`）。

---

## 4. 因子 ↔ 数据来源映射（41 因子）

依赖列 → 数据表：`adj_*`/`close`/`high`/`low`/`open`/`volume`/`amount` ⇒ `raw_bars`（pandadata）；
`pb`/`ps_ttm`/`pe_ttm`/`dv_ttm`/`div_yield`/`turnover_rate`/`turnover_rate_f`/`total_mv`/`circ_mv` ⇒ `daily_basic`；
`rev_growth_yoy`/`eps_growth_yoy`/`roe`/`debt_ratio`/`eps_ttm` ⇒ `finance_reports`；`dividend_ttm` ⇒ `stock_info`。

| 因子代码 | 类别 | 频率 | 依赖数据列 | 数据表/来源 |
|---|---|---|---|---|
| FND_DEBT_RATIO | fundamental | Daily | debt_ratio | finance_reports |
| FND_ROE | fundamental | Daily | roe | finance_reports |
| GRO_EPS_GROWTH_YOY | growth | Daily | eps_growth_yoy | finance_reports |
| GRO_PRICE_MOMENTUM | growth | Daily | adj_close | raw_bars |
| GRO_REV_GROWTH_YOY | growth | Daily | rev_growth_yoy | finance_reports |
| LIQ_AMOUNT_20 | liquidity | Daily | amount | raw_bars |
| INTRADAY_MOM_10 | momentum | Intraday | adj_close | raw_bars |
| MOM_ACCEL | momentum | Daily | adj_close | raw_bars |
| MOM_RET_20 | momentum | Daily | adj_close | raw_bars |
| MOM_RET_5 | momentum | Daily | adj_close | raw_bars |
| MOM_RET_60 | momentum | Daily | adj_close | raw_bars |
| REL_STR_20 | momentum | Daily | adj_close | raw_bars |
| SENT_AMOUNT_RANK | sentiment | Daily | volume, adj_close | raw_bars |
| SENT_VOL_RATIO_5 | sentiment | Daily | volume | raw_bars |
| TURNOVER_RATE | sentiment | Daily | turnover_rate | daily_basic |
| TURNOVER_RATE_F | sentiment | Daily | turnover_rate_f | daily_basic |
| SIZE_MKT_CAP | size | Daily | total_mv | daily_basic（pandadata 推导） |
| SIZE_MKT_CAP_CIRC | size | Daily | circ_mv | daily_basic（pandadata 推导） |
| CORR5 | technical | Daily | close, volume | raw_bars |
| HIGH0 | technical | Daily | adj_high, adj_close | raw_bars |
| KLEN | technical | Daily | high, low, open | raw_bars |
| TECH_BB_POS | technical | Daily | adj_close | raw_bars |
| TECH_MACD_CROSS | technical | Daily | adj_close | raw_bars |
| TECH_MACD_DEA | technical | Daily | adj_close | raw_bars |
| TECH_MACD_DIF | technical | Daily | adj_close | raw_bars |
| TECH_MACD_HIST | technical | Daily | adj_close | raw_bars |
| TECH_MA_BIAS_20 | technical | Daily | adj_close | raw_bars |
| TECH_RSI_14 | technical | Daily | adj_close | raw_bars |
| VAL_DIV_YIELD | value | Daily | dividend_ttm, adj_close, div_yield | stock_info / raw_bars / daily_basic（优先 dividend_ttm/价 推导，回退 dv_ttm） |
| VAL_PB | value | Daily | pb | daily_basic |
| VAL_PE_PERCENTILE | value | Daily | pe_ttm | daily_basic |
| VAL_PE_TTM | value | Daily | adj_close, eps_ttm | raw_bars / finance_reports（eps_ttm = 年报 eps ffill） |
| VAL_PS_TTM | value | Daily | ps_ttm | daily_basic |
| STD5 | volatility | Daily | adj_close | raw_bars |
| VOL_ATR_14 | volatility | Daily | adj_high, adj_low, adj_close | raw_bars |
| VOL_PARKINSON_20 | volatility | Daily | adj_high, adj_low | raw_bars |
| VOL_SKEW_60 | volatility | Daily | adj_close | raw_bars |
| VOL_STD_20 | volatility | Daily | adj_close | raw_bars |
| VOL_STD_20_ANN | volatility | Daily | adj_close | raw_bars |
| INTRADAY_RANGE_20 | volatility | Intraday | adj_high, adj_low, adj_close | raw_bars |
| INTRADAY_VOL_20 | volatility | Intraday | adj_close | raw_bars |

**依赖结构小结**
- 纯 `raw_bars`（价量）即可算：**momentum / volatility / technical / liquidity** 全类 + **growth.GRO_PRICE_MOMENTUM** + **sentiment** 量比/成交额分位 + **value.VAL_PE_TTM / VAL_DIV_YIELD** 的价推导回退。
- 依赖 `daily_basic`：**value**(PB/PS/PE分位)、**size**(市值)、**sentiment**(换手率)。
- 依赖 `finance_reports`：**growth**(营收/净利同比)、**fundamental**(ROE/负债率)、**value.VAL_PE_TTM**(eps_ttm 回退)。
- 依赖 `stock_info`：**value.VAL_DIV_YIELD**(dividend_ttm)。

---

## 5. 已知缺口（衔接方案）

- 基础数据历史缺口（`daily_basic` 仅近期、`finance_reports` 成长仅 42%）→ 见
  `docs/plans/2026-09-08.fundamental-history-backfill.md`（独立回填任务，与本每日流水线正交）。
- `stock_info.industry` 全空 → 由 `metadata_refresh` 另修，影响行业中性化，不影响因子值 NaN。
- 每日因子全量重建 → 建议优化为最新日增量（见 §3）。
