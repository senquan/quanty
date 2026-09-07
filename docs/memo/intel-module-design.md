# 市场情报模块设计（Market Intel）

- 设计日期：2026-09-07
- 参照项目：`E:\Workspace\lab.Quant\docs\repos\Vibe-Research`（v1.0.4，MIT）
- 现役模块：`E:\Workspace\lab.Quant\data-cleaner`（行情/因子/回测，下称 **dc**）
- 新模块落位：`E:\Workspace\lab.Quant\data-cleaner\app\intel`（dc 代码树内 flag-gated 模块，PG schema `intel`）
- 状态：**设计稿，未开工**。本文只做分析与设计，不含实现。

---

## 0. 一句话定位

> **dc 管"价格是什么"，intel 管"市场在说什么"。两者在"情报因子"这一层汇合，且汇合点必须带时点。**

dc 已有的 `SENT_*` 因子（换手率、量比）本质是**价格的衍生物**——它描述交易行为，不描述人在想什么。
intel 补的是**文本侧**：新闻、公告解读、公众号观点、产业链传闻、卖方口径变化。

它不是"另类数据"这个筐，它有明确的产品目标：

1. 把非结构化文本变成**可回溯、可复现、可回测**的结构化信号；
2. 从中提炼**作者/信源维度的风格画像**，回答"这个号值得跟吗、跟它的什么"；
3. 输出情报因子，喂回 dc 的因子选股与回测（§6）。

---

## 1. 为什么是 dc 内的可选模块（flag-gated submodule），不是独立部署单元

用户要求"两模块可以选择部署"。原设计稿（2026-09-07 初版）据此写成"独立目录 `intel/` + 独立镜像 + 独立 compose profile"，**该结论基于不完整的事实，现纠正**。

### 决定性事实（初版未纳入）

1. **dc 是因子的唯一真相源（SoT）**，而 dc→backend 的 **WebSocket 长连接是因子副本同步的根基通道**（`event.factor.updated`，见 `data-cleaner/app/ws/` 与 `docs/plans/2026-09-04.ws-dc-backend.md`）。backend 的 `factor_registry` 只是副本，靠这条 WS 通知触发增量同步 + 每日对账。
2. **intel 的产出（`INTL_*` 因子）本身就是 dc 因子世界的一等公民**：要进 backend 因子副本、要在前端因子库与回测里和传统 `MOM_20` / `SENT_*` 并列查看。所以 intel 不是"独立系统顺带复用 dc 的库"，而是"往 dc 因子工厂里加一条新的生产路径"。这是**子模块**关系，不是对等关系。

因此：intel 落位为 `data-cleaner/app/intel/`（dc 代码树内模块），用 `INTEL_ENABLED` 开关控制启用——与 dc 已有的 `WS_ENABLED` 完全同构（默认 false，关闭时 dc 行为零变化）。"可选部署"由**同一个二进制内的功能开关**实现，无需独立容器 / 镜像 / compose profile。

### 复用 dc 既有基础设施（独立部署要重写的部分）

| 复用项 | 现有实现 | intel 怎么用 |
|---|---|---|
| **WS → backend（因子副本同步）** | `app/ws/events.py::emit_factor_updated(codes, reason)` + `WSClient`（outbox / ack 重连 / service_code 注册） | 产出 INTL_* 后**一行** `ws_events.emit_factor_updated(["INTL_STYLE_MATCH"], reason="intel_build")`，复用同一条可靠推送通道；沿用 dc 的 `WS_SERVICE_CODE`，因子直接进入同一份 `factor_registry` 副本 |
| 配置 | `app/core/config.py::Settings` | 直接读同一 `settings`，仅增 `INTEL_*` 项 |
| DB 引擎 + 迁移 | `apply_migrations()`（lifespan 内调用） | `intel.*` schema 在同一迁移批次里一起建，无需独立迁移器 |
| 调度 | APScheduler（`scheduler.py` 已 cron 盘后 + interval 心跳混跑） | intel 准实时 RSS 轮询 = 再加几个 `interval` 任务；节奏混跑是既成事实，非障碍 |
| backend 内部调用 | `BACKEND_BASE_URL` + `STRATEGY_INTERNAL_TOKEN` | 复用同一套鉴权调 backend 内部端点 |
| 镜像 / 部署 / 可观测 | 同一 Dockerfile、logging、metrics、healthcheck | 同一镜像，`INTEL_ENABLED` 控制；无需第二套运维 |

