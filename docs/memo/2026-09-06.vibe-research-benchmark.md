# Vibe-Research 借鉴分析

- 分析日期：2026-09-06
- 参照项目：`E:\Workspace\lab.Quant\docs\repos\Vibe-Research`（v1.0.4，MIT）
- 现有项目：`E:\Workspace\lab.Quant`（Quanty）
- 结论摘要：**有 5 点值得借鉴；P0、P1 已于 2026-09-06 完成（80 项测试全通过）。**

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

## 三、P0 已修复：Quanty 回测引擎曾产出的全是「总收益 0.00%」

> **状态：2026-09-06 修复完成，27 项测试全通过。**

### 3.1 根因一：成交时间记成了「现在」

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

→ **修复前线上跑出来的所有回测结果都是「总收益 0.00%」。**

### 3.2 根因二：`def on_data` 从未被调用

前端模板（`frontend\apps\web-ele\src\views\quant\strategy\edit.vue:56/81/103`）生成的策略一律是
`def on_data(data, context):` 形式，而引擎 `:87` 只做了 `exec(strategy_code, strategy_globals)`——
**函数定义了，却从没有人调用它**。

实测：照搬前端「双均线交叉策略」模板 → **交易笔数 0、收益 0.00%**。
即：**凡是用前端模板创建的策略，回测结果恒为 0 笔交易。**

### 3.3 修复方案

改成两遍执行：

```text
第一遍  跑策略代码，只收集买/卖信号
        ├─ 模块级写法  → exec 时即执行（保持兼容）
        └─ on_data 写法 → 显式调用一次（兼容 0/1/2 个参数）
        同时实时维护 position / capital，让策略里的 get_position() 拿到那一刻的持仓

第二遍  把每个信号绑定到具体 bar，再逐 bar 撮合
        ├─ ① 显式指定：buy(price, qty, bar=i)
        └─ ② 价格前向匹配：从上一笔的 bar 往后找收盘价等于成交价的第一根 bar
           策略几乎总是写 buy(close.iloc[i])，所以这个匹配是精确的
        成交时间 = 该 bar 的 index，组合价值 = 现金 + 持仓 × 当日收盘价
```

关键取舍：**没有改成事件驱动**。存量策略是批式写法（自己 `for` 循环全历史），
改成逐 bar 回调会让现有策略全部失效。两遍法在保持兼容的前提下拿到了正确的 bar 时间。

### 3.4 顺带修复

| 位置 | 问题 | 处理 |
|---|---|---|
| `:179-181` | `win_rate` = 卖出笔数 / 总笔数，不是盈利比例 | 改为 FIFO 配对算真实盈亏；未平仓不计入分母 |
| `:176` | 总收益只看现金，期末仍持仓时会严重低估 | 改用组合价值（现金 + 持仓市值） |
| `:264` | 安全检测是字符串 `in` 判断，`import os` 拦不住 | 改为 AST 检测：禁模块黑名单 + 禁内置名；注释里提到 `open` 不再误报 |
| `performance_analyzer.py:167` | FIFO 配对时**就地修改**了传入的 trades 的 quantity，同一批数据跑第二次结果就变了 | 改为 `[dict(b) for b in buy_trades]` 复制后再扣减 |

### 3.5 结果自带前提声明

`execute_strategy()` 现在返回 `warnings`，回测结果不再是一堆没有语境的数字：

```
撮合假设：信号在发出的当根 bar 按收盘价成交；不计手续费、印花税与滑点，
         无涨跌停限制，允许当日回转（T+0）
其中 7 笔信号的成交时间由收盘价匹配推断；如需精确指定，
         写成 buy(price, qty, bar=i) / sell(price, qty, bar=i)
```

（这条学自 Vibe-Research `gate.py:268-276` 的 `Plan.limits`——限制必须随结果一起呈现。）

### 3.6 验收

| 项 | 修复前 | 修复后 |
|---|---|---|
| Case A 模块级策略 | 7 笔信号，组合价值全程不变，收益 0.00% | 成交时间 2023-02-22→11-01，收益 **+153.82%** |
| Case B `def on_data`（前端模板） | **0 笔交易，收益 0.00%** | 7 笔交易，收益 **-51.39%** |
| 成交时间落在回测区间内 | False | **True** |
| 组合价值随持仓变动 | False（唯一值 1 个） | **True**（唯一值 97 / 100 个） |

健全性检查（已知答案的场景）：单调上涨 + 首日全仓买入 → 收益 ≈ +50%（价格涨幅）；
单调下跌 → ≈ -33.33%。

**回归测试**：`backend\tests\test_backtest_engine.py`，27 项全通过。
```
cd backend && .venv/Scripts/python.exe -m pytest tests/test_backtest_engine.py -q
```

