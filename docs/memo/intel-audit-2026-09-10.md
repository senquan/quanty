# intel 模块整体审计（2026-09-10）

> 审计范围：`data-cleaner/app/intel/`、`backend/app/api/api_v1/endpoints/intel.py`、
> `frontend/apps/web-ele/src/views/data/news-analysis/`、Postgres `intel` schema、
> `docs/memo/intel-module-plan.md` 的 P0–P4 验收项。
> 方法：只读取证（跑测试、查库、读代码），**未修改任何业务代码**。

---

## 0. 一句话结论

**功能链路 P0–P4 全部建成、225 条单测全绿、成本可审计**，但存在 **2 个 P0 级数据正确性缺陷**
（画像超额收益算错、复权价格断供），它们让"历史准确度"和"IC 评估"这两个**最能证明模块价值的指标目前不可信**。
功能完成度 ≈ 95%，**数据可信度 ≈ 60%**。

---

## 1. 完成度总览

| 阶段 | 计划验收项 | 实况 | 判定 |
|---|---|---|---|
| **P0** 骨架 + RSS | 5 源稳定、去重、原文落盘 | 5 源 7 天内 `ok` 440+ 次/源、`failed` 2 次/源；documents 9924、raw 落盘 9995 文件 / 94MB | ✅ 实质达成（未正式出 7 天观察报告） |
| **P1** 理解层 | LLM 抽取 + 预算闸 + span 校验 | llm_runs ok 978 / api_fail 71；doc_mentions 864、doc_style 678、quarantine 91 | ✅ |
| **P1-Gate** | 50 篇人工标注，4 项阈值 | **已真做**：107 条人工标注，symbol 100% / span 100% / 幻觉 0% / stance 100%（报告 `intel-p1-gate-report.md`） | ✅ 硬 gate 通过 |
| **P2** 画像 | 版本化 + 漂移 + 准确度 + 卡片 | `author_profiles` 14 条、`author_style_summaries` 11 条、前端卡片已上线 | ⚠️ 功能在，**数值不可信**（见 D-1） |
| **P3** 因子 | 防前视 + 6 因子 + 消费侧 + 成本复盘 | `factor_values` 2502 行 / 6 因子 / 386 标的 / 防前视违例 0；`consumer.py` 12 条单测 | ⚠️ 因子在，**IC 从未出数**（见 D-2） |
| **P3-4** 消费侧 | 选股/回测读 `intel.factor_values` | `merge_intel_factors` 已就绪，但**尚未接入任何真实策略** | ⚠️ 接口在、未接线 |
| **P3-5** 成本复盘 | 云端 vs 本地决策 | 累计 ¥10.4306，v2 单条 mention ¥0.0131，结论"不切本地"已落 | ✅ |
| **P4-1** 人工投喂 | URL/文件/zip 上传 | Web 拖拽 + zip 自动解压 + `extract=true` 立即抽取 | ✅ |
| **P4-2** 公众号 watch | 目录监听 | `wechat.py` + inbox 监听已实装 | ✅ |
| **P4-3** RSSHub | 默认 disabled | 已实装，默认关闭（符合计划） | ✅ |
| **P4-4** 风格总结 | LLM 作者综述 | 11 条总结（7 ok / 4 skipped），¥0.0232，0 幻觉 | ✅ 但**未并入 19:30 自动化** |
| **19:30 每日构建** | 定时三步链路 | `daily_build.py` + `tasks.py` 已实装，17 条单测 | ⚠️ 需确认运行进程已加载（见 D-4） |

**代码规模**：`app/intel/` 38 个 py 文件 / 5521 行；单测 17 个文件 / **225 passed**（22.06s）。

---

## 2. 数据现状快照（2026-09-10 10:0x）

