# 示例策略：双均线交叉策略
import numpy as np

# 获取技术指标数据
sma_20 = data['sma_20']
sma_50 = data['sma_50']
close = data['close']

# 策略参数
short_window = 20
long_window = 50

# 遍历数据进行交易决策
for i in range(long_window, len(data)):
    current_price = close.iloc[i]
    current_position = get_position()
    
    # 获取前一天的均线值
    prev_sma_20 = sma_20.iloc[i-1]
    prev_sma_50 = sma_50.iloc[i-1]
    curr_sma_20 = sma_20.iloc[i]
    curr_sma_50 = sma_50.iloc[i]
    
    # 金叉买入信号：短期均线上穿长期均线
    if (prev_sma_20 <= prev_sma_50 and curr_sma_20 > curr_sma_50):
        if current_position == 0:  # 无仓位时才买入
            # 使用90%的资金买入
            available_capital = get_capital()
            buy_quantity = int((available_capital * 0.9) / current_price)
            if buy_quantity > 0:
                buy(current_price, buy_quantity)
    
    # 死叉卖出信号：短期均线下穿长期均线
    elif (prev_sma_20 >= prev_sma_50 and curr_sma_20 < curr_sma_50):
        if current_position > 0:  # 有仓位时才卖出
            # 全部卖出
            sell(current_price, current_position)
    
    # 止损：当价格跌破最近20日最低点时止损
    elif current_position > 0:
        recent_low = close.iloc[i-20:i].min()
        if current_price < recent_low * 0.95:  # 跌破近期低点5%
            sell(current_price, current_position)

print("双均线策略执行完成")