### 同文件遗留问题（不在 P0 / P1 范围）

| 行号 | 问题 |
|---|---|
| 引擎 | 策略一次性看到全量 data 才能算指标 → 仍是**未来函数**结构（P1 遗留，需改事件驱动 + 存量策略迁移） |
| `performance_analyzer.py:203` | 无亏损时 `profit_factor` 返回 `float('inf')`，会产生非法 JSON（P3） |

---

## 三之二、P1 已完成：闸口 + 市场规则（2026-09-06）

### 新增文件

| 文件 | 职责 |
|---|---|
| `backend\app\services\market_rules.py` | **市场规则表**——限制的唯一事实来源。闸口、引擎、报告三处共用，避免各写一遍对不上 |
| `backend\app\services\backtest_gate.py` | 闸口本体 `plan_backtest()` → `Plan` \| `Refusal` |
| `backend\tests\test_market_rules.py` | 53 项测试（代码分类、费率、闸口放行/拦截、引擎执行） |

### 市场规则表（`market_rules.py`）

| 市场 | T+0/1 | 做空 | 最小单位 | 涨跌停 | 费用 |
|---|---|---|---|---|---|
| A股 | T+1 | ✗ | 100 股整手 | 主板 ±10% / 创业板·科创板 ±20% / 北交所 ±30% | 佣金万2.5（最低5元）+ 过户费万0.1 双边 + 印花税万5 卖出单边 |
| 港股 | T+0 | ✓ | 100 股 | — | 佣金万5 + 印花税 0.1% 双边 |
| 美股 | T+0 | ✓ | 碎股 | — | 零佣金 |
| 加密 | T+0 | ✓ | 碎股 | — | taker 0.1% |

代码 → 市场：`600519.SH` / `000001.SZ` / `600519` → A股；`00700.HK` → 港股；`AAPL` → 美股；`BTC/USDT` → 加密。认不出返回 `None`，由闸口转成一句说得清的拒绝。

### 闸口拦住什么

| 场景 | 拒绝理由 |
|---|---|
| 认不出代码 | 认不出属于哪个市场 |
| 加密配 yahoo 源 / 股票配 crypto 源 | 数据源与市场对不上（取数会成功但拿到空表） |
| swing 只给半年 | 至少约 240 根，这个区间约 130 根 |
| long 只给两年 | 至少约 480 根 |
| 做 T | 要分钟级 bar，当前只有日线 |
| A股做空 | 不能做空，测出来的是「只做多」的策略 |
| 起始资金 1000（A股） | 一手都买不起，会得到一份「总收益 0.00%」的报告 |
| 区间反了 / 资金非有限正数 / 口径不认识 | 各自说清 + 补救建议 |

放行时 `Plan.limits` 带 6 条限制说明（交易机制、做空、最小单位、费用、币种、涨跌停），经 `quant.py` 一起回给前端。

**HTTP 422 而不是 500**——这是「请求不成立」，不是服务端出错。响应体 `{reason, remedy}`。

### 引擎执行（`backtest_engine.py`）

持仓从标量改成**带买入 bar 的批次队列**——T+1 要能回答「这批是不是今天买的」，标量做不到。

- **T+1**：FIFO 平仓时跳过 `bar == 当日` 的批次；无可卖批次则拒单
- **涨跌停**：按**前收**算封板价（`round(prev_close × (1±limit), 2)`），封涨停不成交买单，封跌停不成交卖单
- **整手**：A股买单调到 100 的整数倍；不足一手**明确拒单**而不是静默丢弃
- **费用**：`成交额 + 费用 ≤ 可用资金` 才成交，付不起手续费时缩量而非整笔丢弃
- **拒单可见**：被挡下的信号进 `rejections`，并汇总进 `warnings`

> 一条实现上的取舍：不足一手的买单在第一遍收集信号时如果直接 `return`，这笔单子会无声无息消失，报告描述的就是「删掉它之后的策略」。所以仍然记为信号交给第二遍明确拒单，只是不占用资金与持仓。

### 验收

| 场景 | 结果 |
|---|---|
| 闸口 8 个用例 | 6 拦 2 放，全部符合预期 |
| 同一策略 + 同一数据（6 年 1565 根） | 无规则 **+26.93%** / A股 **+20.64%**（费用 ¥77,371）/ 美股 +26.93% |
| 拒单场景 5 个 | T+1 拦、次日放、涨停拦买、跌停拦卖、不足一手拦 —— 全部触发 |

美股与「无规则」一致是符合预期的：零佣金 + 碎股 + T+0。

### ⚠️ 行为变更（前端已适配）

