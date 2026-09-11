"""服务配置模块

所有配置通过环境变量 / .env 注入，严禁硬编码密钥。
"""
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """data-cleaner 服务配置"""

    # Database (复用主后端 PG, 使用独立 schema: factor)
    DATABASE_URL: str = "postgresql+asyncpg://quant_user:quant_password@localhost:5432/quant_db"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # 因子 parquet 数据目录
    FACTOR_DATA_DIR: str = "./data/factors"

    # 失败输入快照目录（便于排查）
    QUARANTINE_DIR: str = "./data/quarantine"

    # 财务数据源（价值/成长因子）
    TUSHARE_TOKEN: str | None = None
    FUNDAMENTAL_PROVIDER: str = "auto"  # tushare / akshare / auto(优先tushare,缺权限兜底akshare)

    # AlphaFeed 行情数据源（A股/美股/港股 K线，REST: X-API-Key 认证）
    ALPHAFEED_KEY: str | None = None
    ALPHAFEED_BASE_URL: str = "https://api.alphafeed.org"

    # Pandadata 数据源（A股日线/前复权/财报等；SDK panda_data==0.0.12，需 init_token 鉴权）
    # 凭据由 Pandadata 服务方提供，base_url 对应其 JAVA_SERVICE_BASE_URL。
    # 未配置时 pandadata 路径不可用（init_token 失败），不影响其他数据源。
    PANDADATA_USERNAME: str | None = None
    PANDADATA_PASSWORD: str | None = None
    PANDADATA_BASE_URL: str | None = None

    # ---- WebSocket 长连接（dc 作为客户端主动连入 backend，1 对多）----
    # 设计见 docs/plans/2026-09-04.ws-dc-backend.md
    # 总开关：关闭时 dc 行为与改造前完全一致（纯 HTTP），便于一键回退
    WS_ENABLED: bool = False
    # backend 的 WS 地址（ws:// / wss://）；留空则由 BACKEND_BASE_URL 自动推导
    BACKEND_WS_URL: str = ""
    # 本实例身份：backend 连接注册表的键，多实例部署时必须唯一
    WS_INSTANCE_ID: str = ""
    # 对应 backend cleaner_services.service_code（副本归属）
    WS_SERVICE_CODE: str = ""
    # 心跳：WS 原生 ping 间隔 / 超时（秒）。
    # 注意：LB / 代理的空闲超时必须大于 ping 间隔，否则连接会被静默踢掉
    WS_PING_INTERVAL_SEC: float = 15.0
    WS_PING_TIMEOUT_SEC: float = 20.0
    WS_OPEN_TIMEOUT_SEC: float = 10.0
    # 重连：指数退避 + 抖动（按 instance_id 哈希错峰，防多实例惊群）
    WS_RECONNECT_MIN_SEC: float = 0.5
    WS_RECONNECT_MAX_SEC: float = 30.0
    WS_RECONNECT_MAX_TIMES: int = 0  # 0 表示不限制次数（由熔断逻辑控制）
    # outbox：发送侧缓冲
    WS_OUTBOX_TTL_HOURS: int = 24
    WS_OUTBOX_MAX_RECORDS: int = 10000
    # 单帧上限（字节），与 protocol.MAX_FRAME_BYTES 对齐
    WS_MAX_FRAME_BYTES: int = 256 * 1024
    # 未 ack 消息数上限（背压保护）
    WS_MAX_INFLIGHT: int = 1000

    # ---- 市场情报模块（intel）：dc 内可选子模块 ----
    # 设计见 docs/memo/intel-module-design.md / intel-module-plan.md
    # 总开关：关闭时 intel 路由/调度/迁移入口全部跳过，dc 行为与改造前一致，
    # 且 intel 重型依赖（LLM SDK 等）不会被 import（沿用 dc 既有 Dockerfile，不新建镜像）。
    # 决策（2026-09-07）：RSS 先行 / LLM 先云端 / 做有效性检验。
    INTEL_ENABLED: bool = False
    # LLM 提供方：cloud（云端 API）/ local（本地 ollama 等）；决策=先云端
    INTEL_LLM_PROVIDER: str = "cloud"
    INTEL_LLM_BASE_URL: str = ""
    INTEL_LLM_API_KEY: str = ""
    INTEL_LLM_MODEL: str = ""
    # 成本护栏：LLM 日预算（元），超限停批并告警（不静默降级）
    INTEL_DAILY_BUDGET_YUAN: float = 10.0
    # LLM 抽取：送审文本最大字符数（超长截断，成本护栏）；价目按每百万 tokens 人民币
    INTEL_LLM_MAX_INPUT_CHARS: int = 8000
    INTEL_LLM_MAX_TOKENS: int = 4096          # reasoning 模型思考+输出上限，太小会空 content
    INTEL_LLM_TIMEOUT_SEC: float = 120.0      # reasoning 模型长文可到 60s+，60 不够
    INTEL_LLM_CONCURRENCY: int = 4            # 批抽取并发数
    INTEL_LLM_TPM_BACKOFF_SEC: float = 60.0   # 429 tpm 配额耗尽时的退避（等一个配额窗口）
    INTEL_LLM_JSON_MODE: bool = False         # response_format json_object；vLLM+Qwen3.6 实测病态，默认关
    # 额外请求体（JSON 字符串，空=不发送）。vLLM Qwen3 混合思考模型需
    # {"enable_thinking": false}——否则思考烧光 max_tokens、content 为空
    INTEL_LLM_EXTRA_BODY_JSON: str = ""
    INTEL_LLM_PRICE_IN_CNY_PER_M: float = 2.0
    INTEL_LLM_PRICE_OUT_CNY_PER_M: float = 8.0
    # RSS 轮询间隔（秒），准实时摄取
    INTEL_RSS_POLL_SEC: int = 300
    # 原文落盘目录（磁盘契约：item 级原文 HTML，文件名=content_hash）
    INTEL_RAW_DIR: str = "./data/intel/raw"
    # 转载识别：64 位 simhash 汉明距离阈值（经典 3）；比对回看窗口（天）
    INTEL_SIMHASH_HAMMING: int = 3
    INTEL_DEDUPE_WINDOW_DAYS: int = 30
    # 拉取：单源超时（秒）/ 并发线程数 / 每源单轮最大条目
    INTEL_HTTP_TIMEOUT_SEC: float = 15.0
    INTEL_FETCH_WORKERS: int = 8
    INTEL_PER_SOURCE_LIMIT: int = 50
    # P4-2 微信半自动：目录 watch（浏览器插件/剪藏落地 ~/intel-inbox/ 自动入库）
    INTEL_INBOX_DIR: str = "~/intel-inbox"
    INTEL_INBOX_POLL_SEC: int = 30          # once=False 时的轮询间隔（秒）
    INTEL_INBOX_DEFAULT_SOURCE: str = "wechat-inbox"  # 扁平文件（无子目录）归入的源名
    INTEL_INBOX_COOLDOWN_SEC: float = 2.0   # 跳过 mtime 距现在 < 该值的文件（防读到半截）
    # P4-1 上传临时目录：**必须**与 inbox 分开。
    # 上传接口把文件暂存后解析入库，若落在 ~/intel-inbox/ 下，任何 inbox 监听
    # （P4-2 watch 的约定是"子目录=公众号"）都会把 _uploads/<源名>-<ts>/ 当成用户
    # 投放的文章**抢先入库**，于是上传接口自己再入库时判重命中 → 报 dup、且拿不到
    # 本次 doc_ids（上传后自动抽取会静默不抽）。目录分开是根治办法。
    INTEL_UPLOAD_TMP_DIR: str = "~/intel-uploads/tmp"
    # P4-3 RSSHub 可选源（默认关闭）：自建/第三方实例 base URL；
    # 源的 url 写 rsshub://<route> 时按此解析为真实 feed URL
    INTEL_RSSHUB_BASE_URL: str = ""
    # 每日情报构建（原 19:30 空占位，已实装）：抽取 → 画像 → 因子 → WS 因子变更广播
    # 抽取受 INTEL_DAILY_BUDGET_YUAN 二次约束（BudgetGate 逐调用拦截，不会超支）。
    INTEL_DAILY_BUILD_ENABLED: bool = True
    INTEL_DAILY_BUILD_HOUR: int = 19
    INTEL_DAILY_BUILD_MINUTE: int = 30
    INTEL_DAILY_BUILD_EXTRACT_LIMIT: int = 200  # 每轮最多送 LLM 的篇数（超出留待下一轮）
    # 抽取开关：关掉后 19:30 只刷画像与因子（零成本）。默认开——不抽取的话
    # 新入库的原文永远停在"未理解"状态（本模块存在的意义就是这条链）。
    INTEL_DAILY_BUILD_EXTRACT: bool = True
    # 画像是否每轮重算：D-9 起 author_profiles 按 **as_of（知识截止日）版本化**，
    # 跨天重算会**新增历史行**（老值保留、可回溯），不再原地覆盖。
    # ⚠️ 但 INTL_AUTHOR_CONVICTION 仍会取「最新 as_of」→ 当日因子值随之更新，
    #    想在回测期间冻结画像口径就关掉它，抽取与因子照跑。
    INTEL_DAILY_BUILD_PROFILES: bool = True
    # D-12：风格总结（P4-4）是否并入每日构建。**花费 LLM**，与抽取共用日预算
    # （BudgetGate 逐调用拦截，不会超支）。样本 < 3 mentions 的作者不送审（零成本）。
    # 默认关 = 保守上线：先手动跑一轮确认质量，再打开。
    INTEL_DAILY_BUILD_STYLES: bool = False
    # 单轮最多总结多少位作者（None = 不限；成本控制用，按 total_mentions 倒序取前 N）
    INTEL_DAILY_BUILD_STYLE_LIMIT: int | None = None
    INTEL_DAILY_BUILD_TIMEOUT_SEC: int = 3600   # 单轮硬超时（防拖垮 dc 盘后流水线）
    INTEL_DAILY_BUILD_EMIT_WS: bool = True      # 构建后广播 factor_updated（backend 增量同步）

    # 抽取优先级（D-3 固化）：人工投喂/公众号是**用户主动投喂**的内容，理应先于
    # 机器抓来的 RSS 欠账被理解。此前靠 ``ORDER BY d.id`` 升序，导致新灌的人工投喂
    # 排在几千条 RSS 老欠账之后饿死（实测欠账 1.28 万篇，东方财富 1.18 万 id 最小）。
    # 这里给出 source_type → 优先级（数字越小越优先）；未列出的类型按 100 处理。
    # 同优先级内按 available_at 倒序（先理解新的），最后用 d.id 兜底保证稳定排序。
    # 置空字符串可退回旧的「纯 id 升序」行为。
    INTEL_EXTRACT_PRIORITY: str = "manual:10,wechat:20,rss:50"

    # Application
    DEBUG: bool = True
    HOST: str = "0.0.0.0"
    PORT: int = 8100
    TZ: str = "Asia/Shanghai"

    # 主后端地址与策略内部下单令牌（模拟盘调仓时调用主后端内部端点）
    BACKEND_BASE_URL: str = "http://localhost:8000"
    STRATEGY_INTERNAL_TOKEN: str = ""

    # ---- 网关接入认证（阶段 A：供主后端 registry 管理）----
    # 多个 key 用逗号分隔；主后端在 registry 中保存对应 key，用于 QoS 轮询 / 因子拉取。
    # 留空表示关闭认证（开发期友好，生产务必配置）。
    # 注：声明为 str（pydantic-settings 的 EnvSettingsSource 对 list[str] 会按 JSON 解析，
    # 逗号分隔无法被 env 源正确读取），解析后的列表通过 property `api_keys` 暴露。
    SERVICE_NAME: str = "cleaner-dev"
    API_KEYS: str = ""                          # 例: "k_prod_xxx,k_staging_yyy"

    @property
    def api_keys(self) -> list[str]:
        """解析后的 API key 列表"""
        return [k.strip() for k in self.API_KEYS.split(",") if k.strip()] if self.API_KEYS else []

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def factor_data_path(self) -> Path:
        """因子数据目录（自动创建）"""
        path = Path(self.FACTOR_DATA_DIR)
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    """配置单例（进程内缓存）"""
    return Settings()


settings = get_settings()
