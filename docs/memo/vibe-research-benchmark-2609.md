# Vibe-Research 借鉴分析

- 分析日期：2026-09-06
- 参照项目：`E:\Workspace\lab.Quant\docs\repos\Vibe-Research`（v1.0.4，MIT）
- 现有项目：`E:\Workspace\lab.Quant`（Quanty）
- 结论摘要：**有 5 点值得借鉴，但在此之前 Quanty 的回测引擎是坏的，必须先修。**

---

## 一、Vibe-Research 是什么

基于 OpenAI Codex Harness 的**本地金融研究工作台**。核心是 AI Agent 驱动的投研：六阶段个股研究、证据链、确定性计算、合规闸门。

| 层 | 组成 | 作用 |
|---|---|---|
| 提示层 | `AGENTS.md` + `.agents/skills/` | 金融研究纪律与 SOP |
| 执行层 | Codex hooks + workspace sandbox | 限制联网、文件访问、取数范围 |
| 编排层 | orchestrator + validator + calc + gate | 强制阶段、证据引用、确定性计算、合规 |

技术栈：Node/TS orchestrator + React/Vite desktop + Python（calc / backtest / 取数脚本）。
规模：数据端点 117 个 / 30 层；测试 orchestrator 539 + desktop 25 + Python 575。

## 二、定位差异——交集只有"回测"

| 维度 | Vibe-Research | Quanty | 谁强 |
|---|---|---|---|
| 核心业务 | 投研（研究/证据/报告） | 回测 + 因子 + 实盘 | — |
| 回测 | Agent 的一个工具入口 | 主业 | Quanty 应更强（实际没有） |
| 因子库 | 无 | `data-cleaner` 因子管线 + 多实例网关 | **Quanty** |
| 实盘 | 无（明确不做投资建议） | `huatai` / `broker` / 组合再平衡 | **Quanty** |
| 性能指标 | `backtest/metrics.py`（26KB） | `performance_analyzer.py` 六大类 50+ 指标 | 平手 |
| 数据源 | 117 端点注册表 + 健康巡检 | Tushare / PandaData 入库 | Vibe 更系统 |
| 结果可信度 | gate + 统计检验 + run card | 无 | **Vibe** |

**关键判断**：两者真正重叠的只有回测。而恰恰在回测上，Vibe-Research 有的四样东西 Quanty 全没有——且都是"让结果可信"的，不是"让指标变多"的。

---

## 三、⚠️ 最高优先级：Quanty 回测引擎当前是坏的（不是借鉴，是先修）

### 根因

`E:\Workspace\lab.Quant\backend\app\services\backtest_engine.py`

```python
# :143  买入时记录成交时间 —— 用的是"现在"，不是 bar 的时间
self.trades.append({'type': 'buy', ..., 'timestamp': pd.Timestamp.now()})

# :167  卖出同样
self.trades.append({'type': 'sell', ..., 'timestamp': pd.Timestamp.now()})

# :100  撮合条件：成交时间 <= 当前 bar 的日期才撮合
while trade_index < len(trades_sorted) and \
      trades_sorted[trade_index]['timestamp'].date() <= enriched_data.index[i].date():
```

回测跑 2023 年数据，成交时间记成 2026-09-06（今天）→ `2026-09-06 <= 2023-01-03` 恒为 False → **一笔都不撮合**。

### 实测证据

复现脚本：`E:\Workspace\lab.Quant\_vr_probe.py`（不联网，假数据，直接调 `execute_strategy`）
运行：`PYTHONUTF8=1 D:/Python/Python312/python.exe _vr_probe.py`

```
bar 区间 : 2023-01-02 -> 2023-12-29 (260 bars)
交易笔数 : 7
末笔交易 : {'type': 'buy', 'price': 106.617, 'quantity': 1835,
            'timestamp': Timestamp('2026-09-06 13:54:33.475936')}   ← 记成今天
组合价值 前3: [100000.0, 100000.0, 100000.0]
组合价值 后3: [100000.0, 100000.0, 100000.0]
指标     : {'total_return': 0.0, 'sharpe_ratio': 0, 'max_drawdown': 0,
            'win_rate': 42.86, 'total_trades': 7, 'final_capital': 100000}
```

