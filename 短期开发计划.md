# Quanty 短期开发计划

> 📅 制定日期: 2026-04-24
> 📌 计划周期: 2-3 周
> 🎯 目标: 修复关键问题，完善核心功能，提升代码质量

---

## 📊 一、项目现状总结

### 技术栈
- **后端**: Python 3.11+ / FastAPI / SQLAlchemy (Async) / PostgreSQL
- **前端**: Vue 3.x / TypeScript / Element Plus / Vite
- **基础设施**: Redis / Gitea (本地 Git) / GitHub (远程)

### 当前状态
| 模块 | 完成度 | 说明 |
|------|--------|------|
| 用户认证 | ✅ 80% | 登录/注册/令牌刷新已完成，权限系统待完善 |
| 权限管理 | ⚠️ 50% | 基础CRUD完成，权限路由未接入 |
| 量化策略 | ⚠️ 40% | 策略CRUD部分完成，缺少更新/删除端点 |
| 回测引擎 | ✅ 60% | 基础回测完成，高级分析待补充 |
| 模拟交易 | ✅ 70% | 华泰模拟交易API完成 |
| 前端页面 | ⚠️ 30% | 框架搭好，大部分页面仍是mock数据 |

---

## 🎯 二、短期开发计划 (2-3周)

### Phase 1: 核心功能完善 (第1周)

#### P0-1: 策略管理 - 补全更新/删除端点
**优先级**: 🔴 P0
**预估工时**: 1 天

```
后端:
❌ 缺失: PUT /api/v1/quant/strategies/{strategy_id}
  - 需实现策略代码更新、名称更新、描述更新
  - 更新时重新验证策略代码 (调用 validate_strategy)
  - 记录更新者为当前用户

❌ 缺失: DELETE /api/v1/quant/strategies/{strategy_id}
  - 权限检查: 仅策略创建者可删除
  - 级联删除: 关联的回测结果
  - 软删除标记 or 硬删除
```

**任务清单**:
- [ ] `backend/app/api/api_v1/endpoints/quant.py` 添加 update/DELETE 路由
- [ ] 策略更新添加代码验证逻辑
- [ ] 关联 backtest_results 清理
- [ ] 前端 `updateStrategyApi` / `deleteStrategyApi` 接入真实后端

---

#### P0-2: 类型错误修复 - Session / AsyncSession 混用
**优先级**: 🔴 P0
**预估工时**: 0.5 天

```python
# ❌ quant.py (Line 26, 57, 71, etc.)
from sqlalchemy.orm import Session  # sync type
db: Session = Depends(get_db)      # 但 get_db 返回 AsyncSession

# ✅ 统一为
from sqlalchemy.ext.asyncio import AsyncSession
db: AsyncSession = Depends(get_db)
```

**文件**:
- `backend/app/api/api_v1/endpoints/quant.py` - 全部改为 AsyncSession

---

#### P0-3: 前端策略列表对接真实数据
**优先级**: 🟠 P1
**预估工时**: 1 天

```typescript
// frontend/apps/web-ele/src/api/quant.ts
// ✅ 已有API调用方法:
// - getStrategiesApi()
// - getStrategyApi()
// - createStrategyApi()
// - updateStrategyApi()
// - deleteStrategyApi()

// 需要: 前端页面调用
/* frontend/apps/web-ele/src/views/quant/strategy/
   ❌ index.vue - 硬编码strategies数组 (Line 37)
   ✅ edit.vue - 需要完善表单提交逻辑
*/
```

**任务清单**:
- [ ] `strategy/index.vue` - getStrategiesApi 对接
- [ ] `strategy/edit.vue` - create/update 提交逻辑
- [ ] 策略删除确认对话框
- [ ] 加载状态和错误处理

---

### Phase 2: 权限系统增强 (第2周)

#### P1-1: 动态路由 - 基于角色权限
**优先级**: 🟠 P1
**预估工时**: 2 天

```
前端: /frontend/apps/web-ele/src/router/
❌ 当前: 所有路由在路由表中定义，所有用户可见
✅ 目标: 根据用户角色动态生成可访问路由

需要:
1. 获取用户权限列表 (从 /api/v1/roles/self 或 token claims)
2. 路由表过滤: accessible.ts 完善权限判断
3. 路由守卫: guard.ts 校验路由权限
```

**参考文件**:
- `frontend/apps/web-ele/src/router/access.ts`
- `frontend/apps/web-ele/src/router/guard.ts`
- `frontend/packages/effects/access/src/`

