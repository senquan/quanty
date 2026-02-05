# Quant Backend API

基于 FastAPI + PostgreSQL 的量化交易系统后端服务

## 项目结构

```
backend/
├── app/                          # 应用核心代码
│   ├── api/                      # API 接口层
│   │   └── api_v1/
│   │       ├── endpoints/        # 各个端点模块
│   │       │   ├── auth.py       # 认证相关接口
│   │       │   ├── users.py      # 用户管理接口
│   │       │   └── quant.py      # 量化策略接口
│   │       └── api.py            # API 路由配置
│   ├── core/                     # 核心模块
│   │   ├── config.py            # 配置管理
│   │   ├── database.py          # 数据库配置
│   │   ├── dependencies.py      # 依赖注入
│   │   └── security.py          # 安全模块
│   ├── models/                   # 数据模型
│   │   ├── user.py              # 用户模型
│   │   └── quant.py             # 量化策略模型
│   └── schemas/                  # 数据验证模型
│       ├── user.py              # 用户相关DTO
│       └── quant.py             # 量化相关DTO
├── alembic/                      # 数据库迁移工具
├── scripts/                      # 脚本文件
├── main.py                       # 应用入口
├── requirements.txt             # Python依赖
├── .env.example                 # 环境变量示例
├── docker-compose.yml           # Docker Compose配置
└── Dockerfile                    # Docker容器配置
```

## 📊 技术栈

- **FastAPI**: 高性能异步框架
- **PostgreSQL**: 关系型数据库
- **SQLAlchemy**: ORM数据库操作
- **Pydantic**: 数据验证和序列化
- **Alembic**: 数据库迁移管理
- **JWT**: 安全的认证机制
- **Docker**: 容器化部署

## 🚀 快速开始

### 1. 使用 Docker Compose (推荐)

```bash
# 启动所有服务
cd backend
docker-compose up -d

# 查看服务状态
docker-compose ps

# 停止服务
docker-compose down
```

### 2. 本地开发

#### 安装依赖
```bash
pip install -r requirements.txt
```

#### 配置环境变量
```bash
cp .env.example .env
# 编辑 .env 文件配置数据库和JWT密钥
```

#### 启动 PostgreSQL 数据库
```bash
# 使用 Docker 启动 PostgreSQL
docker run -d --name quant-postgres \
  -e POSTGRES_DB=quant_db \
  -e POSTGRES_USER=quant_user \
  -e POSTGRES_PASSWORD=quant_password \
  -p 5432:5432 \
  postgres:15
```

#### 数据库迁移
```bash
# 创建迁移文件
alembic revision --autogenerate -m "Initial migration"

# 应用迁移
alembic upgrade head
```

#### 启动服务
```bash
# 开发模式
python main.py

# 或使用uvicorn
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 3. 访问API文档

启动后访问：
- Swagger UI: http://localhost:8000/api/docs
- ReDoc: http://localhost:8000/api/redoc

## 📁 数据库配置

### PostgreSQL 连接配置
```python
DATABASE_URL=postgresql+asyncpg://username:password@localhost:5432/quant_db
```

### 支持的数据库连接方式
- **开发**: `postgresql+asyncpg://...` (异步连接)
- **生产**: `postgresql+psycopg2://...` (同步连接)

## 🔧 常用命令

### 数据库操作
```bash
# 创建新的迁移文件
alembic revision --autogenerate -m "描述"

# 应用所有迁移
alembic upgrade head

# 回滚到特定版本
alembic downgrade -1

# 查看迁移历史
alembic history
```

### 开发工具
```bash
# 运行测试
pytest

# 代码格式化
black .

# 代码检查
flake8
```

## 🐳 Docker 部署

### 构建镜像
```bash
docker build -t quant-backend .
```

### 运行容器
```bash
docker run -p 8000:8000 \
  -e DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db \
  -e SECRET_KEY=your-secret-key \
  quant-backend
```

## 📊 API 功能

### 认证模块
- 用户注册/登录 (JWT)
- 密码加密 (bcrypt)
- Token验证和过期管理

### 用户管理
- 获取当前用户信息
- 用户权限控制

### 量化策略
- 策略创建和存储
- 策略回测接口
- 策略结果分析

## 🔗 与前端集成

项目已配置允许前端本地开发服务器访问，支持端口 3000 和 5173 (Vite默认端口)。

## 📝 注意事项

1. **生产环境**: 确保修改默认的SECRET_KEY
2. **数据库安全**: 使用强密码和SSL连接
3. **性能优化**: 根据实际负载调整数据库连接池参数

项目已完全就绪，支持 PostgreSQL 数据库！