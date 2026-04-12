# Quanty - 量化交易系统

<p align="center">
  <img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT">
  <img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python: 3.11+">
  <img src="https://img.shields.io/badge/vue-3.x-green.svg" alt="Vue: 3.x">
  <img src="https://img.shields.io/badge/FastAPI-0.104+-teal.svg" alt="FastAPI">
</p>

Quanty 是一个功能完善的企业级量化交易系统，提供从策略开发、回测验证到性能分析的一站式解决方案。

## ✨ 核心特性

### 💡 量化回测引擎
- **多数据源支持**: Yahoo Finance（股票）、CCXT（加密货币）
- **安全执行环境**: 隔离策略代码，保障系统安全
- **基础指标计算**: 总收益率、夏普比率、最大回撤、胜率、交易次数
- **策略验证**: 语法检查、安全性检查、必需函数检查

### 📊 技术指标系统
- **30+ 技术指标**: SMA、EMA、RSI、MACD、布林带、随机指标、ATR 等
- **数据自动增强**: 为价格数据自动添加技术指标
- **交易信号生成**: 基于技术指标自动生成买卖信号
- **多指标组合**: 支持多指标综合信号计算

### 📈 高级性能分析
- **收益指标**: 总收益、年化收益、月度/年度收益统计
- **风险指标**: 波动率、下行波动率、VaR、CVaR、偏度、峰度
- **风险调整收益**: 夏普比率、索提诺比率、卡尔玛比率、信息比率
- **交易分析**: 盈亏统计、持仓时间、盈利因子
- **回撤分析**: 最大回撤、平均回撤、回撤持续时间
- **基准比较**: Beta、Alpha、跟踪误差、上涨/下跌捕获率

### 🖥️ 现代化前端界面
- **策略管理**: 搜索、筛选、性能概览
- **策略编辑器**: 代码高亮、模板插入、实时验证
- **回测配置**: 标的选择、时间范围、初始资金设置
- **可视化图表**: 净值曲线、收益分布、回撤分析

## 🚀 快速开始

### 环境要求

- Python 3.11+
- Node.js 18+
- PostgreSQL 14+
- Redis 7+

### 安装步骤

#### 1. 克隆仓库

```bash
git clone https://github.com/senquan/quanty.git
cd quanty
```

#### 2. 后端启动

```bash
cd backend

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或: venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 文件，配置数据库连接等

# 运行数据库迁移
alembic upgrade head

# 启动服务
python main.py
```

后端服务将在 `http://localhost:8000` 启动。

#### 3. 前端启动

```bash
cd frontend

# 安装依赖（使用 pnpm）
pnpm install

# 启动开发服务器
pnpm dev
```

前端应用将在 `http://localhost:3000` 启动。

## 📁 项目结构

```
quanty/
├── backend/                 # 后端服务（FastAPI）
│   ├── app/
│   │   ├── services/        # 核心业务逻辑
│   │   │   ├── backtest_engine.py      # 回测引擎
│   │   │   ├── technical_indicators.py # 技术指标
│   │   │   └── performance_analyzer.py # 性能分析
│   │   ├── api/             # API 路由
│   │   └── main.py          # 应用入口
│   ├── alembic/             # 数据库迁移
│   └── requirements.txt     # Python 依赖
├── frontend/                # 前端应用（Vue3 + VBEN Admin）
│   ├── src/
│   │   ├── views/quant/     # 量化交易模块
│   │   └── api/             # API 接口定义
│   └── package.json         # Node 依赖
├── docs/                    # 文档
├── FEATURES.md              # 功能详情
└── LICENSE                  # MIT 许可证
```

## 🔌 API 接口

### 策略管理
- `POST /api/v1/quant/strategies` - 创建策略
- `GET /api/v1/quant/strategies` - 获取策略列表
- `GET /api/v1/quant/strategies/{id}` - 获取策略详情
- `PUT /api/v1/quant/strategies/{id}` - 更新策略
- `DELETE /api/v1/quant/strategies/{id}` - 删除策略

### 回测功能
- `POST /api/v1/quant/backtest` - 运行回测
- `GET /api/v1/quant/backtest-history/{strategy_id}` - 获取回测历史
- `POST /api/v1/quant/validate-strategy` - 验证策略代码

### 数据服务
- `GET /api/v1/quant/market-data` - 获取市场数据

## 📝 使用示例

### 创建双均线策略

```python
strategy_code = """
def on_data(data, context):
    # 获取均线数据
    sma_20 = data['sma_20']
    sma_50 = data['sma_50']
    close = data['close']
    
    # 交易逻辑
    for i in range(50, len(data)):
        # 金叉买入
        if sma_20.iloc[i-1] <= sma_50.iloc[i-1] and sma_20.iloc[i] > sma_50.iloc[i]:
            buy(close.iloc[i])
        
        # 死叉卖出
        elif sma_20.iloc[i-1] >= sma_50.iloc[i-1] and sma_20.iloc[i] < sma_50.iloc[i]:
            position = get_position()
            if position > 0:
                sell(close.iloc[i], position)
"""
```

### 运行回测

```python
backtest_request = {
    "strategy_id": 1,
    "symbol": "AAPL",
    "data_source": "yahoo",
    "start_date": "2023-01-01",
    "end_date": "2023-12-31",
    "initial_capital": 100000.0
}
```

## 🛠️ 依赖

### 后端
- **FastAPI**: 高性能 Web 框架
- **pandas**: 数据分析处理
- **numpy**: 数值计算
- **yfinance**: Yahoo Finance 数据获取
- **ccxt**: 加密货币交易所接口
- **TA-Lib**: 技术分析库
- **scipy**: 科学计算

### 前端
- **Vue 3**: 渐进式 JavaScript 框架
- **Element Plus**: 组件库
- **VBEN Admin**: 企业级中后台前端解决方案
- **ECharts**: 图表可视化

## 📈 系统优势

1. **专业级回测引擎**: 支持多数据源、安全执行、详细分析
2. **丰富的技术指标**: 30+ 常用技术指标，自动生成交易信号
3. **全面性能分析**: 50+ 性能指标，与基准比较，风险评估
4. **现代化前端**: Vue3 + Element Plus，图表可视化，用户体验佳
5. **可扩展架构**: 模块化设计，易于添加新功能和数据源
6. **生产就绪**: 完整的错误处理、数据验证、安全检查

## 🗺️ 路线图

- [ ] 实盘交易接口（券商API集成）
- [ ] 机器学习策略支持
- [ ] 多资产组合回测（股票、期货、期权）
- [ ] 策略实时监控与报警
- [ ] 社区策略分享平台
- [ ] 云端部署与分布式计算

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

[MIT License](LICENSE)

## 👤 作者

- **Senquan ZHU** - *Initial work* - [senquan](https://github.com/senquan)

---

<p align="center">Built with ❤️ for quantitative trading</p>