---

#### P1-2: 菜单权限展示
**优先级**: 🟠 P1
**预估工时**: 1 天

```python
# 后端: 在角色响应中返回树状权限菜单
# roles.py /{role_id} 端点修改:

# ✅ 现有
result = select(RolePermission).filter(...)
permissions = perm_result.scalars().all()

# 改为: 返回带层级的菜单权限
def get_menu_tree(permissions):
    """将平铺的权限列表转为树状结构"""
    ...
```

**任务清单**:
- [ ] 后端返回树状权限菜单
- [ ] 前端侧边栏根据权限渲染菜单
- [ ] 无权限菜单隐藏

---

#### P1-3: 接口权限控制中间件
**优先级**: 🟡 P2
**预估工时**: 1.5 天

```python
# 后端: 添加基于角色的接口级权限
@router.get("/strategies")
async def get_strategies(
    current_user: User = Depends(get_current_user),
    permission: str = Depends(check_permission("strategy:view")),
    ...
):
```

**任务清单**:
- [ ] 接口权限装饰器 `check_permission`
- [ ] RBAC校验中间件
- [ ] 角色->权限映射完善 (RolePermission 表)

---

### Phase 3: 回测功能增强 (第2-3周)

#### P1-4: 回测结果持久化
**优先级**: 🟠 P1
**预估工时**: 1 天

```python
# backend/app/models/quant.py
# ❌ BacktestResult 缺少:
# - strategy_name (冗余字段，便于查询)
# - detailed_metrics (JSON字段存储详细分析结果)
# - run_duration (回测耗时)

# ✅ 需要新增字段:
detailed_metrics = Column(Text)  # JSON格式存储
strategy_name = Column(String)   # 冗余，便于搜索
run_duration = Column(Float)     # 秒
error_message = Column(Text)     # 回测失败时记录
```

---

#### P1-5: 前端回测结果图表优化
**优先级**: 🟡 P2
**预估工时**: 1.5 天

```vue
<!-- frontend/apps/web-ele/src/views/quant/backtest/ -->
<!-- ❌ index.vue - 全部图表使用mock数据 -->
<!-- ✅ 目标: 接入真实回测结果 -->

需要实现的图表:
1. 📈 净值曲线对比 (策略 vs 基准) - ECharts
2. 📊 日收益分布直方图
3. 📉 回撤分析图
4. 📋 盈亏统计表格 (按标的)
5. 🔥 月度收益热力图
```

**任务清单**:
- [ ] 接入真实 backtest API
- [ ] ECharts 图表适配真实数据结构
- [ ] 新增: 盈亏分布热力图
- [ ] 新增: 交易明细列表

---

#### P1-6: 回测数据增强接口
**优先级**: 🟡 P2
**预估工时**: 1 天

```python
# POST /api/v1/quant/analyze
# 输入: 交易记录列表
# 输出: 高级分析指标

# 使用 performance_analyzer.py:
from app.services.performance_analyzer import PerformanceAnalyzer

def comprehensive_analysis(portfolio_values, trades):
    return {
        "returns_metrics": {...},      # 年化收益、波动率等
        "risk_adjusted": {...},        # Sharpe, Sortino, Calmar, Info Ratio
        "drawdown_analysis": {...},    # MaxDD, AvgDD, Duration
        "benchmark_comparison": {...}, # Alpha, Beta, Tracking Error
    }
```

---

### Phase 4: 基础设施与代码质量 (第3周)

#### P2-1: 代码格式化与静态检查
**优先级**: 🟡 P2
**预估工时**: 0.5 天

```
后端:
❌ 不一致: 导入顺序、缩进、类型注解
✅ 需要:
1. ruff (替代 black + isort): `ruff check . && ruff format .`
2. mypy: 类型检查
   pip install mypy
   mypy backend/app --ignore-missing-imports

前端:
❌ oxfmt / oxlint 已配置但需启用
✅ 需要: pnpm lint
```

---

#### P2-2: Alembic 迁移管理
**优先级**: 🟡 P2
**预估工时**: 1 天

```bash
# 当前: 手动修改 model + 同步数据库
# 目标: Alembic 自动化迁移管理

1. alembic revision --autogenerate -m "add fields..."
2. alembic upgrade head

需迁移的变更:
- BacktestStrategy.add(detailed_metrics, strategy_name)
- User.add(role_id_nullable constraint)
- 添加审计日志表
```

---