| 表 | 行数 | 备注 |
|---|---|---|
| `sources` | 7 | 5 RSS(enabled) + 分红养老之路 ×2（**均 disabled**） |
| `documents` | 9924 | 原文落盘 9995 / 94MB |
| `doc_mentions` | 864 | 已抽 808 篇 → **欠账 9116 篇** |
| `doc_style` | 678 | |
| `author_profiles` | 14 | |
| `author_style_summaries` | 11 | |
| `factor_values` | 2502 | trade_date 仅 09-07(1440) / 09-08(660) / 09-10(402) |
| `llm_runs` | 1049 | ok 978 ¥10.4306 / api_fail 71 ¥0 |
| `quarantine` | 91 | span 校验拒收，属正常 |
| `symbol_alias` | 11122 | |
| `feed_health` | 2215 | 近 7 天 ok 440+ 次/源 |

**欠账分布**：东方财富股票 8379 / 虎嗅 398 / 华尔街见闻 161 / 钛媒体 143 / 爱范儿 28 / 分红养老之路 7。

---

## 3. 技术债务清单

### 🔴 P0 — 数据正确性（建议立即修）

#### D-1　画像「20/60 日超额收益」实际算的都是**次日**收益

**位置**：`data-cleaner/app/intel/aggregate/profile.py`

| 行号 | 代码 | 问题 |
|---|---|---|
| **281** | `ORDER BY timestamp ASC LIMIT 1` | 个股端：取提及日之后**第 1 个交易日**，与 `window` 完全无关 |
| **228** | `ORDER BY timestamp ASC LIMIT 2` + 234 行 `rows[-1]/rows[0]` | 基准端：取区间内**最早两条**，即首日与次日，也不是 window 日 |

`EXCESS_WINDOWS = [20, 60]`（第 29 行）在两个 SQL 里都没起作用 → `avg_excess_20d ≡ avg_excess_60d` 恒成立。

**证据 1（库内）**：14 条画像 **全部** `avg_excess_20d == avg_excess_60d`，无一例外
（东方财富 0.65/0.65、散户森 0.33/0.33、脑洞汽车 -0.28/-0.28、郑廷旭 -6.47/-6.47 …）。

**证据 2（真实行情）**：`600674.SH`，提及日 2025-07-02

| 取法 | 日期 | close | 收益 |
|---|---|---|---|
| 现代码（次日） | 2025-07-03 | 15.2857 | — |
| 应为 20 交易日 | 2025-07-30 | 15.4508 | **+1.08%** |
| 应为 60 交易日 | 2025-09-24 | 13.8902 | **-9.14%** |

**60 日真实方向是跌 9%，现代码报的是次日 +0.1%——符号都可能反。**

**波及**：
- `avg_excess_20d` / `avg_excess_60d` / `win_rate_20d` / `win_rate_60d`（直接错）
- `INTL_AUTHOR_CONVICTION` 因子（由 win_rate 派生 → 失真）
- 前端画像卡片「准确度」列（当前只显示 `N 样本·20/60d` 标签，暂不可见，一旦展示具体数字即暴露）

**修复方向**：个股端改 `LIMIT 1 OFFSET :w-1`；基准端取区间首 + 第 w 个交易日（不能用 `LIMIT 2`）。
配套：补单测断言 `avg_excess_20d != avg_excess_60d`，并把 P2 验收项「3 作者 × 10 提及与行情软件手工核对」真正做掉。

---

#### D-2　`raw_bars.hfq_close` 自 2026-09-07 起断供，IC 评估因此永远"待解锁"

```
2026-09-03  rows=5555  hfq=5555  adj=0     ← 正常
2026-09-04  rows=5556  hfq=5555  adj=0     ← 开始缺 1 条
2026-09-07  rows=5557  hfq=   0  adj=0     ← 完全断供
2026-09-08  rows=5557  hfq=   0  adj=0
2026-09-09  rows=1542  hfq=   0  adj=0
```
（`adj_factor` 列**从未填过**，全表 0；`amount` 同理。）

**后果**：
1. `_p3_eval.py` 只用 `hfq_close`（无 close 降级）→ 全表 None → 每个截面 `rmap` 为空 → 输出
   `⏳ 待解锁：无可用截面（跳过 3 个日期，行情未覆盖因子日）`。
   **提示是误导性的**——不是行情没覆盖，是价格列空。
2. `profile.py:267/287` 有 `hfq_close or close` 降级 → 画像改用**未复权 close**，除权日会产生假跳空，
   超额收益进一步失真（与 D-1 叠加）。

