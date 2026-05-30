import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import yfinance as yf
import ccxt
from abc import ABC, abstractmethod

class DataSource(ABC):
    """数据源抽象基类"""
    
    @abstractmethod
    def get_historical_data(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        pass

class YahooFinanceDataSource(DataSource):
    """Yahoo Finance数据源"""
    
    def get_historical_data(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        try:
            ticker = yf.Ticker(symbol)
            data = ticker.history(start=start_date, end=end_date)
            data.columns = [col.lower().replace(' ', '_') for col in data.columns]
            return data
        except Exception as e:
            raise ValueError(f"Failed to fetch data from Yahoo Finance: {str(e)}")

class CCXTDataSource(DataSource):
    """加密货币数据源"""
    
    def __init__(self, exchange_name: str = 'binance'):
        self.exchange = getattr(ccxt, exchange_name)()
    
    def get_historical_data(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        try:
            # 转换时间格式
            since = self.exchange.parse8601(start_date)
            limit = 1000  # CCXT限制
            
            ohlcv = self.exchange.fetch_ohlcv(symbol, '1d', since, limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            return df
        except Exception as e:
            raise ValueError(f"Failed to fetch data from CCXT: {str(e)}")

class BacktestEngine:
    """回测引擎核心类"""
    
    def __init__(self, initial_capital: float = 100000):
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.position = 0
        self.trades = []
        self.daily_returns = []
        self.portfolio_values = []
        
    def execute_strategy(self, strategy_code: str, data: pd.DataFrame) -> Dict:
        """执行策略代码"""
        try:
            # 保存初始资金用于最终计算
            initial_capital_snapshot = self.initial_capital
            
            # 添加技术指标
            from app.services.technical_indicators import DataEnricher
            enriched_data = DataEnricher.add_technical_indicators(data)
            
            # 创建策略执行环境
            strategy_globals = {
                'np': np,
                'pd': pd,
                'data': enriched_data,
                'buy': self._buy,
                'sell': self._sell,
                'get_position': lambda: self.position,
                'get_capital': lambda: self.capital,
            }
            
            # 按天推进：在每个 bar 执行策略逻辑后计算组合价值
            portfolio_values = []
            
            # 先重置实例状态，避免多次调用时数据残留
            self.capital = initial_capital_snapshot
            
            # 执行策略代码（策略函数内部通过循环调用 buy/sell）
            exec(strategy_code, strategy_globals)
            
            # 策略执行后，按实际交易结果计算每日组合价值
            # 重新按天遍历，模拟逐日盈亏
            current_position = 0
            current_capital = initial_capital_snapshot
            trade_index = 0
            trades_sorted = sorted(self.trades, key=lambda t: t['timestamp'])
            
            for i in range(len(enriched_data)):
                current_price = enriched_data.iloc[i]['close']
                
                # 处理当天成交的交易
                while trade_index < len(trades_sorted) and trades_sorted[trade_index]['timestamp'].date() <= enriched_data.index[i].date():
                    trade = trades_sorted[trade_index]
                    if trade['type'] == 'buy':
                        current_position += trade['quantity']
                        current_capital -= trade['price'] * trade['quantity']
                    else:
                        current_position -= trade['quantity']
                        current_capital += trade['price'] * trade['quantity']
                    trade_index += 1
                
                # 组合价值 = 可用现金 + 持仓市值
                current_value = current_capital + (current_position * current_price)
                portfolio_values.append(current_value)
            
            return {
                'trades': self.trades,
                'final_capital': current_capital,
                'portfolio_values': portfolio_values,
                'daily_returns': self._calculate_daily_returns(portfolio_values)
            }
            
        except Exception as e:
            raise ValueError(f"Strategy execution failed: {str(e)}")
    
    def _buy(self, price: float, quantity: int = None):
        """买入操作"""
        if price is None or price <= 0:
            return  # 价格无效，跳过
        
        if quantity is None:
            quantity = int(self.capital / price)
        
        if quantity <= 0:
            return  # 数量为零，跳过
        
        cost = price * quantity
        if cost <= self.capital:
            self.capital -= cost
            self.position += quantity
            self.trades.append({
                'type': 'buy',
                'price': price,
                'quantity': quantity,
                'timestamp': pd.Timestamp.now()
            })
    
    def _sell(self, price: float, quantity: int = None):
        """卖出操作"""
        if price is None or price <= 0:
            return  # 价格无效，跳过
        
        if quantity is None:
            quantity = self.position
        
        if quantity <= 0:
            return  # 无持仓可卖
        
        if quantity > self.position:
            quantity = self.position  # 不超过实际持仓
        
        revenue = price * quantity
        self.capital += revenue
        self.position -= quantity
        self.trades.append({
            'type': 'sell',
            'price': price,
            'quantity': quantity,
            'timestamp': pd.Timestamp.now()
        })
    
    def calculate_metrics(self, results: Dict) -> Dict:
        """计算回测指标"""
        trades = results['trades']
        final_capital = results['final_capital']
        
        # 基础指标
        total_return = (final_capital - self.initial_capital) / self.initial_capital * 100
        
        # 交易统计
        winning_trades = [t for t in trades if t['type'] == 'sell']
        win_count = len(winning_trades)
        win_rate = (win_count / len(trades) * 100) if trades else 0
        
        # 最大回撤计算
        portfolio_values = results.get('portfolio_values', [])
        max_drawdown = self._calculate_max_drawdown(portfolio_values) if portfolio_values else 0
        
        # 夏普比率
        daily_returns = results.get('daily_returns', [])
        sharpe_ratio = self._calculate_sharpe_ratio(daily_returns) if daily_returns else 0
        
        # 总交易次数
        total_trades = len(trades)
        
        return {
            'total_return': round(total_return, 2),
            'sharpe_ratio': round(sharpe_ratio, 2),
            'max_drawdown': round(max_drawdown, 2),
            'win_rate': round(win_rate, 2),
            'total_trades': total_trades,
            'final_capital': round(final_capital, 2)
        }
    
    def _calculate_max_drawdown(self, portfolio_values: List[float]) -> float:
        """计算最大回撤"""
        if not portfolio_values:
            return 0
        
        peak = portfolio_values[0]
        max_dd = 0
        
        for value in portfolio_values:
            if value > peak:
                peak = value
            drawdown = (peak - value) / peak * 100
            max_dd = max(max_dd, drawdown)
        
        return max_dd
    
    def _calculate_sharpe_ratio(self, returns: List[float]) -> float:
        """计算夏普比率"""
        if not returns or len(returns) < 2:
            return 0
        
        returns_array = np.array(returns)
        mean_return = np.mean(returns_array)
        std_return = np.std(returns_array)
        
        if std_return == 0:
            return 0
        
        # 年化夏普比率 (假设252个交易日)
        sharpe = (mean_return / std_return) * np.sqrt(252)
        return sharpe
    
    def _calculate_daily_returns(self, portfolio_values: List[float]) -> List[float]:
        """计算每日收益率"""
        if len(portfolio_values) < 2:
            return []
        
        returns = []
        for i in range(1, len(portfolio_values)):
            daily_return = (portfolio_values[i] - portfolio_values[i-1]) / portfolio_values[i-1]
            returns.append(daily_return)
        
        return returns

class StrategyValidator:
    """策略代码验证器"""
    
    @staticmethod
    def validate_strategy(code: str) -> Dict:
        """验证策略代码"""
        errors = []
        warnings = []
        
        # 基础语法检查
        try:
            compile(code, '<string>', 'exec')
        except SyntaxError as e:
            errors.append(f"语法错误: {str(e)}")
            return {'valid': False, 'errors': errors, 'warnings': warnings}
        
        # 安全性检查
        dangerous_functions = ['eval', 'exec', 'open', 'file', 'input', '__import__']
        for func in dangerous_functions:
            if func in code:
                warnings.append(f"检测到危险函数: {func}")
        
        # 必需函数检查
        if 'buy' not in code:
            warnings.append("策略中没有找到买入操作")
        
        if 'sell' not in code:
            warnings.append("策略中没有找到卖出操作")
        
        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings
        }

class DataManager:
    """数据管理器"""
    
    def __init__(self):
        self.sources = {
            'yahoo': YahooFinanceDataSource(),
            'crypto': CCXTDataSource()
        }
    
    def get_data(self, source: str, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """获取历史数据"""
        if source not in self.sources:
            raise ValueError(f"Unsupported data source: {source}")
        
        return self.sources[source].get_historical_data(symbol, start_date, end_date)