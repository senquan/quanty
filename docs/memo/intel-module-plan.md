# 市场情报模块实施计划（intel-module-plan）

- 计划日期：2026-09-07
- 上游文档：`intel-module-design.md`（设计稿，已定稿）
- 状态：**计划，未开工。本文不含代码。**
- 执行原则：**每期验收不过，不进下一期。**

---

## 0. 已拍板的三个决策（2026-09-07）

| # | 决策 | 对计划的影响 |
|---|---|---|
| D1 | **情报源先公开 RSS** | P0 只接 RSS；`manual.py` / `wechat.py` 在 P0 留空实现 + 注册占位，P4 才启用。公众号场景不提前投入 |
| D2 | **LLM 先云端** | P1 起全走云端 provider（首选 deepseek，备选 qwen/openai）；`provider.py` 统一接口保留 ollama，P3 结束后拿 `llm_runs` 的真实成本数据再评估是否把批量抽取切本地。**预算闸是 P1 第一优先级，不是事后补** |
| D3 | **做有效性检验** | P1 结束设硬 gate（见 §P1-Gate）：50 篇手工标注比对，不达标不进 P2 |

---

## 1. RSS 源候选清单（P0 第一个动作就是验证可达性）

首批清单取自 `docs/repos/Vibe-Research/datasources/rss_sources.json` 的 tier-1 策展源中**中文 / 宏观子集**（A 股情报最对口），不是凭空列。该清单本身已逐字节对齐上游 `simonlin1212/investment-news`，可信度高。

### 1.1 中文 / 宏观源全量（从 Vibe 清单摘出）

| 源 | 行业 | URL | P0 首批? | 风险 / 备注 |
|---|---|---|---|---|
| 华尔街见闻 | macro | https://dedicated.wallstreetcn.com/rss.xml | ✅ 首批 | 部分内容需登录，可能只拿到摘要 → 验证"能否取全文" |
| 东方财富股票 | macro | http://rss.eastmoney.com/rss_stock.xml | ✅ 首批（A 股最对口） | **http 非 https**，部分环境会拒 / 重定向；先测 |
| 东方财富资讯 | macro | http://rss.eastmoney.com/rss_partener.xml | ➕ 备选 | 同上 http 问题 |
| 经济观察网 | macro | http://www.eeo.com.cn/rss.xml | ➕ 备选 | http 问题 |
| 36氪 | tech | https://36kr.com/feed | ⛔ 剔除 | 实测返回 SPA 落地页（text/html，`<!DOCTYPE html>`），非 RSS feed，已移除首批 |
| 爱范儿 | tech | https://www.ifanr.com/feed | ✅ 首批 | 科技/创投/一级市场互补；实测 200 + application/rss+xml |
| 虎嗅 | tech | https://rss.huxiu.com/ | ✅ 首批 | 产业观点；实测 200 + feed=Y |
| 钛媒体 | tech | https://www.tmtpost.com/rss.xml | ✅ 首批 | 同虎嗅；实测 200 + feed=Y（延迟偏高 ~1.4s） |
| IT之家 | tech | https://www.ithome.com/rss/ | ➕ 备选 | 消费电子，信号弱；实测 200 + text/xml，可作后备 |
| 智东西 | ai | https://zhidx.com/rss | ➕ 备选 | AI 硬件 |
| 机器之心 | ai | https://wechat2rss.xlab.app/feed/...xml | ⛔ 排除 | 依赖第三方镜像 wechat2rss，**P0 默认 disabled**，health 标 degraded（设计文档 §7.1） |
| 新智元 | ai | https://wechat2rss.xlab.app/feed/...xml | ⛔ 排除 | 同上 |
| 少数派 / 白鲸出海 / 动点科技 / 月光博客 | tech | — | ⛔ 暂缓 | 与 A 股信号弱相关，P0 不做 |

### 1.2 P0 首批验证建议（3–5 个）

**建议首批 5 个（2026-09-07 实测全部可达 + 真 RSS/Atom）**：华尔街见闻、东方财富股票、虎嗅、钛媒体、爱范儿。

> 实测备注：5 个源全部 HTTP 200 且返回 XML feed（`Feed=Y`）。36氪 `/feed` 实测返回 SPA 落地页（text/html，非 feed），已从首批剔除，由爱范儿替补。东方财富股票为 http（非 https），抓取时需允许明文；钛媒体延迟偏高（~1.4s），轮询间隔已留余量。