**修复方向**：先跑复权口径审计/回填（项目内已有 `quant-adjust-price-audit` 技能可直接用），
再重跑画像 + 因子 + IC。回填完成后 `factor_values` 也应重算。

---

### 🟠 P1 — 功能缺口

#### D-3　~~抽取欠账 9116 篇，且新数据被老欠账饿死~~ **✅ 已处置（2026-09-11）**

`store.py` 的 `docs_for_understanding` 原用 `ORDER BY d.id` **升序**取候选：
RSS 老文档 id 小、分红养老新入库 id 大 → 任何新灌入的语料都排在几千条老欠账之后。

这正是 9/9「上传 200+ 篇却毫无动静」的深层原因（当时靠 `_p4_extract_source.py` 定向 doc_ids 绕过）。
老朱明确要求过的「优先抽取人工投喂的」此前只存在于一次性脚本里。

**已固化为三层排序**（`_understanding_order_sql`，见 §7 处置记录）：
1. `source_type` 优先级（`INTEL_EXTRACT_PRIORITY=manual:10,wechat:20,rss:50`）；
2. 同级内 `available_at DESC NULLS LAST`（先理解新鲜的）；
3. `d.id` 兜底（稳定排序，分页不重不漏）。
置空该配置即退回旧的纯 id 升序（可回滚）。

**顺带修掉一个同源缺陷**：`_file_uri` 用 `as_uri()` 转义方括号/中文（绕开
`urlsplit` 的 Invalid IPv6 URL），但**标题回溯那条路漏了 unquote** → 标题落库成
`%5B2026-09-09-1030%5D%E6%84%9F...`（实测 id=10445）。新增 `_readable_stem()`
统一收口三处调用点，存量 1 条已修正。

#### D-4　~~19:30 定时任务是否在运行进程生效，未确认~~ **✅ 已确认（2026-09-11）**

审计时：`app/intel/tasks.py` 修改于 2026-09-09 16:26，dc 进程 PID 30548 早于它，
跑的可能是**空占位**。审计给的验证命令（只注册不启动，不拉 RSS）：

```bash
cd data-cleaner && INTEL_ENABLED=true .venv/Scripts/python.exe -c "
import app.tasks.scheduler as S; S.register_jobs()
j=S.scheduler.get_job('intel_daily_build'); print(j.func_ref if j else 'NOT REGISTERED')"
```

**已确认在运行进程中生效**，五层取证（见 §8 处置记录）：
1. 代码层：`func_ref = app.intel.tasks:_daily_intel_build_job`，
   `trigger = cron[day_of_week='mon-fri', hour='19', minute='30']`；
2. 进程层：PID 27912 启动于 09-11 09:41:31，**晚于**代码 mtime（09-09 16:26）；
3. 日志层：dc 日志有 `Added job "_daily_intel_build_job"`；
4. **运行进程自报**（最强）：`/qos` 的 `system.scheduler` 报
   `intel_daily_build → app.intel.tasks:_daily_intel_build_job`，
   `next_run = 2026-09-11T19:30:00+08:00`；
5. 干跑编排：`run_daily_build(do_extract=False)` → 画像 ok(14) / 因子 ok(2502 行
   / 386 标的 / 防前视违例 0)、`errors=[]`、成本 ¥0。

**遗留**：审计时那条验证命令只证明「磁盘上的代码对」，**不证明运行进程**——
本次已通过新增的 `/qos` `system.scheduler` 字段补上，以后一眼可查。

#### D-5　IC 评估从未出数（P3 验收项空缺）

文档记为「数据窗口阻塞，已并入每周一自动重跑」，但根因是 D-2。
即便 hfq 补齐，当前因子日只有 09-07/08/10 三个截面、行情到 09-09 → **样本量远不足以谈 IC**。
现状应如实标注为"样本不足，待语料与行情积累"，而不是"待解锁"。

#### D-6　`ingest/web.py` 仍是 `NotImplementedError`

P0-6 计划内冻结的接口（无 RSS 站点单页抓取），非缺陷，但属于**未完工接口**，用到时会直接抛。

