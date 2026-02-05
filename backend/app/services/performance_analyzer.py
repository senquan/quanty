import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta

class PerformanceAnalyzer:
    """高级性能分析器"""
    
    def __init__(self, portfolio_values: List[float], trades: List[Dict], 
                 benchmark_values: List[float] = None, risk_free_rate: float = 0.02):
        self.portfolio_values = portfolio_values
        self.trades = trades
        self.benchmark_values = benchmark_values
        self.risk_free_rate = risk_free_rate / 252  # 日化无风险利率
        
    def comprehensive_analysis(self) -> Dict:
        """综合性能分析"""
        if not self.portfolio_values:
            return {}
        
        analysis = {}
        
        # 基础收益指标
        analysis.update(self._calculate_return_metrics())
        
        # 风险指标
        analysis.update(self._calculate_risk_metrics())
        
        # 风险调整收益指标
        analysis.update(self._calculate_risk_adjusted_metrics())
        
        # 交易分析
        analysis.update(self._analyze_trades())
        
        # 回撤分析
        analysis.update(self._analyze_drawdowns())
        
        # 基准比较
        if self.benchmark_values:
            analysis.update(self._benchmark_comparison())
        
        return analysis
    
    def _calculate_return_metrics(self) -> Dict:
        """计算收益指标"""
        values = np.array(self.portfolio_values)
        returns = np.diff(values) / values[:-1]
        
        total_return = (values[-1] - values[0]) / values[0] * 100
        annualized_return = (values[-1] / values[0]) ** (252 / len(values)) - 1
        
        # 计算月度、年度收益
        monthly_returns = self._calculate_periodic_returns(returns, 21)  # 21交易日/月
        yearly_returns = self._calculate_periodic_returns(returns, 252)  # 252交易日/年
        
        return {
            'total_return': round(total_return, 2),
            'annualized_return': round(annualized_return * 100, 2),
            'monthly_avg_return': round(np.mean(monthly_returns) * 100, 2) if monthly_returns else 0,
            'yearly_avg_return': round(np.mean(yearly_returns) * 100, 2) if yearly_returns else 0,
            'best_day': round(np.max(returns) * 100, 2) if len(returns) > 0 else 0,
            'worst_day': round(np.min(returns) * 100, 2) if len(returns) > 0 else 0,
        }
    
    def _calculate_risk_metrics(self) -> Dict:
        """计算风险指标"""
        values = np.array(self.portfolio_values)
        returns = np.diff(values) / values[:-1]
        
        volatility = np.std(returns) * np.sqrt(252) * 100  # 年化波动率
        downside_returns = returns[returns < 0]
        downside_volatility = np.std(downside_returns) * np.sqrt(252) * 100 if len(downside_returns) > 0 else 0
        
        # VaR和CVaR (95%置信度)
        var_95 = np.percentile(returns, 5) * 100 if len(returns) > 0 else 0
        cvar_95 = np.mean(downside_returns) * 100 if len(downside_returns) > 0 else 0
        
        return {
            'volatility': round(volatility, 2),
            'downside_volatility': round(downside_volatility, 2),
            'var_95': round(var_95, 2),
            'cvar_95': round(cvar_95, 2),
            'skewness': round(self._calculate_skewness(returns), 4) if len(returns) > 0 else 0,
            'kurtosis': round(self._calculate_kurtosis(returns), 4) if len(returns) > 0 else 0,
        }
    
    def _calculate_risk_adjusted_metrics(self) -> Dict:
        """计算风险调整收益指标"""
        values = np.array(self.portfolio_values)
        returns = np.diff(values) / values[:-1]
        
        if len(returns) == 0:
            return {'sharpe_ratio': 0, 'sortino_ratio': 0, 'calmar_ratio': 0, 'information_ratio': 0}
        
        # 夏普比率
        excess_returns = returns - self.risk_free_rate
        sharpe_ratio = np.mean(excess_returns) / np.std(excess_returns) * np.sqrt(252)
        
        # 索提诺比率
        downside_returns = returns[returns < 0]
        sortino_ratio = (np.mean(returns) - self.risk_free_rate) / np.std(downside_returns) * np.sqrt(252) if len(downside_returns) > 0 else 0
        
        # 卡尔玛比率 (年化收益/最大回撤)
        max_drawdown = self._calculate_max_drawdown_percentage()
        annualized_return = (values[-1] / values[0]) ** (252 / len(values)) - 1
        calmar_ratio = annualized_return / abs(max_drawdown) if max_drawdown != 0 else 0
        
        # 信息比率 (相对基准)
        information_ratio = 0
        if self.benchmark_values and len(self.benchmark_values) > 1:
            benchmark_returns = np.diff(self.benchmark_values) / self.benchmark_values[:-1]
            if len(benchmark_returns) == len(returns):
                active_returns = returns - benchmark_returns
                information_ratio = np.mean(active_returns) / np.std(active_returns) * np.sqrt(252)
        
        return {
            'sharpe_ratio': round(sharpe_ratio, 2),
            'sortino_ratio': round(sortino_ratio, 2),
            'calmar_ratio': round(calmar_ratio, 2),
            'information_ratio': round(information_ratio, 2),
        }
    
    def _analyze_trades(self) -> Dict:
        """分析交易行为"""
        if not self.trades:
            return {
                'total_trades': 0,
                'win_rate': 0,
                'avg_win': 0,
                'avg_loss': 0,
                'profit_factor': 0,
                'avg_trade_duration': 0
            }
        
        buy_trades = [t for t in self.trades if t['type'] == 'buy']
        sell_trades = [t for t in self.trades if t['type'] == 'sell']
        
        # 配对买卖交易
        trade_pairs = []
        for sell in sell_trades:
            for buy in buy_trades:
                if buy['timestamp'] < sell['timestamp']:
                    trade_pairs.append({
                        'buy_price': buy['price'],
                        'sell_price': sell['price'],
                        'buy_time': buy['timestamp'],
                        'sell_time': sell['timestamp'],
                        'quantity': min(buy['quantity'], sell['quantity'])
                    })
                    break
        
        if not trade_pairs:
            return {
                'total_trades': len(self.trades),
                'win_rate': 0,
                'avg_win': 0,
                'avg_loss': 0,
                'profit_factor': 0,
                'avg_trade_duration': 0
            }
        
        # 计算每笔交易盈亏
        trade_results = []
        durations = []
        for pair in trade_pairs:
            pnl = (pair['sell_price'] - pair['buy_price']) * pair['quantity']
            trade_results.append(pnl)
            duration = (pair['sell_time'] - pair['buy_time']).total_seconds() / (24 * 3600)  # 天数
            durations.append(duration)
        
        winning_trades = [r for r in trade_results if r > 0]
        losing_trades = [r for r in trade_results if r < 0]
        
        total_wins = sum(winning_trades) if winning_trades else 0
        total_losses = abs(sum(losing_trades)) if losing_trades else 0
        
        return {
            'total_trades': len(trade_pairs),
            'win_rate': round(len(winning_trades) / len(trade_pairs) * 100, 2) if trade_pairs else 0,
            'avg_win': round(np.mean(winning_trades), 2) if winning_trades else 0,
            'avg_loss': round(np.mean(losing_trades), 2) if losing_trades else 0,
            'profit_factor': round(total_wins / total_losses, 2) if total_losses > 0 else float('inf'),
            'avg_trade_duration': round(np.mean(durations), 2) if durations else 0,
        }
    
    def _analyze_drawdowns(self) -> Dict:
        """分析回撤"""
        if not self.portfolio_values:
            return {'max_drawdown': 0, 'avg_drawdown': 0, 'max_drawdown_duration': 0}
        
        values = np.array(self.portfolio_values)
        peak = values[0]
        drawdowns = []
        drawdown_periods = []
        current_drawdown_duration = 0
        max_drawdown_duration = 0
        
        for i, value in enumerate(values):
            if value > peak:
                peak = value
                drawdown_periods.append(current_drawdown_duration)
                current_drawdown_duration = 0
            else:
                drawdown = (peak - value) / peak * 100
                drawdowns.append(drawdown)
                current_drawdown_duration += 1
                max_drawdown_duration = max(max_drawdown_duration, current_drawdown_duration)
        
        return {
            'max_drawdown': round(max(drawdowns) if drawdowns else 0, 2),
            'avg_drawdown': round(np.mean(drawdowns) if drawdowns else 0, 2),
            'max_drawdown_duration': max_drawdown_duration,
            'drawdown_periods': len([d for d in drawdown_periods if d > 0])
        }
    
    def _benchmark_comparison(self) -> Dict:
        """基准比较分析"""
        if not self.benchmark_values or len(self.benchmark_values) < 2:
            return {}
        
        portfolio_returns = np.diff(self.portfolio_values) / self.portfolio_values[:-1]
        benchmark_returns = np.diff(self.benchmark_values) / self.benchmark_values[:-1]
        
        if len(portfolio_returns) != len(benchmark_returns):
            return {}
        
        # 超额收益
        excess_returns = portfolio_returns - benchmark_returns
        tracking_error = np.std(excess_returns) * np.sqrt(252) * 100
        
        # 上涨/下跌捕获率
        up_markets = benchmark_returns > 0
        down_markets = benchmark_returns < 0
        
        up_capture = np.mean(portfolio_returns[up_markets] / benchmark_returns[up_markets]) * 100 if np.any(up_markets) else 100
        down_capture = np.mean(portfolio_returns[down_markets] / benchmark_returns[down_markets]) * 100 if np.any(down_markets) else 100
        
        # Beta
        if len(benchmark_returns) > 1:
            covariance = np.cov(portfolio_returns, benchmark_returns)[0, 1]
            benchmark_variance = np.var(benchmark_returns)
            beta = covariance / benchmark_variance if benchmark_variance != 0 else 1
        else:
            beta = 1
        
        # Alpha
        risk_free_daily = self.risk_free_rate
        portfolio_return = np.mean(portfolio_returns)
        benchmark_return = np.mean(benchmark_returns)
        alpha = portfolio_return - (risk_free_daily + beta * (benchmark_return - risk_free_daily))
        
        return {
            'tracking_error': round(tracking_error, 2),
            'up_capture_ratio': round(up_capture, 2),
            'down_capture_ratio': round(down_capture, 2),
            'beta': round(beta, 3),
            'alpha': round(alpha * 252 * 100, 2),  # 年化Alpha
            'correlation': round(np.corrcoef(portfolio_returns, benchmark_returns)[0, 1], 3) if len(portfolio_returns) > 1 else 0,
        }
    
    def _calculate_periodic_returns(self, daily_returns: np.ndarray, period: int) -> List[float]:
        """计算周期收益"""
        if len(daily_returns) < period:
            return []
        
        periodic_returns = []
        for i in range(period, len(daily_returns) + 1, period):
            period_start = i - period
            period_end = i
            period_return = np.prod(1 + daily_returns[period_start:period_end]) - 1
            periodic_returns.append(period_return)
        
        return periodic_returns
    
    def _calculate_max_drawdown_percentage(self) -> float:
        """计算最大回撤百分比"""
        if not self.portfolio_values:
            return 0
        
        values = np.array(self.portfolio_values)
        peak = values[0]
        max_drawdown = 0
        
        for value in values:
            if value > peak:
                peak = value
            drawdown = (peak - value) / peak
            max_drawdown = max(max_drawdown, drawdown)
        
        return max_drawdown * 100
    
    def _calculate_skewness(self, returns: np.ndarray) -> float:
        """计算偏度"""
        if len(returns) < 3:
            return 0
        mean = np.mean(returns)
        std = np.std(returns)
        return np.mean(((returns - mean) / std) ** 3) if std != 0 else 0
    
    def _calculate_kurtosis(self, returns: np.ndarray) -> float:
        """计算峰度"""
        if len(returns) < 4:
            return 0
        mean = np.mean(returns)
        std = np.std(returns)
        return np.mean(((returns - mean) / std) ** 4) - 3 if std != 0 else 0