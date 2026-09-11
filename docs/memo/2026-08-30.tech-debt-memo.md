=== 技术债务清单（架构层）===

    日期：2026-08-30
    范围：因子策略 / 多 data-cleaner 网关架构

    【背景】

    架构为「主后端(:8000) 网关 + 多 data-cleaner(:8100) 实例」：
    - data-cleaner 是因子域 owner：持有因子库、行情、计算引擎(engine.py)，策略配置表 factor.factor_strategies 也建在它这边。
    - 主后端只做 JWT 鉴权 / 路由 / 交易下单，因子策略读写经 factor_strategy_proxy 转发到某个 cleaner。
    证据：
      - data-cleaner/app/api/v1/strategy.py:1 注释「因子选股策略接口（data-cleaner 持有配置与计算）」
      - migrations/005_factor_strategy.sql:2 「由 data-cleaner 持有/读写」
      - backend/app/api/api_v1/endpoints/factor_strategy.py:1 「所有读写经 factor_strategy_proxy 转发；统一来源在 data-cleaner」
      - backend/app/services/factor_strategy_proxy.py:31 pick_service 仅挑第一个 is_active 实例

    【高优先级】

    1. 跨 data-cleaner 的因子无法在同一策略中混用
       - 根因：pick_service 盲选单个 cleaner，不按策略所用因子反查其归属实例；
         scores / backtest 全程在该 cleaner 本地用 factor_registry.get_factor(code) 计算
         （data-cleaner/app/factors/registry.py:15），该 cleaner 只认识自己注册的因子。
       - 现象：策略混用 cleaner-A 与 cleaner-B 的因子时，被派到 A 即抛 FactorNotFoundError，策略不可评估。
       - 地基已有：FactorRegistry 表按 service_code+factor_code 记录因子归属
         （backend/app/models/cleaner.py:64，uq_service_factor 见 :60），缺的是「评估期按因子路由+合并」一层。
       - 候选解法：
         L1 因子共置：把所需因子定义/口径同步到跑策略的 cleaner（数据冗余，治标）。
         L2 因子级路由+联邦聚合（推荐）：resolve_factor_services(config) 反查归属→各 cleaner
             算因子矩阵→网关对齐合并→纯打分接口跑 engine.score（计算与归属解耦）。
         L3 共享因子物化库：各 cleaner 把因子值写入中央时序库，引擎只读库（解耦最彻底，工程量最大）。

    2. 因子策略保存失败不可定位（已修复 2026-08-30）
       - 背景：factor_strategies 表(migration 005)建在 data-cleaner 侧；其启动已调用 apply_migrations()
         （data-cleaner/app/main.py:26-33，含「已应用 N 个迁移」日志），自动建表已生效。
       - 原痛点：factor_strategy_proxy 抛 FactorStrategyProxyError 后端点未捕获，前端裸 catch 写死
         「保存失败」，真实原因（relation 不存在 / 清洗服务 5xx / 无可用实例）被掩盖；
         且后端 Response 用 msg 字段，前端拦截器只认 error/message，字段对不上。
       - 修复：
         · 后端 backend/main.py 新增 FactorStrategyProxyError 专用处理器，返回
           Response.fail(code=502, msg="因子服务错误：<真实原因>")（去掉 Internal Server Error 前缀）。
         · 前端 request.ts 错误提取补充 Response 的 msg 字段（原只认 error/message）。
         · 因子策略表单 catch 直接读取 e.response.data.msg 展示后端透传的真实原因（不再写死「保存失败」，也不单靠拦截器）。
       - 验证：保存/回测/调仓等因子服务错误现在显示具体清洗服务报错，不再黑盒。

    【中优先级】

    3. pick_service 不感知因子分布 / QoS
       - 当前只按 id 顺序取第一个 is_active（factor_strategy_proxy.py:37），未参考 FactorRegistry 归属、
         未参考 cleaner_services.qos/last_heartbeat（backend/app/models/cleaner.py:31-33）。
       - 多实例下流量分配与就近性无依据。

    4. factor_availability / scores / backtest 仅反映单 cleaner 视野
       - data-cleaner 各自只知本地因子，聚合因子底册依赖主后端 factor_registry 同步
         （cleaner_gateway.sync_factors），但运行期未据此做路由。

    【低优先级】

    5. 计算与消费耦合，无历史因子值可重放
       - 回测依赖实时重算，无物化因子快照，跨 cleaner 因子更难审计/复现（见 L3）。

    【数据接入层】

    6. akshare 成长数据遍历开销大，需低频调度
       - 背景：tushare fina_indicator 缺权限，成长（营收/净利同比）改由 akshare 利润表
         (stock_profit_sheet_by_report_em) 逐标的遍历补齐
         （data-cleaner/app/ingestion/fundamental_source.py: fetch_growth_akshare / _map_profit_sheet）。
       - 现象：akshare 无"全市场按报告期"批量接口，每次刷新需遍历全 A（~5000 标的）各自拉取，
         单期即数千次网络调用，耗时数分钟~小时级，不宜每日高频。
       - 影响：GRO_ 成长因子依赖 factor.finance_reports；若并入每日 refresh_fundamental 高频跑，
         会拖垮每日任务且易触发源站限频/超时。
       - 建议：
         · 成长刷新作为周/月级回填任务（财报天然按季更新，引擎打分只取最新一期即可），
           与每日 daily_basic / trading_status 刷新解耦。
         · 增量优化：仅对"库内尚无该报告期"的标的拉取，减少重复 IO；
           或升级 tushare 积分档恢复 fina_indicator 批量（一次返回全市场），彻底去掉遍历。
       - 关联：FUNDAMENTAL_PROVIDER=auto 已自动探测 tushare 权限并兜底 akshare；
         trading_status（涨停池/跌停池/停复牌）为按日接口，开销小，可保留每日。

    【建议落地顺序】

    - 短期：修 #2（data-cleaner 启动 apply_migrations，网关可读错误），消除「保存失败」黑盒。
    - 中期：实现 #1 的 L2（因子级路由 + 合并），支撑跨 cleaner 因子策略。
    - 长期：评估 #5 的 L3 共享因子库，做计算/消费解耦与回测可重放。