---

### 🟡 P2 — 工程与流程

| # | 债务 | 说明 |
|---|---|---|
| D-7 | **`data-cleaner/tests/` 被 .gitignore 整体忽略** | 225 条 intel 测试**不在版本库**（老朱 9/10 决定维持忽略）。风险：换机器/恢复环境后测试全丢 |
| D-8 | P2 验收「3 作者 × 10 提及手工核对」无记录 | 正因为没做，D-1 潜伏至今 |
| D-9 | 画像重算覆盖历史 | `author_profiles` 同版本覆盖，无 `as_of` 版本化 → 历史截面不可复现（计划 P2-2 原意是"版本化不覆盖"，实现偏离）。冻结开关：`INTEL_DAILY_BUILD_PROFILES=false` |
| D-10 | 「分红养老之路」重复源 | id 2151(wechat) + id 3590(manual)，**均 `enabled=False`**，224 篇挂在 3590。源名重复易混淆 |
| D-11 | backend 数据读取双轨 | `/mentions`、`/profiles` **直连 Postgres**；`/upload` **转发 dc**。同一份数据两条路径 |
| D-12 | P4-4 风格总结未自动化 | 仍需手动 `_p4_build_style_summaries.py`，未并入 19:30 |
| D-13 | 一次性脚本散落 | `_p1_gate_eval.py`、`_p2_*`、`_p3_*`、`_p4_*`、`_verify_*` 等在 dc 根目录（部分已进 gitignore） |
| D-14 | ~~文档未同步最新数字~~ **✅ 已处置（2026-09-11）** | 计划文档 §10 原为 5359 篇 / 1764 行 / ¥6.98 的旧快照；已按重跑后的真实数字同步为 **13446 篇 / 864 mention / 2502 因子行 / ¥10.4306**（详见 §6 重跑记录） |

---

## 4. 建议处置顺序

| 序 | 事项 | 理由 | 成本 |
|---|---|---|---|
| 1 | **修 D-1**（20/60d 收益窗口） | 数值错误，直接影响模块可信度 | 小（改 2 处 SQL + 补单测），零 LLM 成本 |
| 2 | **修 D-2**（hfq 回填） | 阻塞 IC，且污染所有收益计算 | 中（需跑复权回填） |
| 3 | ~~重跑画像 + 因子 + IC，更新文档数字（D-14）~~ **✅ 已完成（2026-09-11）** | 修完 1/2 必须重算 | 零 LLM（画像/因子不花钱） |
| 4 | ~~**固化 D-3 抽取优先级**~~ **✅ 已完成（2026-09-11）** | 老朱明确要求过 | 小 |
| 5 | ~~确认 D-4 定时任务生效（必要时重启 dc）~~ **✅ 已确认（2026-09-11）** | 否则每日链路是空的 | 极小 |
| 6 | D-10 合并重复源、D-11 统一读取路径 | 卫生问题 | 小 |
| 7 | D-9 画像版本化（按需） | 只在需要严格回测复现时做 | 中 |

**不建议现在做**：新因子、新数据源、P4-4 自动化——先把已有数字修对，再谈扩张。

---

## 5. 审计方法备注

- 全部为只读操作；唯一有副作用的是跑 `_p3_eval.py`（只读查询）与 `pytest`（用测试库夹具）。
- D-1 判定依据：代码行 + 库内 14/14 画像 `e20 == e60` + 真实行情三点对照，非推测。
- D-2 判定依据：按日统计 `hfq_close` 非空率，定位到 09-07 断供边界。

---

## 6. D-14 处置记录：重跑画像 + 因子 + IC（2026-09-11 06:45）

**触发**：D-1（画像窗口失效）与 D-2（hfq 断供）修完后，历史画像与因子都是旧口径算出来的，
必须重算；顺带把文档里引用旧快照的数字（D-14）同步掉。

**执行链路**（零 LLM 成本 —— 画像与因子都不调模型）：

```bash
cd E:/Workspace/lab.Quant/data-cleaner
.venv/Scripts/python.exe _p2_build_profiles.py --skip-migration
.venv/Scripts/python.exe _p3_build_factors.py
.venv/Scripts/python.exe _p3_eval.py
```