> 独立部署的代价：重写 `client.py`+`outbox`+`protocol`（~500 行，且与 backend 逐字一致的契约测试要再维护一份），backend 还要为 intel 再登记一类 WS 连接（`service_code` / 注册表分支）。纯重复劳动。

### 原"5 条差异"在 colocation 下如何被消化（concern 保留，给 mitigation）

| 原 concern（初版"拆"的理由） | colocation 下的处置 |
|---|---|
| 依赖重量（LLM/向量库 与 pandas/numpy 锁版本冲突） | **懒加载 import**——intel 重型依赖仅在 `INTEL_ENABLED=True` 且被调用时 import；`requirements` 用 extras（`requirements-intel.txt`）；`INTEL_ENABLED=False` 时基础镜像不装重依赖，dc 镜像保持精简 |
| 调度节奏（准实时 vs 盘后定时） | 已 moot：scheduler 本就混跑双节奏；intel 任务跑在 `run_in_executor` 带超时（与 EOD 流水线同款隔离），不阻塞盘后入库 |
| 故障域（爬虫封禁 / LLM 超预算 不应拖垮行情） | intel 的 LLM/抓取全部 `run_in_executor` + 超时 + 异常吞掉；WS/HTTP 推送本就是尽力而为（绝不抛异常、绝不阻塞）。intel 崩不影响 dc 主循环 |
| 数据可变性（情报可修正、需版本化/重跑） | 由 `intel.*` schema **DB 层隔离**承载（与进程无关）；`doc_mentions`/`author_profiles` 带 `llm_version` + `computed_at` 版本化，不覆盖 |
| 合规边界（第三方内容 / LLM 条款 / 抓取礼仪） | 由模块内 retention/access 策略承载；"一键下线"= 关 `INTEL_ENABLED`，语义等价于原独立容器停服 |

### 唯一仍倾向独立的场景（留个后路）

若未来 intel 与 dc 的**水平扩展画像严重分化**（如要 N 个 intel worker + 1 个 dc worker，或 intel 需 GPU / 独立资源类），可把 `app/ws/` 抽成内部共享包、intel 独立成服务——但这是未来扩展问题，**不前置**。当前正确做法是先 colocate，需要再抽。

### colocation 下仍共享 / 仍隔离什么

| 关系 | 方式 | 边界 |
|---|---|---|
| PostgreSQL | 同实例，`intel` schema 与 `factor` schema 并列 | intel **只读** `factor.raw_bars` / `factor.stock_info`，**绝不写** |
| 标的空间 | 复用 `factor.stock_info`（symbol / name / industry） | 别名表挂 `intel.symbol_alias`，可人工补 |
| 回测 | intel 只产因子值，回测仍在 dc（`app/strategy/engine.py:677`） | intel 不实现撮合 |
| 前端 | 同一套（新增情报页面） | 经 backend 网关转发，不直连 intel |

---

## 2. 从 Vibe-Research 借什么（以及不借什么）

Vibe-Research 是**投研工作台**（LLM 驱动个股研究），跟我们要建的**情报流水线**不是一回事。
但它的工程纪律几乎可以原样搬。逐条对照：

### 2.1 借（这些是"让结果可信"的东西）