7 笔交易发出并记录成功，但**组合价值全程等于初始资金**，收益/夏普/回撤全为 0。
而报告照样显示"7 笔交易、胜率 42.86%"——数字排版整齐，看不出任何异常。

这正好是 Vibe-Research `backtest/README.md:28` 那句话的活教材：

> 一个不成立的回测照样能算出夏普和最大回撤，数字排版整齐、看不出异常。闸口的价值在**拦住**。

### 影响面

`backend\app\api\api_v1\endpoints\quant.py:231-233` 是回测主入口，直接调 `BacktestEngine`：

```python
231  engine = BacktestEngine(initial_capital=backtest_request.initial_capital)
232  results = engine.execute_strategy(strategy.code, data)
233  metrics = engine.calculate_metrics(results)
```

→ **当前线上跑出来的所有回测结果都是「总收益 0.00%」。**

### 修复要点

撮合循环不该依赖交易自带的 timestamp，而应在**逐 bar 推进时**由引擎给出当前 bar 时间。建议改成事件驱动：策略在每个 bar 只看到 `data.iloc[:i+1]`，由引擎按 bar 撮合。

### 同文件附带问题（次要但真实）

| 行号 | 问题 |
|---|---|
| `:179-181` | `win_rate` = 卖出笔数 / 总笔数，不是盈利比例（实测 3 sell / 7 = 42.86%） |
| `:87` | `exec` 后策略自己 `for` 全历史 → 策略一次性看到未来数据，**未来函数** |
| `:264` | 安全检测是字符串 `in` 判断，`__import__` 挡不住 `import os` |
| 全文 | 无涨跌停、T+1、手续费、最小手数 → **A 股回测不可用** |

---

## 四、值得借鉴的 5 点

### 1. Gate 闸口——跑之前先判"这个回测成不成立"

- 文件：`docs\repos\Vibe-Research\backtest\gate.py:159-290`
- 核心：**只输出 `Plan` 或 `Refusal`，没有"带一堆警告勉强跑"的第三种**。原文注释：「警告没人看，数字人人看」。
- 三问：① 回测什么（代码→市场→规则）② 需要什么（口径→bar 粒度、最少样本）③ 限制是什么（这个市场根本做不了什么）
- 拦住：混市场 · A股做T · 任何市场做T（无分钟数据）· 样本不足 · A股做空 · 认不出代码 · 区间反了 · 本金买不起一手

```python
# gate.py:103-110
@dataclass(frozen=True)
class Refusal:
    reason: str   # 为什么不成立
    remedy: str   # 怎么才能跑
    def __bool__(self) -> bool:
        return False    # 便于 `if not plan:` 直接用
```

关键设计：`Plan.limits`（`:268-276`）是**必须随结果一起呈现**的限制说明——交易机制、做空、最小单位、费用、币种、涨跌停。不是藏在文档里，是跟数字一起出。

**落地**：在 `quant.py` 回测入口前加 `plan_backtest()`，返回 `Refusal` 时直接 HTTP 422 带 `reason` + `remedy`。这个改造量小、收益最大——上面那个 0 收益 bug 如果有闸口，会以"样本/撮合异常"被拦下。

### 2. 统计显著性验证——夏普是本事还是运气

- 文件：`docs\repos\Vibe-Research\backtest\validation.py`

| 方法 | 行号 | 回答的问题 |
|---|---|---|
| `monte_carlo_test` | `:29` | 打乱成交顺序后，原策略还显著优于随机吗（p 值） |
| `bootstrap_sharpe_ci` | `:136` | 夏普的置信区间多宽、`prob_positive` 多少 |
| `walk_forward_analysis` | `:208` | 分 5 个时间窗，各窗表现一致吗（一致性率） |

Quanty 的 `performance_analyzer.py` 能算 50+ 指标，但**没有一个是"这个数字可不可信"**。夏普 1.8 和夏普 0.3 在报告里长得一样。

**落地**：`validation.py` 几乎可以整文件移植——只依赖 `numpy` / `pandas` + 一个 `TradeRecord`。Quanty 的 trades 是 dict，写个适配函数即可。

### 3. Run Card——这次跑的到底是什么

- 文件：`docs\repos\Vibe-Research\backtest\run_card.py:25-95`
- 内容：config_hash（配置指纹）+ strategy_hash（策略源码指纹）+ 每个 artifact 的 size 与 sha256 + 数据源清单 + 指标 + warnings
- 同时输出 JSON 与 Markdown 两份