#### P2-3: API 文档完善
**优先级**: 🟢 P3
**预估工时**: 0.5 天

```python
# FastAPI 自动生成 Swagger (已开启 /api/docs)
# 需要: 完善 docstring, examples

@router.post("/strategies", response_model=StrategyResponse)
async def create_strategy(
    strategy_data: StrategyCreate = Body(
        ...,
        examples=[{
            "name": "双均线策略",
            "description": "基于金叉死叉",
            "code": "import pandas...\n"
        }]
    )
):
    """创建量化策略"""
```

---

## 📋 三、任务优先级矩阵

| 任务 | 优先级 | 影响 | 工作量 | 建议阶段 |
|------|--------|------|--------|----------|
| P0-1 策略更新/删除端点 | P0 | 阻塞策略完整管理 | 1天 | Week 1 |
| P0-2 Session类型修复 | P0 | 潜在运行时错误 | 0.5天 | Week 1 |
| P0-3 前端策略列表对接 | P1 | 用户可直接使用 | 1天 | Week 1 |
| P1-1 动态路由 | P1 | 用户体验大幅提升 | 2天 | Week 2 |
| P1-2 菜单权限 | P1 | 安全展示 | 1天 | Week 2 |
| P1-3 接口权限中间件 | P2 | 安全加固 | 1.5天 | Week 2 |
| P1-4 回测持久化 | P1 | 数据完整性 | 1天 | Week 2 |
| P1-5 回测图表优化 | P2 | 分析能力 | 1.5天 | Week 3 |
| P1-6 回测分析接口 | P2 | 分析能力 | 1天 | Week 3 |
| P2-1 代码格式化 | P2 | 可维护性 | 0.5天 | Week 3 |
| P2-2 Alembic迁移 | P2 | 部署可靠性 | 1天 | Week 3 |
| P2-3 API文档 | P3 | 协便利性 | 0.5天 | Week 3 |

---

## 🔧 四、技术债务清单

### 立即修复
1. ❌ `quant.py` 中使用 `Session` 而非 `AsyncSession` (可能导致并发问题)
2. ❌ 策略更新/删除端点缺失 (前端 API 已定义但后端未实现)
3. ⚠️ `User.gander` 拼写错误 → 应改为 `gender`

### 中期优化
1. ⚠️ 缺少数据库迁移工具 (手动改model → 容易遗漏)
2. ⚠️ 无单元测试 (核心逻辑未受保护)
3. ⚠️ 密码字段未验证强度
4. ⚠️ Token 无刷新/轮换机制 (只有 refresh_token，无 rotation)

### 长期规划
1. 📌 WebSocket 支持实时行情推送
2. 📌 策略版本管理
3. 📌 回测结果对比 (Multiple strategies comparison)
4. 📌 Docker Compose 一键部署
5. 📌 CI/CD 流水线 (GitHub Actions)
6. 📌 策略市场 (社区分享)

---

## 📝 五、验收标准

### Week 1 交付物
- [ ] 策略 CRUD 完整实现
- [ ] Session 类型统一
- [ ] 前端策略列表可操作
- [ ] 代码通过 `ruff check`

### Week 2 交付物
- [ ] 用户登录后能看到自己权限对应的菜单
- [ ] 菜单权限与角色关联
- [ ] 接口级别权限控制 (可选)
- [ ] 回测结果可持久化查询

### Week 3 交付物
- [ ] 前端回测图表使用真实数据
- [ ] 新增高级分析图表
- [ ] Alembic 迁移脚本就位
- [ ] API 文档完善

---

## 🎨 六、补充说明

### 开发规范
```
1. 后端: 全部使用 async/await
2. 类型注解: 函数签名必须包含参数和返回值类型
3. 错误处理: FastAPI HTTPException 统一使用
4. 数据库: select() 语法, 不使用 query()
5. Pydantic v2: 使用 model_dump() 替代 dict()
6. 前端: TypeScript strict mode
7. 组件: Vue 3 + Composition API + <script setup>
```

### 注意事项
- ⚠️ `User.gander` 字段拼写错误 (已录入数据库)，后续需处理迁移
- ⚠️ 模拟交易 (`huatai_trading.py`) 暂无真实券商接入
- ⚠️ 前端 `monacoEditorRef` 类型声明需确认
- ⚠️ GitHub Token 需更新权限支持 PR API

---

> 💡 此开发计划由 Hermes Agent 基于代码分析自动生成
> 可根据实际情况调整任务优先级和排期