| Vibe-Research 的做法 | 出处 | 落到 intel |
|---|---|---|
| **原文不可变落盘**，每条证据带 `raw_ref` + sha256 | `AGENTS.md §4` 磁盘契约 | `intel.documents.raw_path` + `content_hash`。**原文永不改写**，清洗产物另存 |
| **事实 vs 推断分离**：evidence（原文摘录）与 calculation（计算/推断）分开存、分开引用 | §4 `evidence.json` / `calculations.json` | `doc_mentions.stance`（LLM 推断）**必须带 `span_start/span_end`**（原文位置）。没 span 的推断不予入库 |
| **禁止凭记忆生成数字**：每个事实数字必须来自本次取数，取不到写"未获取" | §0.1 三条不可越线 | LLM 抽取的**任何数字**（目标价、产能、市占率）必须带 span；抽不到写 `null`，**不许编** |
| **确定性计算与 LLM 分离**：数字由 `calc/` 算，LLM 只做抽取与归纳 | §0.2 / §3 | 涨跌幅、超额收益**只从 `factor.raw_bars` 算**；LLM 只产分类/打分/文本 |
| **先用确定性筛子，再上 LLM** | `datasources/chokepoint_keywords.json`（涨价/扩产/订单/减持…含 `re:` 正则与 negatives） | `understand/prescreen.py`：标的代码正则 + 别名表 + 事件关键词，**零 LLM 成本**且可单测复现。没命中的文章不送 LLM |
| **数据源健康巡检**：逐端点实跑，出 ok/partial/failed 与耗时 | `datasources/health.py`（117 端点 → `health_report.md`） | `intel.feed_health` 表 + `GET /intel/health`。**抓取源会烂，必须持续看见** |
| **provider 兼容矩阵**：10 项测试不全绿的 provider 只能用于试验 | `providers/README.md` | LLM provider 也要有 `provider_health`（结构化输出是否稳、中文是否稳），矩阵不全绿的不上生产批跑 |
| **合规红线：不给建仓/加减仓/目标价建议** | §0.3 | intel 输出"某作者近 30 日提到 X，该作者历史 20 日超额 +3.2%（样本 47）"，**不输出"建议买入 X"** |

### 2.2 不借

| Vibe-Research 的做法 | 为什么不借 |
|---|---|
| 六阶段个股研究 SOP（profile→financials→estimates→valuation→risk→report） | 它是**单标的深度研究**的编排；我们要的是**全市场、日频、批量**的情报流水线。阶段制 SOP 是串行的，跑不动 5000 只 |
| Codex Harness / hooks 沙箱 | 我们的 LLM 调用点很窄（抽取/schema 化），不需要 Agent 自主工具调用。引入 Agent = 引入不可控成本与不可复现性 |
| `registry.json` 117 端点 + 分层（30 层） | 那是**行情/财务**端点表，dc 已有自己的 ingestion 注册表（`app/ingestion/registry.py`）。intel 的源是**文本流**，形态不同，另起 `intel.sources` |
| 研究报告产物 | 我们要的是**因子与信号**，不是给人读的报告。作者画像是中间产物，不是终点 |

---

## 3. 架构：L0–L5 六层

```text
L0 摄取 Ingest      RSS / RSSHub / 网页 / 手工导入 / 微信（半自动）
                    └─> 原文落盘 raw/（不可变）+ documents 表
L1 规范化 Normalize  正文抽取、去广告、时间归一、**转载去重（simhash）**
L2 理解 Understand   2a 确定性预筛（标的识别 + 事件分类，零 LLM）
                     2b LLM 抽取（schema 化，必带 span）
L3 聚合 Aggregate    按 author 时间加权聚合 → 风格向量 + **历史准确度**
L4 因子化 Factorize  情报因子 + **available_at（防前视）** → intel.factor_values
L5 消费 Consume      选股参考面板 / dc 因子选股与回测
```

**为什么 L2 要拆成 2a/2b**：不是所有文章都值钱。一篇没提到任何标的、也不是宏观/产业观点的文章，
送 LLM 就是纯烧钱。确定性预筛**零成本、可复现、可单测**，先把 80% 的无用文本滤掉，
再让 LLM 处理剩下的 20%。这也是 Vibe 用 `chokepoint_keywords.json` 而不是「让 LLM 读全部新闻」的原因。

**为什么 L3 的"历史准确度"必须在 intel 里算，而不是交给人判断**：
"这个号准不准"是最容易自欺的问题——人只记得对的那些。
准确度必须由 `factor.raw_bars` 的真实收益算：提及后 N 日超额收益的中位数、胜率、样本数。
**样本数必须一起显示**：3 篇的画像和 300 篇的画像不能同样对待。

---

## 4. 目录结构