**落地**：回测结果表加 `config_hash` / `strategy_hash` 两列。成本极低，但能回答"这份回测是拿哪个版本策略、哪套参数跑的"——Quanty 现在答不了。

### 4. 严格 JSON / 绝不返回 NaN

三处一致的做法：

| 位置 | 做法 |
|---|---|
| `validation.py:439-456` | `write_validation_json` 用 `allow_nan=False`，非有限值 → `null` |
| `risk_xray.py:36-44` | `_finite()` 每个对外 float 都过一遍 |
| `calc\README.md:13` | 原则：「绝不返回 inf / NaN；无意义域返回 `not_meaningful` 而不是伪装成数」 |

Quanty 现状：指标全是裸 float。`json.dumps` 默认 `allow_nan=True`，会吐出非法 JSON 的裸 `NaN` token；前端收到后图表白屏或显示 `null` 而不报错。

**落地**：加一个 `_json_safe()` 工具函数，在所有 API 出口统一过一遍。

### 5. 数据源注册表 + 健康巡检

- `datasources\registry.json`（82KB，117 端点）+ `datasources\health.py`
- `health.py`：按注册表逐端点**实跑**取数，4 线程并发，汇总 ok / partial / failed / 耗时 / 失败原因 → `health_report.json` + `health_report.md`
- 报告里明写：「健康检查结果只反映本机网络 / 该时刻源状态，不作为研究证据」

Quanty 的 `cleaner_gateway.py` 有 QoS 轮询，但那回答的是"服务活着吗"，不是"数据对不对"。因子层面没有对等的巡检。

**落地**：给 `factor_registry` 加一个类似的 health 巡检，逐个因子实跑、出报告。data-cleaner 现在满地 `_probe_*.py` / `_verify_*.py` 一次性脚本，正是缺这个机制的症状。

---

## 五、不建议借鉴的

| 项 | 原因 |
|---|---|
| Codex Harness 整套 Agent 编排 | 与 Quanty 的 FastAPI + Vue 定位不同，改造成本远大于收益 |
| 六阶段研究 SOP | 投研向，Quanty 不做个股研究 |
| 合规 gate（不给目标价/止损） | Quanty 是自有交易系统，约束前提不同 |
| `calc/formulas.py` 估值公式 | 估值向，用不上 |
| `orchestrator/number_fidelity.ts`（35KB） | 数字绑定校验很扎实，但耦合报告生成链路，强依赖其 Agent 架构 |

**但有一条设计思想值得单独拎出来**：Vibe-Research 把"计算"从 LLM 里彻底剥离——`calc` 是纯函数库，agent 只选输入、解释输出，每个结果带 `calculation_id`（实参 + 引用 DAG + 序列 sha256），可复算可审计。Quanty 若日后接 AI 辅助分析，这个边界应该现在就划清楚。

---

## 六、建议执行顺序

| 优先级 | 事项 | 涉及文件 |
|---|---|---|
| P0 | 修回测撮合（bar 时间误用 now）→ 事件驱动逐 bar 推进 | `backtest_engine.py:100,143,167` |
| P0 | 修 `win_rate` 口径（盈利笔数 / 平仓笔数） | `backtest_engine.py:179-181` |
| P1 | 加回测闸口 `plan_backtest()`（Plan / Refusal） | 新增 `backend/app/services/backtest_gate.py` + `quant.py:231` 前 |
| P1 | 补 A 股市场规则：涨跌停（按前收）、T+1、费用、整手 | 引擎内 |
| P2 | 移植 `validation.py` 三件套（MC / Bootstrap / WF） | 新增 `backend/app/services/backtest_validation.py` |
| P2 | Run card：config_hash + strategy_hash 落库 | 回测结果表 + `run_card.py` 移植 |
| P3 | `_json_safe()` 统一 API 出口 | `backend/app/api/` 出口层 |
| P3 | 因子健康巡检报告 | `data-cleaner/app/` 新增 |

---

## 附：复现脚本

`E:\Workspace\lab.Quant\_vr_probe.py` — 不联网，假数据，直接调 `BacktestEngine.execute_strategy`。
临时文件，确认修复后可删。