**数字变化对照**

| 指标 | 重跑前 | 重跑后 | 说明 |
|---|---|---|---|
| 文档总数 | 13439 | **13446** | 期间 RSS 继续入库（+7） |
| 原文 / 转载 | — | **12926 / 520** | — |
| mention 总数 | 864 | **864** | 不变（未跑抽取） |
| 覆盖文档 / 标的 | — | **458 / 390** | — |
| quarantine | — | 91 | — |
| 画像数 | 14 | **14** | — |
| 因子行数 | 2502 | **2502** | 386 标的 × 3 交易日 |
| 累计 LLM 成本 | ¥10.4306 | **¥10.4306** | 重跑本身不产生成本 |

**画像修正后（D-1 生效）**

| 画像 | excess_20d | excess_60d | acc_n |
|---|---|---|---|
| 散户森（唯一样本充足） | **+1.04%** | **+4.28%** | 317 |

- 两值不再相等（旧口径下恒等）；`20d` 由前次 `+1.17%` 微调至 `+1.04%`、`acc_n` 315 → 317，
  原因是 09-10 行情补齐后可用交易日增加，属正常的数据推进。
- 其余 13 个画像全部 `sample_insufficient`（提及日集中在 09-03 ~ 09-09，距今不足
  20 个交易日）→ 如实置 NULL，不再输出假的「次日收益」。

**IC 复算（w=1 六因子全部出数）**

| 因子 | IC(w=1) | 截面数 |
|---|---|---|
| INTL_FIRST_MENTION | **+0.1863** | 1 |
| INTL_AUTHOR_CONVICTION | +0.0218 | 2 |
| INTL_SENTIMENT_10 | +0.0082 | 2 |
| INTL_STYLE_MATCH | -0.0233 | 2 |
| INTL_RESONANCE_5 | -0.0405 | 2 |
| INTL_MENTION_HEAT_5 | -0.1598 | 2 |

- w=5 / w=20 仍「待解锁」——**这是真实的数据窗口限制**：因子日只有 09-07 ~ 09-10，
  算 w=5 需要 09-14 的行情（09-11 尚未收盘）。判别依据：修复前 **w=1 都出不了数**
  （价格列全空导致），现在 w=1 全部出数 ⇒ 价格链路已通。
- **无 hfq 兜底告警**（`_p3_eval.py` 未打印「N 只标的用 close 兜底」）⇒ 全库 hfq 完整。

**副产物发现**

| # | 发现 | 定性 |
|---|---|---|
| a | `api_fail` 由 6 条涨至 **141 条**（v2） | **非缺陷**。全部集中于 09-10 19:30~20:14 的 `intel_daily_build`，错误为 `ConnectTimeout WinError 10060` (89 条) 与 `HTTP 502` (46 条) —— 内网 vLLM (192.168.14.88:8000) 不可达，即老朱出差上海连不上办公室内网的已知问题。`api_fail` 一律 ¥0，无成本损失 |
| b | `INTL_AUTHOR_CONVICTION` mean 由 `0.5683` 变为 **`0.8881`** | **D-9 的实证**。画像同版本覆盖 → 每日重算会改写历史因子行。本轮重跑同样把 09-07/09-08 的因子行按今天的画像重写了。需要严格回测复现时必须先做 D-9 的版本化 |

**文档同步**：`intel-module-plan.md` §10 验收表与「已验证 vs 待验证」段已更新为最新口径，
旧快照（5359 / 1764 / ¥6.98）保留为历史引用并加注说明，不删（可追溯当时状态）。

---

## 7. D-3 处置记录：抽取优先级固化（2026-09-11）

**问题定性**：`docs_for_understanding` 用 `ORDER BY d.id` 升序，把「入库先后」当成了
「理解优先级」。而 id 大小只反映抓取顺序 —— RSS 老欠账 id 小，永远先抽；用户主动
投喂的公众号/人工文章 id 大，排在队尾。**实测欠账 12814 篇**（比审计时的 9116 又涨了）：