```
E:\Workspace\lab.Quant\intel\
├── app/
│   ├── core/
│   │   ├── config.py            # INTEL_* 设置（LLM provider、预算、开关）
│   │   ├── logging.py
│   │   └── db.py                # asyncpg / SQLAlchemy，schema=intel
│   ├── ingest/                  # L0
│   │   ├── base.py              # FeedAdapter 抽象：fetch() -> list[RawDoc]
│   │   ├── rss.py               # 通用 RSS/Atom
│   │   ├── rsshub.py            # RSSHub 自建实例（默认关闭）
│   │   ├── web.py               # 单页抓取 + 正文抽取
│   │   ├── wechat.py            # 微信：见 §7，半自动
│   │   ├── manual.py            # 手工导入（文件/目录 watch）
│   │   └── registry.py          # 源注册表 + 轮询策略
│   ├── normalize/               # L1
│   │   ├── extract.py           # 正文抽取（去"在看/往期推荐/广告"）
│   │   ├── dedupe.py            # canonical_url + content_hash + **simhash 转载识别**
│   │   └── alias.py             # 标的别名：代码/简称/俗称 -> symbol
│   ├── understand/              # L2
│   │   ├── prescreen.py         # 2a 确定性筛（可单测，无网络无 LLM）
│   │   └── llm/
│   │       ├── client.py        # 统一调用 + 重试 + 预算闸 + 审计落库
│   │       ├── provider.py      # openai / deepseek / qwen / ollama(本地)
│   │       ├── schema.py        # pydantic 输出契约（strict）
│   │       └── prompts.py       # prompt 版本化（v1 / v2 ...）
│   ├── aggregate/               # L3
│   │   ├── author_profile.py    # 风格向量 + 历史准确度
│   │   └── resonance.py         # 多源共振（去重转载后）
│   ├── factorize/               # L4
│   │   ├── intel_factors.py     # INTL_* 因子实现
│   │   └── availability.py      # available_at 计算，防前视
│   ├── storage/                 # documents / mentions / profiles / factors
│   ├── api/v1/                  # feeds / docs / signals / profiles / factors / health
│   └── tasks/
│       ├── scheduler.py         # 轮询调度（与 dc 的盘后调度无关）
│       └── backfill.py          # 历史补数
├── migrations/
│   └── 001_intel_schema.sql
├── tests/
├── Dockerfile
└── docker-compose.yml
```

**命名约定**：情报因子前缀 `INTL_`，与 dc 的 `SENT_`（量比/换手，价格衍生物）区分开——
两者都叫"情绪"会让人误以为是同一个东西。

---

## 5. 数据模型（schema `intel`）

| 表 | 关键字段 | 说明 |
|---|---|---|
| `intel.sources` | `id, type, url, name, credibility, enabled, poll_interval_sec, robots_ok, last_polled_at` | 源登记。`credibility` 人工标注（0–5），不是模型打的 |
| `intel.authors` | `id, name, platform, source_id, credibility, note` | 作者/公众号。一个公众号 = 一个 author |
| `intel.documents` | `id, source_id, author_id, url, canonical_url, title, published_at, ingested_at, content_hash, simhash, raw_path, body_text, is_original, is_ad, lang` | **不可变原文落盘**，`raw_path` 指向原文文件。`ingested_at` 是系统首次观测时间，**防前视的关键** |
| `intel.doc_mentions` | `id, doc_id, symbol, stance, confidence, horizon, thesis, span_start, span_end, method, llm_version, computed_at` | 单篇对单标的的观点。`method ∈ rule/llm`；**`span_*` 是硬要求**（Vibe 的事实 vs 推断） |
| `intel.doc_style` | `doc_id, style_dims jsonb, llm_version, computed_at` | 单篇风格信号（value/growth/momentum/contrarian/event/quality 各维度强度） |
| `intel.author_profiles` | `author_id, as_of, style_vector jsonb, sample_docs, hit_rate, avg_excess_ret_20d, preferred_industries jsonb, avg_holding_horizon, llm_version, computed_at` | 作者画像。**版本化，不覆盖**——换 prompt 会产生新版本 |
| `intel.signal_daily` | `trade_date, symbol, mention_cnt, sentiment, resonance, available_at` | 日频信号中间层 |
| `intel.factor_values` | `factor_code, symbol, trade_date, value, available_at` | 因子值。**与 dc 因子表同构**，便于回测直接消费 |
| `intel.llm_runs` | `id, doc_id, provider, model, prompt_version, input_tokens, output_tokens, cost_cny, latency_ms, status, error` | LLM 审计：花了多少钱、哪次失败、哪版 prompt |
| `intel.feed_health` | `source_id, checked_at, status, latency_ms, items, error` | 源健康（抄 Vibe `health.py` 的思路） |
| `intel.symbol_alias` | `alias, symbol, source` | 别名表：`茅台`/`贵州茅台`/`600519` → `600519.SH`。人工可补 |

