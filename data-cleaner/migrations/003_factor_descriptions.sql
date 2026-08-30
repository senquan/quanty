-- 003: 因子定义增加说明字段，并播种内置因子的说明文案

ALTER TABLE factor.definitions ADD COLUMN IF NOT EXISTS description TEXT;

-- 播种：已存在则只更新 description，不覆盖人工修改的名称/类别等
INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'GRO_EPS_GROWTH_YOY',
    '净利润同比增长率',
    'growth',
    'Daily',
    '',
    '["eps_growth_yoy"]'::jsonb,
    'system',
    '净利润同比增长率：本期净利润相对上年同期的增幅，反映盈利成长性，使用时宜剔除非经常性损益。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'GRO_PRICE_MOMENTUM',
    '价格成长动量(60日/250日)',
    'growth',
    'Daily',
    '',
    '["adj_close"]'::jsonb,
    'system',
    '价格成长势能：60日收益相对250日收益的比值，刻画涨势加速度；需至少250个交易日历史才有值。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'GRO_REV_GROWTH_YOY',
    '营收同比增长率',
    'growth',
    'Daily',
    '',
    '["rev_growth_yoy"]'::jsonb,
    'system',
    '营收同比增长率：本期营收相对上年同期的增幅，衡量业务扩张速度，是成长股定价的核心变量。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'INTRADAY_MOM_10',
    '近10根K线动量',
    'momentum',
    'Intraday',
    '',
    '["adj_close"]'::jsonb,
    'system',
    '近10根K线动量：按K线根数（非自然日）计窗口的短周期动量，在分钟/小时线上代表日内动能。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'INTRADAY_RANGE_20',
    '近20根K线振幅均值',
    'volatility',
    'Intraday',
    '',
    '["adj_high", "adj_low", "adj_close"]'::jsonb,
    'system',
    '近20根K线振幅均值：单根K线（高-低）/收盘价 的均值，衡量日内价格波动幅度，与流动性密切相关。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'INTRADAY_VOL_20',
    '近20根K线收益率波动率',
    'volatility',
    'Intraday',
    '',
    '["adj_close"]'::jsonb,
    'system',
    '近20根K线波动率：以K线根数计窗口的收益标准差，在高频数据上可细粒度刻画日内波动。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'MOM_ACCEL',
    '动量加速度',
    'momentum',
    'Daily',
    '',
    '["adj_close"]'::jsonb,
    'system',
    '动量加速度：20日收益减2倍10日收益，近似涨势的二阶导；由正转负常预示上行动能衰减。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'MOM_RET_20',
    '20日动量',
    'momentum',
    'Daily',
    '',
    '["adj_close"]'::jsonb,
    'system',
    '20日动量：近一个月收益率，A股最常用窗口。A股短周期反转效应显著，该因子常呈负向预测。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'MOM_RET_5',
    '5日动量',
    'momentum',
    'Daily',
    '',
    '["adj_close"]'::jsonb,
    'system',
    '5日动量：近5个交易日收益率，捕捉超短期趋势。噪音大、换手高，常与更长周期动量搭配使用。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'MOM_RET_60',
    '60日动量',
    'momentum',
    'Daily',
    '',
    '["adj_close"]'::jsonb,
    'system',
    '60日动量：近一季度收益率，刻画中期趋势。Jegadeesh-Titman 动量区间在A股多表现为反转。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'REL_STR_20',
    '20日相对强度',
    'momentum',
    'Daily',
    '',
    '["adj_close"]'::jsonb,
    'system',
    '20日相对强度：20日收益在当日全市场做横截面z-score标准化，剔除大盘涨跌后的相对强弱。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'SENT_AMOUNT_RANK',
    '成交额市场分位',
    'sentiment',
    'Daily',
    '',
    '["volume", "adj_close"]'::jsonb,
    'system',
    '成交额市场分位：当日成交额（量×价）在全市场的百分比排名，越接近1越受资金追捧，常用于识别龙头。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'SENT_TURNOVER_20',
    '20日换手率',
    'sentiment',
    'Daily',
    '',
    '["volume"]'::jsonb,
    'system',
    '20日换手代理：当日成交量相对20日均量的倍数（以成交量近似换手率），放大说明交投活跃、筹码交换加快。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'SENT_VOL_RATIO_5',
    '5日量比',
    'sentiment',
    'Daily',
    '',
    '["volume"]'::jsonb,
    'system',
    '5日量比：5日均量与20日均量之比。大于1表示近期放量、资金关注度提升，是情绪升温的直接信号。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'TECH_BB_POS',
    '布林带位置',
    'technical',
    'Daily',
    '',
    '["adj_close"]'::jsonb,
    'system',
    '布林带位置：价格在20日均线±2倍标准差通道中的相对位置，0为下轨、1为上轨，越接近1越超买。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'TECH_MACD_DEA',
    'MACD DEA',
    'technical',
    'Daily',
    '',
    '["adj_close"]'::jsonb,
    'system',
    'MACD DEA：DIF的9日指数移动平均（信号线）。DIF上穿DEA为金叉、下穿为死叉，是最常用的择时信号。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'TECH_MACD_DIF',
    'MACD DIF',
    'technical',
    'Daily',
    '',
    '["adj_close"]'::jsonb,
    'system',
    'MACD DIF：12日EMA减26日EMA的离差值，反映短长均线偏离程度；上穿零轴常视为趋势转强信号。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'TECH_MACD_HIST',
    'MACD 柱',
    'technical',
    'Daily',
    '',
    '["adj_close"]'::jsonb,
    'system',
    'MACD柱：2×(DIF−DEA)，直观反映快慢线间距变化。柱体由负转正或持续放大常作为趋势确认依据。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'TECH_MACD_CROSS',
    'MACD 金叉',
    'technical',
    'Daily',
    '',
    '["adj_close"]'::jsonb,
    'system',
    'MACD金叉：DIF自下而上穿越DEA的当日记为1、其余为0的布尔信号，是经典的短线买入/择时触发点。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'TECH_MA_BIAS_20',
    '20日均线乖离率',
    'technical',
    'Daily',
    '',
    '["adj_close"]'::jsonb,
    'system',
    '20日均线乖离率：（价格−20日均线）/20日均线，衡量偏离均线的幅度，绝对值越大均值回归压力越强。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'TECH_RSI_14',
    '14日RSI',
    'technical',
    'Daily',
    '',
    '["adj_close"]'::jsonb,
    'system',
    '14日RSI：100−100/(1+RS)，RS为14日平均涨幅与平均跌幅之比（简单移动平均变体）。>70超买、<30超卖。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'VAL_DIV_YIELD',
    '股息率',
    'value',
    'Daily',
    '',
    '["div_yield"]'::jsonb,
    'system',
    '股息率：近12个月每股分红与股价之比，衡量现金回报水平，是红利低波策略的核心选股指标。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'VAL_PB',
    '市净率',
    'value',
    'Daily',
    '',
    '["pb"]'::jsonb,
    'system',
    '市净率：股价与每股净资产之比，反映对账面价值的溢价。适用于重资产行业，轻资产公司易失真。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'VAL_PE_PERCENTILE',
    'PE历史分位(3年)',
    'value',
    'Daily',
    '',
    '["pe_ttm"]'::jsonb,
    'system',
    'PE历史分位：当前PE在近3年（756个交易日）中的分位数，越低越接近历史底部；无PE时用价格分位代理。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'VAL_PE_TTM',
    '市盈率(TTM)',
    'value',
    'Daily',
    '',
    '["pe_ttm"]'::jsonb,
    'system',
    '市盈率TTM：股价与最近四季每股收益之比，衡量回本年限。越低越"便宜"，亏损股为负需剔除。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'VAL_PS_TTM',
    '市销率(TTM)',
    'value',
    'Daily',
    '',
    '["ps_ttm"]'::jsonb,
    'system',
    '市销率TTM：市值与近12个月营收之比，不受净利润为负干扰，常用于高成长未盈利公司估值。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'VOL_ATR_14',
    '14日ATR',
    'volatility',
    'Daily',
    '',
    '["adj_high", "adj_low", "adj_close"]'::jsonb,
    'system',
    '14日ATR：真实波幅取当日振幅、最高-前收、最低-前收三者最大值的14日均值，可刻画跳空缺口风险。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'VOL_PARKINSON_20',
    '20日Parkinson波动率',
    'volatility',
    'Daily',
    '',
    '["adj_high", "adj_low"]'::jsonb,
    'system',
    '20日Parkinson波动率：仅用最高/最低价的极差估计量（方差=ln(H/L)²均值/(4ln2)），效率约为收盘价法的5倍。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'VOL_SKEW_60',
    '60日收益偏度',
    'volatility',
    'Daily',
    '',
    '["adj_close"]'::jsonb,
    'system',
    '60日收益偏度：收益分布三阶矩。正偏说明大涨尾部更厚，负偏提示极端下跌风险，属尾部风险度量。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

INSERT INTO factor.definitions (code, name, category, frequency, formula, data_sources, author, description)
VALUES (
    'VOL_STD_20',
    '20日收益率标准差',
    'volatility',
    'Daily',
    '',
    '["adj_close"]'::jsonb,
    'system',
    '20日收益波动率：日收益率序列的滚动标准差，最经典的波动度量，高波动对应更高的风险溢价。'
)
ON CONFLICT (code) DO UPDATE SET description = EXCLUDED.description;