| source_type | 源 | 待抽取 | 总计 | 备注 |
|---|---|---|---|---|
| rss | 东方财富股票 | **11853** | 12312 | id 最小，旧实现下永远占满前 N 条 |
| rss | 虎嗅 | 529 | 579 | |
| rss | 华尔街见闻 | 221 | 271 | |
| rss | 钛媒体 | 172 | 189 | |
| rss | 爱范儿 | 32 | 47 | |
| **manual** | **分红养老之路** | **7** | 224 | id 最大，旧实现下永不入选 |
| wechat | 分红养老之路 | 0 | 3 | |

**改动**：三层排序抽成纯函数 `_understanding_order_sql(prio)`（便于白盒单测）。

```python
# app/intel/store.py
ORDER BY CASE LOWER(s.source_type)
             WHEN :p_manual THEN 10
             WHEN :p_wechat THEN 20
             WHEN :p_rss    THEN 50
             ELSE 100            -- 未列出的类型沉底
         END,
         d.available_at DESC NULLS LAST,   -- 同级先理解新鲜的
         d.id                              -- 稳定兜底（分页不重不漏）
```

形参来源：`INTEL_EXTRACT_PRIORITY`（dc `.env`，默认 `manual:10,wechat:20,rss:50`）。

**四个设计决定及理由**

| 决定 | 理由 |
|---|---|
| 用 `source_type` 而非新加 `priority` 列 | 不改表结构、无需迁移；分类维度已经存在且语义清晰（谁把内容送进来的） |
| 同级用 `available_at DESC` 而非 `ingested_at` | `available_at` 是**内容本身**的时间（可防前视），`ingested_at` 只是入库时刻；且它已被防前视链路信任 |
| `NULLS LAST` | RSS 源常不给发布时间（`available_at` 为 NULL）。PG 的 `DESC` 默认 `NULLS FIRST`，不显式声明会把「时间未知」的顶到同组最前，抢掉新鲜内容的位置 |
| 「未知类型」`ELSE 100` 而非与 rss 同级 | 新增源类型时**默认沉底**（宁可漏抽也不要挤掉已知重要内容），发现后可显式配优先级 |

**保留 `doc_ids` 定向路径**：上传后 `extract=true` 立即抽取走这条，只抽这次上传的那批，
不被全局优先级重排、也不混入历史欠账。行为与改动前一致。

**测试**：新增 `tests/intel/test_extract_priority.py`（**11 条**）——
- 纯函数层：配置解析（默认/空格/坏值/空串）、SQL 三层顺序、空优先级回滚、
  key 小写化与参数化（防注入）；
- 真实库层：**首条候选不再是最小 id 的 RSS 老欠账**、桶间顺序符合优先级、
  `doc_ids` 定向不越界、`limit` 生效。

**顺带修掉的同源缺陷（标题百分号编码）**

`_file_uri` 用 `Path.as_uri()` 转义方括号与中文（为绕开 `urlsplit` 的
`Invalid IPv6 URL`），但**标题回溯那条路没跟着 unquote** ——
`Path(url).stem` 直接把 `%5B...%5D%E6%84%9F...` 当 title 落库。
这是 9/10 那次修复的漏网之鱼：改了 URL 主体，漏了标题。

| 项 | 内容 |
|---|---|
| 新增 | `app/intel/ingest/manual.py::_readable_stem()` —— 统一收口 `file://` URI / Path / 纯文件名三种形态 |
| 替换调用点 | `_parse_file_multi`（纯文本分支）、`_parse_plain`、`_parse_html_bytes` 共 3 处 |
| 存量修正 | id=10445 的标题由 `%5B2026-09-09-1030%5D%E6%84%9F%E6%81%A9...` 还原为 `[2026-09-09-1030]感恩长鑫打新收益换两家优质红利`；全库复查编码残留 **0** |
| 测试 | `test_manual_ingest.py` 新增 3 条（还原正确性 / 多形态 / 纯文本路径） |

**`_p4_extract_source.py` 改造**：不再是硬编码「分红养老之路」的一次性脚本，
改为 `--source <源名>` 通用定向工具，保留 `--limit` / `--dry-run`。
日常轮次已不需要它（优先级固化后自动按序抽），用途退化为「单独补齐某个源」。