### 两个字段的语义必须写死

```python
# published_at  —— 文章自称的发布时间。用于「展示」和「叙事」，不可信（可能没有、可能错、可能被改）
# ingested_at   —— 系统首次观测到这篇文章的时间。这才是「信息可得」的时刻
available_at = max(published_at, ingested_at)
```

**回测只能用 `available_at`，不能用 `published_at`。**
理由见 §9.1。

---

## 6. 情报因子定义（L4）

| 因子 | 计算 | 用途 |
|---|---|---|
| `INTL_MENTION_HEAT_5` | 近 5 日提及次数（**去重转载后**的不同 doc 数） | 热度。转载不计数——20 个号转同一篇不是"20 个号看好" |
| `INTL_SENTIMENT_10` | Σ(stance × 作者历史准确度 × 时间衰减) / Σ(权重)，10 日窗口 | 加权情绪。**准确度做权重**：不准的号少说话 |
| `INTL_AUTHOR_CONVICTION` | 单作者对单标的的提及次数 × 该作者准确度 × 新鲜度 | "谁在重仓嘴" |
| `INTL_RESONANCE_5` | 5 日内**不同作者**（转载已去重）提及同一标的的数量 | 真共振 vs 假共振 |
| `INTL_STYLE_MATCH` | 标的特征与指定作者风格向量的余弦相似度 | **选股参考的核心**：这个号会喜欢哪些票 |
| `INTL_FIRST_MENTION` | 是否为该标的近 60 日首次被提及 | 信息差代理 |

**所有因子都带 `available_at`**，写入 `intel.factor_values`。

---

## 7. 场景落地：公众号 → 作者风格画像 → 选股参考

这是本次设计要打通的主场景。走一遍：

### 7.1 微信源的真实约束（先说清楚，别设计成空中楼阁）

微信公众号**没有公开 RSS、没有文章列表 API**。现实约束：

| 路径 | 可行性 | 判断 |
|---|---|---|
| 官方接口 | 仅认证服务号有素材/图文接口，订阅号无；且需主体资质 | ❌ 不指望 |
| 搜狗微信搜索 | 已基本停止服务，强验证码 | ❌ 不采用 |
| `mp.weixin.qq.com/s/xxx` 直抓 | 部分链接带时效参数、防盗链、需微信 UA；有被封风险 | ⚠️ 只用于**用户自己提供的链接** |
| RSSHub 自建 / wechat2rss 等第三方聚合 | 能用，但依赖第三方镜像站，稳定性与合规性不保证 | ⚠️ 默认关闭，health 里如实标 `degraded` |
| **人工投喂**：用户提供链接列表 / 导出的 HTML / 剪藏目录 | 完全可控、无合规风险 | ✅ **这是主路径** |

**设计结论**：微信源定位为**半自动**——

```text
P4a  人工导入：用户提供 URL 列表（.txt/.csv）或导出文件（HTML/mhtml/纯文本），
     系统解析 → 正文抽取 → 入库。零抓取风险。
P4b  目录 watch：用户用浏览器插件/剪藏工具把文章存到 ~/intel-inbox/，
     系统监听该目录，新文件自动入库。日常使用最省事。
P4c  第三方聚合（可选，默认 disabled）：RSSHub 自建实例。
     在 feed_health 里明确标 degraded，不假装它很稳。
```

**这个取舍的核心是**：微信这一侧的"获取"本来就没有干净的自动化方案，
与其做一个随时会坏的爬虫，不如把力气花在**获取之后的清洗与理解**上——那才是 LLM 真正增值的地方，
也是这个模块真正的壁垒。抓取是别人的问题。

### 7.2 端到端流程

