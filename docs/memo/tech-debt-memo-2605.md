 === 技术债务清单 ===

    【高优先级】

    1. 前端缺少测试覆盖
       - 后端无任何测试文件
       - 前端测试仅覆盖了 vben-admin 基础包（stores、utils、shared）

  ⏱ Timeout — secret capture cancelled
       - 业务代码（API 层、页面组件、量化模块、交易模块）零测试

    2. 权限控制组件功能不完整
       - access-control.vue 中明确标记 TODO：
         - 不支持多个权限码/角色的组合逻辑（AND/OR）
         - 不支持自定义判断逻辑
         - 当前只支持单一权限码校验

    3. 大量 @ts-expect-error 绕过类型检查
       - 14 处 @ts-expect-error 分布在 layout-ui、form-ui、tabs、modal、drawer、json-viewer、theme-button、use-sortable 等核心组件中
       - 说明存在类型声明缺失或类型不匹配问题未修复

    【中优先级】

    4. 量化模块 API 未对接真实交易所
       - quant.py 230 行，trading.py 348 行
       - 依赖了 yfinance、ccxt、TA-Lib，但后端 API 是否真正连接交易所/数据源需确认
       - ccxt 4.2.25 版本较新，需关注兼容性

    5. 后端依赖版本较旧
       - fastapi 0.121.1（已有较新版本）
       - sqlalchemy 2.0.23（未用最新 2.x）
       - pydantic 2.7.0
       - uvicorn 0.25.0
       - 需确认是否有安全补丁

    6. 路由模块（generate-routes / merge-route）存在硬编码问题
       - generate-routes-frontend.test.ts 和 merge-route-modules.test.ts 暗示路由生成逻辑复杂且容易出错

    【低优先级】

    7. Docker 环境缺少 secret 管理
       - SECRET_KEY 通过 pydantic-settings 读取环境变量，但未配置 docker-compose 的 secret 机制
       - 生产环境建议用 Docker secrets 或 vault

    8. 前端 37 个 devDependencies 可能需审计
       - 部分包可能已不再使用（死代码）

    9. 文档中的 TODO 模板未清理
       - docs/plans/README.md 中有 [ ] 计划1、[ ] 计划2 等示例模板残留

    【已确认无问题】

    - 无硬编码密码/密钥
    - 后端密码使用 bcrypt 哈希存储（正确）
    - JWT 使用 SECRET_KEY 签名（从环境变量读取，非硬编码）
    - 后端代码中无 TODO/FIXME/HACK 标记