理由：覆盖"宏观/财经 + 产业"两类信号；东方财富股票是 A 股最对口源；其余为 https、稳定、全文 RSS，跑通链路成本最低。wechat2rss 两家因第三方镜像依赖按设计文档 §7.1 排除。

### 1.3 不在 Vibe 清单内、需另行发现的源

- **财联社电报、新浪财经**：Vibe 清单**没有**这两条（其 macro 用的是华尔街见闻 / 东方财富 / FT / CNBC 等）。若需要国内快讯口径，需单独发现（GitHub Awesome-Feeds / 官网 RSS 直找），不进首批。

**入选标准**（三条全满足才入库）：① 免费/公开可达；② 能拿到正文全文（不是只有摘要）；③ 抓取频率 ≤ 1 次/15 分钟（做礼貌爬虫，robots 尊重写进 `sources.robots_ok`）。

**规模控制**：P0 最多接 **3–5 个源**。目标是跑通链路，不是覆盖度。

---

## 2. P0 — 骨架 + RSS 跑通（ ingestion 闭环 ）

| 任务 | 内容 | 产出 |
|---|---|---|
| P0-1 | dc 内模块骨架：`data-cleaner/app/intel/` 目录 + `core`(config/db/logging)、`INTEL_ENABLED=false` 默认关、`settings` 增 `INTEL_*` 项、模块路由/调度/迁移入口由开关门控（沿用 dc 既有 Dockerfile，不新建镜像） | dc 能起、模块入口可跳过、能连 PG（schema=`intel`） |
| P0-2 | `migrations/001_intel_schema.sql`：**只建 P0 需要的表**——`sources` / `documents` / `feed_health` / `symbol_alias`。其余表（mentions/profiles/factor_values/llm_runs）推迟到对应期再建，不提前建空表 | migration 可重复执行 |
| P0-3 | RSS 源验证（§1 清单）→ 入选源写 `sources`，验证报告记 `feed_health` | 3–5 个可用源 |
| P0-4 | `ingest/rss.py` + `registry.py`：轮询拉取、原文**不可变落盘** raw/、`documents` 记 `ingested_at`/`content_hash` | 新文章自动入库 |
| P0-5 | `normalize/`：正文抽取 + `content_hash` 精确去重 + simhash 转载识别（阈值可配） | 去重管道 |
| P0-6 | `manual.py` / `wechat.py` **空实现 + 注册占位**（D1）：接口签名定下来，抛 NotImplemented，源类型登记进 `sources.type` 枚举 | 接口冻结，P4 直接填肉 |
| P0-7 | `api/v1/health`：feed_health 查询接口 | 源状态可见 |

**P0 验收（全过才进 P1）**：
- [ ] `feed_health` 连续 7 天 status=ok（允许个别 partial，failed 要有 error 记录）
- [ ] 转载去重单测：同文改标题、洗稿两段，simhash 均判重；两篇不同文章不误杀
- [ ] 原文落盘抽查 10 篇：`raw_path` 文件存在且 `content_hash` 与文件一致
- [ ] dc 侧零改动、零新依赖（`INTEL_ENABLED=false` 时 dc 行为与现在完全一致，基础镜像不装 intel 重依赖）

---

## 3. P1 — 理解层 + 有效性检验 Gate

| 任务 | 内容 | 产出 |
|---|---|---|
| P1-1 | `symbol_alias` 灌种子数据：从 `factor.stock_info` 导 symbol/简称，人工补俗称（茅台→600519.SH 等） | 别名表可用 |
| P1-2 | `understand/prescreen.py`：标的识别（代码正则 + 别名表）+ 事件关键词表（参照 Vibe `chokepoint_keywords.json` 形态：关键词 + 正则 + negatives）。**零 LLM、可单测** | 过滤率报告：预筛命中率（预期滤掉 70%+） |
| P1-3 | `understand/llm/client.py`：云端 provider 接入（D2，首选 deepseek）、重试、**预算闸 `INTEL_LLM_DAILY_BUDGET_CNY`（超了停批告警）**、审计写 `llm_runs` | 每次调用可追溯成本 |
| P1-4 | `schema.py` strict pydantic 输出契约 + **span 硬校验**（无 span 的 stance/thesis 直接拒绝入库，落 quarantine） | 抽取结果 100% 带 span |
| P1-5 | `prompts.py` 版本化（v1 起步），`doc_mentions` / `doc_style` 落库，`migrations/002` 建表 | 文章 → 结构化观点 |
| P1-6 | 批跑预筛命中的存量文档（P0 攒下的） | `llm_runs` 有真实成本数据 |