```text
①  摄取    用户提供 200 篇某公众号历史文章（目录 watch / URL 列表）
             → 每篇原文落盘 raw/2026/09/<uuid>.html，documents 表记 ingested_at = now()
②  去重    canonical_url + content_hash 去完全重复；
             simhash 识别转载/洗稿 → is_original=false 的不计共振、不计热度
③  预筛    确定性：正则命中 600519 / 贵州茅台 / 茅台 → 600519.SH（别名表）
             200 篇里 143 篇命中至少一个标的，57 篇是纯方法论/闲聊 → 只送那 143 篇给 LLM
④  LLM     按篇抽取（schema 化，必带 span）：
             { symbol, stance, confidence, horizon, thesis, span, style_dims }
             抽取结果写 doc_mentions / doc_style，记 llm_version + 审计
⑤  聚合    按 author 时间加权（半衰期 180 天）算风格向量；
             用 factor.raw_bars 算「提及后 20 日超额收益」→ 历史准确度
⑥  产出    作者画像卡片（见下）+ INTL_STYLE_MATCH 因子
```

### 7.3 作者画像卡片（面向用户的产出形态）

```
作者：某某投研（公众号）        样本：143 篇 / 提及 87 只标的 / 2023-04 ~ 2026-09

风格向量       价值 ████████░░ 0.62   成长 █████░░░░░ 0.41
               动量 ██░░░░░░░░ 0.18   逆向 ██████░░░░ 0.47
               事件驱动 ███░░░░░░░ 0.25  质量 ███████░░░ 0.55

持仓周期       中长线（中位提及后持有 94 个交易日）
行业偏好       电子 28% / 电力设备 19% / 医药 14% / 其他 39%
市值偏好       中小盘（提及标的市值中位 86 亿）

历史准确度（由 factor.raw_bars 实算，非自评）
  提及后 20 日超额收益   中位数 +2.1%   胜率 58.6%   样本 47
  提及后 60 日超额收益   中位数 +5.4%   胜率 61.7%   样本 47
  提示：样本 47 < 100，准确度置信度低，仅供参考

近期动向（近 30 日）
   新增提及  600519.SH（中性，1 次）、300750.SZ（看多，3 次）
   风格漂移  价值 0.62 → 0.55（近 90 天），向成长倾斜

⚠ 本卡片是描述性统计，不是投资建议。样本量与时间窗口已如实标注。
```

**三个刻意的设计**：

1. **准确度是算出来的，不是作者自述、也不是模型打分。** 只信 `factor.raw_bars` 的真实收益。
2. **样本数永远跟着准确度一起显示。** "胜率 58.6%" 不写样本就是耍流氓——3 篇的 100% 胜率毫无意义。
3. **风格漂移要单独标。** 一个 2023 年是价值风格、2026 年转成成长的号，用全量历史算画像会失真。

---

## 8. LLM 使用规范

### 8.1 成本控制（LLM 是唯一的持续成本）

| 手段 | 做法 |
|---|---|
| 确定性预筛 | 未命中任何标的/事件词的文章不送 LLM（实测预期过滤 70%+） |
| 内容哈希缓存 | `content_hash` → 抽取结果。转载/重复不重复烧钱 |
| 分层模型 | **本地模型（Ollama + Qwen 系列量化，本机 RTX 5070 8GB 可跑 7B/14B）做批量抽取**；云端模型只做复杂推理与兜底 |
| 预算闸 | `INTEL_LLM_DAILY_BUDGET_CNY`，超了**停批并告警**，不是静默降级 |
| 审计 | 每次调用写 `llm_runs`（tokens / cost / latency / status / prompt_version） |

> 本机 GPU 是现成资产：RTX 5070 8GB，此前已在跑 faster-whisper large-v3。
> 批量抽取这种"量大、单条简单"的活，本地模型更划算，也不受 API 限流与数据出境的约束。
> 云端模型留给"总结作者风格"这种量小、要求高的任务。

### 8.2 防幻觉

| 规则 | 说明 |
|---|---|
| 输出必须 schema 化 | pydantic strict 校验，不合规就重试一次，再失败落 quarantine 并标记该 doc 未处理 |
| **任何抽取必须带 span** | 没有原文位置的 stance / thesis 不予入库 |
| 抽不到写 `null` | 不许"根据上下文推断"。标的是标的就是，不是就不是 |
| **数字不经 LLM 计算** | 涨跌幅、超额收益一律由 `factor.raw_bars` 算（Vibe §0.2 同款纪律） |
| prompt 版本化 | `llm_version` 进每条记录，换 prompt 产生新版本，**不覆盖旧版** |

