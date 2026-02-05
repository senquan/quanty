#!/usr/bin/env python3
"""
量化交易系统测试脚本
用于验证回测引擎和API功能
"""

import asyncio
import sys
import os
from datetime import datetime, timedelta

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.backtest_engine import (
    BacktestEngine, DataManager, StrategyValidator
)
from app.services.performance_analyzer import PerformanceAnalyzer


def test_strategy_validation():
    """测试策略代码验证"""
    print("=== 测试策略验证 ===")
    
    validator = StrategyValidator()
    
    # 测试有效策略
    valid_code = """
import numpy as np
close = data['close']
for i in range(10, len(data)):
    if i % 50 == 0:  # 每50天买入一次
        buy(close.iloc[i])
    elif i % 100 == 0:  # 每100天卖出一次
        position = get_position()
        if position > 0:
            sell(close.iloc[i], position)
"""
    
    result = validator.validate_strategy(valid_code)
    print(f"有效策略验证结果: {result}")
    
    # 测试无效策略
    invalid_code = "invalid syntax here"
    result = validator.validate_strategy(invalid_code)
    print(f"无效策略验证结果: {result}")
    print()


def test_data_manager():
    """测试数据获取"""
    print("=== 测试数据管理器 ===")
    
    data_manager = DataManager()
    
    try:
        # 测试Yahoo Finance数据
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
        
        print(f"获取AAPL数据: {start_date} 到 {end_date}")
        data = data_manager.get_data('yahoo', 'AAPL', start_date, end_date)
        print(f"数据形状: {data.shape}")
        print(f"数据列: {list(data.columns)}")
        print(f"最新几行数据:\n{data.tail()}")
        print()
        
        return data
        
    except Exception as e:
        print(f"数据获取失败: {e}")
        return None


def test_backtest_engine():
    """测试回测引擎"""
    print("=== 测试回测引擎 ===")
    
    # 简单的测试策略
    simple_strategy = """
import numpy as np
close = data['close']

# 简单的买入持有策略
for i in range(10, len(data)):
    if i == 10:  # 第10天买入
        buy(close.iloc[i])
    elif i == len(data) - 1:  # 最后一天卖出
        position = get_position()
        if position > 0:
            sell(close.iloc[i], position)
"""
    
    try:
        # 创建测试数据
        import pandas as pd
        dates = pd.date_range('2023-01-01', periods=100, freq='D')
        test_data = pd.DataFrame({
            'close': np.random.uniform(100, 150, 100),
            'high': np.random.uniform(105, 155, 100),
            'low': np.random.uniform(95, 145, 100),
            'volume': np.random.randint(1000000, 5000000, 100)
        }, index=dates)
        
        # 执行回测
        engine = BacktestEngine(initial_capital=100000)
        results = engine.execute_strategy(simple_strategy, test_data)
        metrics = engine.calculate_metrics(results)
        
        print(f"回测结果:")
        print(f"  交易次数: {len(results['trades'])}")
        print(f"  最终资金: {results['final_capital']:.2f}")
        print(f"  总收益率: {metrics['total_return']:.2f}%")
        print(f"  夏普比率: {metrics['sharpe_ratio']:.2f}")
        print(f"  最大回撤: {metrics['max_drawdown']:.2f}%")
        print()
        
        return results, metrics
        
    except Exception as e:
        print(f"回测执行失败: {e}")
        return None, None


def test_performance_analyzer():
    """测试性能分析器"""
    print("=== 测试性能分析器 ===")
    
    try:
        # 创建模拟数据
        portfolio_values = [100000, 102000, 98000, 105000, 103000, 108000, 110000]
        trades = [
            {'type': 'buy', 'price': 100, 'quantity': 100, 'timestamp': datetime.now()},
            {'type': 'sell', 'price': 105, 'quantity': 50, 'timestamp': datetime.now()},
            {'type': 'buy', 'price': 98, 'quantity': 100, 'timestamp': datetime.now()},
            {'type': 'sell', 'price': 110, 'quantity': 150, 'timestamp': datetime.now()}
        ]
        
        analyzer = PerformanceAnalyzer(portfolio_values, trades)
        analysis = analyzer.comprehensive_analysis()
        
        print("高级性能分析结果:")
        for key, value in analysis.items():
            print(f"  {key}: {value}")
        print()
        
    except Exception as e:
        print(f"性能分析失败: {e}")


def test_strategy_template():
    """测试策略模板"""
    print("=== 测试策略模板 ===")
    
    # RSI策略模板
    rsi_strategy = """
# RSI超买超卖策略
import numpy as np

rsi = data['rsi']
close = data['close']

for i in range(20, len(data)):
    current_price = close.iloc[i]
    current_rsi = rsi.iloc[i]
    
    # RSI < 30 超卖买入
    if current_rsi < 30 and rsi.iloc[i-1] >= 30:
        buy(current_price)
    
    # RSI > 70 超买卖出
    elif current_rsi > 70 and rsi.iloc[i-1] <= 70:
        position = get_position()
        if position > 0:
            sell(current_price, position)
"""
    
    validator = StrategyValidator()
    result = validator.validate_strategy(rsi_strategy)
    print(f"RSI策略模板验证: {result}")
    print()


def main():
    """主测试函数"""
    print("🚀 开始量化交易系统测试\n")
    
    # 运行各项测试
    test_strategy_validation()
    test_data_manager()
    test_backtest_engine()
    test_performance_analyzer()
    test_strategy_template()
    
    print("✅ 所有测试完成！")


if __name__ == "__main__":
    main()