### P1-Gate：有效性检验（D3，硬 gate）

**样本**：50 篇（从预筛命中、已过 LLM 抽取的文档里分层抽样：不同源、不同标的、含 stance 强弱差异）。

**标注方式**：人工读原文，独立记录 symbol / stance / horizon，不看 LLM 结果；标完再比对。

| 指标 | 达标线 | 说明 |
|---|---|---|
| 标的识别一致率（symbol 级） | **≥ 90%** | 这是基础能力，低于 90% 说明预筛或抽取有系统性缺陷 |
| stance 方向一致率（看多/中性/看空三分类） | **≥ 80%** | 主观性允许一定分歧 |
| span 有效率（抽查 span 确实指向对应原文片段） | **≥ 95%** | 这是防幻觉的底线， span 瞎指等于没有 |
| 幻觉率（LLM 抽出的标的/数字原文不存在） | **≤ 2%** | 超过即红线 |

**gate 规则**：
- 全达标 → 进 P2；
- 有 1 项不达标 → 允许**最多 2 轮** prompt 修复（每轮重新跑 50 篇，成本可控），修完仍不达标 → **停止 P2–P4，出复盘报告**；
- 幻觉率超标 → 不给修复轮，直接停。

---

## 4. P2 — 作者画像 + 历史准确度

| 任务 | 内容 | 产出 |
|---|---|---|
| P2-1 | `factor.raw_bars` **只读**接入：提及后 20/60 日超额收益计算（基准=同期指数，指数选择待定：中证全指或对应行业指数） | 准确度计算管道 |
| P2-2 | `aggregate/author_profile.py`：风格向量（时间加权，半衰期 180 天）+ 准确度 + 样本数；`author_profiles` **版本化不覆盖**（`migrations/003`） | 画像数据 |
| P2-3 | 风格漂移检测（近 90 天 vs 全量） | 漂移标记 |
| P2-4 | 画像卡片 API + 前端页面（经 backend 网关，不直连 intel） | 用户可见产出 |
| P2-5 | 样本量红线：sample_docs / 提及样本 < 30 的作者，准确度字段显示"样本不足"而非数字 | 防小样本误导 |

**P2 验收**：
- [ ] 抽 3 个作者 × 各 10 条提及，超额收益与手工用行情软件核对一致
- [ ] 画像卡片上所有准确度数字都带样本数与窗口
- [ ] "样本不足"逻辑有单测

---

## 5. P3 — 因子化 + 回测接入

| 任务 | 内容 | 产出 |
|---|---|---|
| P3-1 | `factorize/availability.py`：`available_at = max(published_at, ingested_at)` 写进每行因子 | 防前视数据基础 |
| P3-2 | **防前视单测（先于因子实现写）**：构造 published_at 早于 ingested_at 的文档，断言 ingested_at 前无因子值；LLM 重跑（新 llm_version）不得改变旧版本因子值的回测可见性 | 单测锁死 |
| P3-3 | `INTL_*` 因子按序实现：`MENTION_HEAT_5` → `SENTIMENT_10` → `RESONANCE_5` → `FIRST_MENTION` → `AUTHOR_CONVICTION` → `STYLE_MATCH`（从简到繁，每个先做 IC 初检再实现下一个） | `intel.factor_values` |
| P3-4 | dc 消费侧：因子选股/回测读 `intel.factor_values`（**可选依赖**，intel 停跑时 dc 回退到不含 INTL_* 的行为，不报错） | 回测闭环 |
| P3-5 | **成本复盘（D2 后续动作）**：汇总 `llm_runs` 全量成本 → 决策批量抽取是否切 ollama 本地 | 成本报告 + 切/不切结论 |

**P3 验收**：
- [ ] 防前视单测通过且覆盖"LLM 重跑"场景
- [ ] 每个 INTL_* 因子有分层回测/IC 报告（**因子无信号 ≠ 失败**，如实记录后决定去留）
- [ ] compose 只跑 `market-data` 时 dc 全链路回归通过（intel 缺席不影响既有功能）

---

## 6. P4 — 微信半自动 + 风格总结（主场景收官）