### 8.3 provider 抽象

`understand/llm/provider.py` 统一接口，支持 `openai` / `deepseek` / `qwen` / `ollama`（本地）。
每台都要跑一次兼容矩阵（结构化输出稳定性、中文稳定性、长文截断行为），
结果写 `provider_health`，**矩阵不全绿的不上生产批跑**（抄 Vibe `providers/README.md` 的做法）。

---

## 9. 硬纪律（这三条不守，模块就是负资产）

### 9.1 防前视偏差：因子只能用 `available_at`

**场景**：某公众号 2026-03-01 发了篇文章看好 X。我们 2026-09-07 才把它导入系统。
如果用 `published_at = 2026-03-01` 生成因子，回测里 2026-03 就"知道"了这个观点——
**但那时候系统里根本没有这条情报**。这就是前视偏差，回测收益会虚高。

**规则**：
```python
available_at = max(published_at, ingested_at)   # 写进每行因子
```
回测取因子时 `WHERE available_at <= <回测当日>`。
**这一条要写单测锁死**：构造一个 published_at 远早于 ingested_at 的文档，
断言它在 `ingested_at` 之前不产生任何因子值。

另：**LLM 重跑也会造成前视**。换 prompt 后重算的历史因子，不能"改进"过去的回测结果。
所有 `doc_mentions` / `author_profiles` 都带 `llm_version` + `computed_at`，
回测按"当时可用的最新版本"取，不用今天的版本。

### 9.2 转载去重：不做这个，共振因子完全失真

A 号原创一篇，B/C/D…T 号转载。不做去重的话：
`INTL_RESONANCE = 20`（"20 个号同时看好"），真实是 **1 个号**。

**手段**：`canonical_url` 归一 + `content_hash` 精确去重 + **simhash 近似去重**（识别改标题/洗稿）。
`is_original=false` 的文档**不计入**热度与共振，但仍计入作者画像（转载本身也是风格信号：它转什么）。

### 9.3 不给投资建议（合规红线）

intel 的输出边界（抄 Vibe §0.3）：

| ✅ 输出 | ❌ 不输出 |
|---|---|
| 某作者近 30 日提及哪些标的、什么立场 | 建议买入/卖出 X |
| 该作者历史准确度（含样本数与窗口） | 目标价 / 止损位 / 仓位 |
| 标的特征与作者风格的匹配度 | "推荐关注"式的话术 |
| 情报因子的值与可用时点 | 任何形式的"该买"暗示 |

**理由不是为了合规而合规**：一旦输出落到动作建议，这个模块就从"研究工具"变成"信号服务"，
而它的准确度**远没到**可以承担这个角色的程度。

---

## 10. 部署形态：dc 内可选模块

"两模块可选部署"由 **同一个 dc 二进制内的功能开关**实现，无需独立容器 / 镜像 / compose profile：

```bash
# data-cleaner/.env
INTEL_ENABLED=false      # 默认关；true 时启动情报模块（摄取/理解/画像/因子化全部激活）
WS_ENABLED=false         # dc→backend 因子副本同步（intel 的 INTL_* 推送也走这条）
```

```bash
# 只跑行情（默认）
INTEL_ENABLED=false docker compose up -d data-cleaner
# 行情 + 情报
INTEL_ENABLED=true  docker compose up -d data-cleaner
```

| 组合 | 场景 |
|---|---|
| `INTEL_ENABLED=false` | 只需要行情与因子回测；不碰文本、不接 LLM、零额外成本、基础镜像不装重依赖 |
| `INTEL_ENABLED=true` | 情报因子喂回选股与回测，完整闭环 |

**开关语义**：`INTEL_ENABLED=false`（默认）时，`app/intel/` 的路由 / 调度 / 迁移入口全部跳过，dc 行为与改造前一致（与 `WS_ENABLED` 同构）。dc 侧**不引入任何 intel 运行时依赖**——`INTEL_ENABLED=False` 时重型依赖不 import、不安装，dc 照常工作。