---

## 8. D-4 处置记录：定时任务在运行进程生效的确认（2026-09-11）

**问题**：审计只能证伪不能证实 —— `app/intel/tasks.py` mtime 是 09-09 16:26，
而当时 dc 进程 PID 30548 早于它。若进程加载的是旧代码，19:30 跑的仍是
`_daily_intel_build_placeholder`（只打日志的空占位），**整条每日链路等于空的**。

**为什么要分五层**：单靠任一层都不够 ——

| 层 | 手段 | 为什么不够（单独用时） |
|---|---|---|
| L1 | 新进程 `import` + `register_jobs()` 看 `func_ref` | 只证明**磁盘上的代码**对，与运行进程无关 |
| L2 | 进程启动时刻 vs 代码 mtime | 只是**推断**；进程可能热重载，或 mtime 被 touch |
| L3 | dc 日志里的 `Added job "_daily_intel_build_job"` | **重启会覆盖日志**（实测踩到，见下） |
| L4 | **运行进程 `/qos` 自报 job 列表** | ✅ 最强：进程内 APScheduler 实例的真实状态 |
| L5 | 干跑 `run_daily_build(do_extract=False)` | 证明**链路能跑通**，不只是注册了 |

**结论：全部通过。**

```
L1  func_ref=app.intel.tasks:_daily_intel_build_job  cron[mon-fri 19:30]
L2  PID=27912 启动=2026-09-11 09:41:31 (CST)  代码 mtime=2026-09-09 16:26:42
L3  日志含 Added job "_daily_intel_build_job"
L4  运行进程报 intel_daily_build → app.intel.tasks:_daily_intel_build_job
    next_run = 2026-09-11T19:30:00+08:00（共 8 个 job）
L5  抽取[skipped]｜画像[ok] 14 个｜因子[ok] 2502 行 386 标的 违例=0
    ERRORS=[]  COST=¥0.0000
```

### 改动：`/qos` 暴露 `system.scheduler`（让进程能自报）

`app/core/qos.py` 新增 `_scheduler_state()`，把运行进程的调度器状态并进
`system` 字段：

```jsonc
"scheduler": {
  "enabled": true, "running": true, "job_count": 8,
  "jobs": [
    {"id": "intel_daily_build",
     "func_ref": "app.intel.tasks:_daily_intel_build_job",
     "next_run": "2026-09-11T19:30:00+08:00"},
    ...
  ]
}
```

以后判断「某任务是否真的在跑」只需 `curl :8100/api/v1/qos | jq .system.scheduler`，
不必再翻日志或比 mtime。`func_ref` 一眼区分真实现与空占位。

### 顺手做成的验收工具：`_verify_d4_task.py`

五层取证一键跑（只读 + 可选干跑，零 LLM 成本）：

```bash
cd data-cleaner
./.venv/Scripts/python.exe _verify_d4_task.py                    # L1~L4
./.venv/Scripts/python.exe _verify_d4_task.py --dry-run-pipeline # 再加 L5
```

### 排查中踩到的三个坑（都写进了脚本注释）

1. **Windows API 的 FILETIME 是 UTC**：`GetProcessTimes` 拿到的启动时刻直接与本地
   mtime 比会**差 8 小时**，差点把「进程晚于代码」误判成「早于」。
   （当时算得「10.85 小时前」正是 UTC 22:28 = CST 06:28。）
2. **dc 日志会被重启覆盖**：同一路径被 `>` 重新打开，启动段那几十行消失。
   L3 因此**不能作为判据**，只作兜底 —— 这就是必须加 L4 的原因。
3. **中文 Windows 两种编码别混**：系统命令（`netstat`）stdout 是 **GBK**；
   而自己起的 python 子进程 stdout 是 **UTF-8**。用 GBK 解后者会把
   `抽取[skipped]` 变乱码，断言静默失效（表现为「数据明明对，判定却不过」）。

另注：审计报告给的原验证命令仍有效，但只覆盖 L1。