| 任务 | 内容 | 产出 |
|---|---|---|
| P4-1 | `manual.py` 实装：URL 列表（.txt/.csv）+ 导出文件（HTML/mhtml/纯文本）解析入库 | 人工投喂路径 |
| P4-2 | `wechat.py` 实装：目录 watch（~/intel-inbox/），浏览器插件/剪藏落地自动入库；mp.weixin.qq.com 链接直抓仅限用户主动提供 | 日常使用路径 |
| P4-3 | `rsshub.py` 可选源：默认 disabled，feed_health 标 degraded，不假装稳 | 备选路径 |
| P4-4 | 风格总结报告：云端 LLM 对单作者全量文档做风格综述（量小质高的任务），输出进画像卡片 | LLM 总结作者投资风格 |

**P4 验收（= 主场景验收）**：
- [ ] 导入某公众号 200 篇历史文章 → 全链路跑通：去重 → 预筛 → 抽取 → 画像 → INTL_STYLE_MATCH
- [ ] 画像卡片含：风格向量 / 行业偏好 / 持仓周期 / 历史准确度（带样本数）/ 风格漂移
- [ ] 全程 LLM 成本在 `llm_runs` 可查，无预算外调用

---

## 7. 里程碑依赖与检查点总览

```text
P0 骨架+RSS ──7天健康观察──▶ P1 理解层 ──P1-Gate(50篇标注)──▶ P2 画像 ──▶ P3 因子+回测 ──成本复盘──▶ P4 微信
                     │                        │
                     └ failed 源剔除重选       └ 不达标：≤2轮prompt修复→仍不达标=停，出复盘
```

| 检查点 | 性质 | 不过的后果 |
|---|---|---|
| P0 健康观察（7 天） | 软 gate | 个别源 failed 可剔除替换；全部 failed 说明选源策略错了，重做 §1 |
| **P1-Gate** | **硬 gate** | 不达标停 P2–P4（见 §3） |
| P3 因子 IC 初检 | 软 gate | 单因子无信号可裁撤该因子，不阻塞其他因子 |
| P3 成本复盘 | 决策点 | 决定 LLM 本地/云端的最终形态 |

---

## 8. 全局预算护栏（贯穿各期）

| 护栏 | 设定 |
|---|---|
| `INTEL_LLM_DAILY_BUDGET_CNY` | P1 起步设保守值（建议 ¥10/天），跑一周按 `llm_runs` 实际数据调整 |
| 单篇文档 LLM 上限 | 超长文截断策略：> 8k 字的文档只送首 8k + 摘要段（正文抽取时已去广告，超长多为长篇方法论，标的密度低） |
| 存量回放分批 | P4 导入 200 篇历史文章分 4 批跑，每批结束看成本再放下一批 |
| 停止开关 | 预算超限 = 停批 + 告警，**绝不静默降级**（设计文档 §8.1） |

---

## 9. 明确不做的事（本期计划范围内）

- 不做自动盯盘/实时告警——RSS 是分钟级，做不了实时，不装
- 不做向量库/RAG 检索——P0–P4 没有任何场景需要语义检索，等有真实需求再说
- 不做微信全自动爬虫——设计文档 §7.1 已论证，不重复
- 不做 Agent/工具调用式 LLM 编排——抽取点是窄接口，不需要（设计文档 §2.2）
- intel 不实现回测——撮合归口永远在 dc

---

## 10. 实施记录

| 日期 | 项 | 状态 |
|---|---|---|
| 2026-09-07 | **P0-1 骨架**：`data-cleaner/app/intel/` 包 + `core`(config/logging/db 接入层) + 开关门控占位路由(`/intel/health` 公开、`/intel/status` 受保护) + 占位调度(`intel_rss_poll` interval / `intel_daily_build` cron)；`app/core/config.py` 增 `INTEL_ENABLED`(默认 false) 及 `INTEL_*` 项；`app/api/v1/router.py` 与 `app/tasks/scheduler.py` 条件挂载；`migrations/011_intel_schema.sql` 建 `intel` schema 及 sources/documents/feed_health 表 | ✅ 已搭（纯骨架，无业务逻辑） |

**验证**：全部文件 `py_compile` 通过；`INTEL_ENABLED` 默认 false，`app.intel.*` 仅在其为 true 时被 import（重型依赖不加载）。`011` 迁移为纯 DDL，沿用 dc 启动期 `apply_migrations()` 全量执行（与 `factor` schema 始终存在一致）；开关仅门控路由/调度/重型依赖，schema 空表零运行成本。

**下一步**：P0 开工 → 先对 §1 的 5 个 RSS 源逐个实跑可达性验证，再填 `app/intel/ingest/rss.py`。