**未来若需独立扩展**：当 intel 与 dc 水平扩展画像分化（多 intel worker / GPU），可沿用初版思路加 compose profile（`market-data` / `intel` / `full`）把 intel 拆成独立 service，复用同一份 `app/ws/` 代码；这是**后手**，非当前必选项。

**信息流单向**：`intel → 读 factor.raw_bars`（算准确度）、`intel → 写 intel.factor_values` 并 `emit_factor_updated` 通知 backend、`dc → 读 intel.factor_values`（回测）。**dc 绝不反向依赖 intel 运行时**（仅共享同一 PG 的 `intel` schema）。

---

## 11. 分期实施

| 期 | 内容 | 产出 | 验收 |
|---|---|---|---|
| **P0** | schema + 骨架 + RSS 源跑通 + 去重 + 存储 + health | 一个 RSS 源稳定入库 | `feed_health` 连续 7 天 ok；转载去重单测通过 |
| **P1** | 确定性预筛 + 别名表 + LLM 抽取（schema + span + 审计） | 文章 → 结构化观点 | 抽取结果 100% 带 span；`llm_runs` 有完整成本记录 |
| **P2** | 作者画像 + 历史准确度（接 `factor.raw_bars`） | 画像卡片 | 准确度数字与手工核对一致；样本数必显 |
| **P3** | 情报因子 + `available_at` + 防前视单测 + 回测接入 | `INTL_*` 因子可回测 | 防前视单测锁死；因子能喂进 dc 选股引擎 |
| **P4** | 微信半自动摄取（manual / 目录 watch / 可选 RSSHub）+ 风格总结报告 | 主场景打通 | 导入 200 篇 → 出画像 → 出选股参考 |

**建议从 P0 + P1 起步**：这两期不依赖微信（微信是最麻烦的一环），
先用公开 RSS（财联社、新浪财经、行业媒体）把链路跑通，
证明"文本 → 结构化信号"这件事成立，再啃微信。

---

## 12. 风险与开放问题

| 风险 | 影响 | 应对 |
|---|---|---|
| 微信获取无干净方案 | 主场景的"摄取"环节要人工 | §7.1 的半自动路径；把力气放在清洗与理解 |
| LLM 抽取质量不稳定 | 因子噪声大，回测无信号 | 先上确定性预筛 + 本地模型批量跑 + 人工抽检 100 篇建立 baseline |
| 历史文章回放成本 | 一个号 5 年文章可能上千篇 | 先用本地模型，云端只跑最近 90 天；历史分批 |
| 情报因子有效性质疑 | 可能根本不 alpha | **先做检验再做工程**：P1 后先拿 50 篇手工标注，看 LLM 抽取与人工的一致率；低于阈值就别往下建 |
| 版权与合规 | 第三方内容存储与分发 | 原文仅本机落盘、不对外分发；不产出投资建议；抓取遵守 robots 与频率限制 |
| 转载识别误杀 | 原创被判转载，丢信号 | simhash 阈值可配，误杀可人工翻转（`documents.is_original` 可人工改） |

### 三个问题已于 2026-09-07 拍板

| 问题 | 决策 | 落法 |
|---|---|---|
| 情报源优先级 | **先公开 RSS** | P0 只接 RSS；`manual.py` / `wechat.py` 接口在 P0 就留出（空实现 + 注册），P4 才启用 |
| LLM 本地还是云端 | **先云端** | P1 全走云端 provider（deepseek/qwen/openai 任选其一起步）；`provider.py` 保留 ollama 接口，P3 后视 `llm_runs` 成本数据再评估是否切本地 |
| P1 后是否做有效性检验 | **做** | P1 结束设硬 gate：50 篇手工标注比对，指标与阈值见 `intel-module-plan.md` P1-Gate；不达标不进 P2 |

---

## 13. 与现有文档的关系

- `docs/memo/architecture.md` §0「回测在 dc」——本次设计**不动**这条归口。intel 不实现回测。
- `docs/memo/quant-closed-loop-roadmap.md` M3「技术面+另类因子扩展」——情报因子是该里程碑的落地形态之一。
- `docs/memo/vibe-research-benchmark-2609.md` —— 回测可信度那次借鉴；本文是第二次借鉴，取的是**数据纪律**而非回测。