`BacktestRequest` 新增 `style`（默认 `swing`）、`allow_short`、`apply_market_rules`。
**默认 swing 要求约 240 根 bar**，此前习惯跑半年区间的回测现在会收到 422。这是闸口的本意。

### 前端适配（同日完成）

改造前 `runBacktestApi`（`#/api/quant`）**没有任何调用方** ——
`views/quant/backtest/index.vue` 是纯 mock 页面（假净值、假指标、假历史），加个下拉框到假页面上毫无意义，所以一起接了真 API。

| 文件 | 改动 |
|---|---|
| `api/quant.ts` | 新增 `BACKTEST_STYLES` / `DATA_SOURCES` 常量（与后端 `gate.STYLES`、`DataManager.sources` 对齐）；`BacktestResult` 类型**重写**——原类型（`strategy_name` / `annualized_return` / `equity_curve`）与后端返回完全对不上；新增 `BacktestRefusal` / `RejectedSignal` / `BacktestHistoryItem` / `parseBacktestRefusal()` |
| `views/quant/backtest/index.vue` | 真接 API；口径下拉 + 口径说明；闸口 422 → 常驻 `ElAlert` 显示 reason + remedy；限制 / 警告 / 拒单三个面板；净值曲线用真实 `portfolio_dates` + `portfolio_values`；历史记录接真接口 |

顺带修的三处：

1. **数据源下拉的 `ccxt` 后端根本不认** —— `DataManager.sources` 只有 `yahoo` / `crypto`，选 CCXT 必然 400。已改为 `crypto`。
2. **组合价值曲线没有日期，画不出来** —— 后端补 `portfolio_dates`（与 `portfolio_values` 等长），此前只有一串浮点数。
3. **导出按钮是空壳**（`@click="() => {}"`） —— 改为导出该行指标 JSON。后端只存指标不存逐笔，所以不假装能还原完整回测。

> 实现坑：图表在 `v-else` 里，`result` 赋值后同步调 `renderResult()` 时 DOM 还没挂载，`chartRef` 为空 → 曲线不显示。必须 `await nextTick()`。

前端口径下拉**只显示说明（如「需约 240 根日线」），不做判断** —— 判断是后端闸口的事，前端再复制一份规则就是三处事实来源。

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

| 优先级 | 事项 | 涉及文件 | 状态 |
|---|---|---|---|
| P0 | 修回测撮合（bar 时间误用 now） | `backtest_engine.py` 重写 | ✅ 已完成 |
| P0 | 修 `on_data` 从未被调用 | `backtest_engine.py` | ✅ 已完成 |
| P0 | 修 `win_rate` 口径（FIFO 配对） | `backtest_engine.py` | ✅ 已完成 |
| P0 | 安全校验改 AST | `backtest_engine.py` | ✅ 已完成 |
| P0 | 回测结果自带前提声明 | `backtest_engine.py` warnings | ✅ 已完成 |
| P1 | 加回测闸口 `plan_backtest()`（Plan / Refusal） | `backtest_gate.py` + `quant.py:231` 前 | ✅ 已完成 |
| P1 | 补市场规则：涨跌停（按前收）、T+1、费用、整手 | `market_rules.py` + 引擎撮合 | ✅ 已完成 |
| P1 | 策略执行改事件驱动，消除未来函数 | `backtest_engine.py` + 存量策略迁移 | 待做 |
| P1 | 前端暴露 `style` 选择 + 展示 limits / warnings | `api/quant.ts`、`views/quant/backtest/index.vue` | ✅ 已完成 |
| P2 | 移植 `validation.py` 三件套（MC / Bootstrap / WF） | 新增 `backend/app/services/backtest_validation.py` | 待做 |
| P2 | Run card：config_hash + strategy_hash 落库 | 回测结果表 + `run_card.py` 移植 | 待做 |
| P3 | `_json_safe()` 统一 API 出口（含 `profit_factor=inf`） | `backend/app/api/` 出口层 | 待做 |
| P3 | 因子健康巡检报告 | `data-cleaner/app/` 新增 | 待做 |

## 附：本次改动文件

| 文件 | 改动 |
|---|---|
| `backend\app\services\backtest_engine.py` | 重写执行与撮合（+453 / -177） |
| `backend\app\services\performance_analyzer.py` | 1 处：FIFO 配对不再就地修改传入 trades |
| `backend\tests\test_backtest_engine.py` | 新增，27 项回归测试 |
| `_vr_probe.py` | 端到端验收脚本（不联网，假数据，两个 case：模块级 / `def on_data`） |

运行方式：

```bash
# 回归测试
cd backend && .venv/Scripts/python.exe -m pytest tests/test_backtest_engine.py -q

# 端到端验收
PYTHONUTF8=1 D:/Python/Python312/python.exe _vr_probe.py
```
