"""策略代码安全校验回归测试。

2026-09-06 R5:用例从 ``test_backtest_engine.py`` 搬出来。

**为什么还留着这个测试**:``StrategyValidator`` 服务的是策略 CRUD(存进库之前
先判这段代码能不能收),不是回测。回测撮合已经迁去 dc 了,
但「用户提交的代码」仍然要过这一关 —— 所以校验器和它的测试都留。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.strategy_validator import StrategyValidator

# 双均线交叉（前端模板 edit.vue 原样照搬）
MA_CROSS = """
# 双均线交叉策略
def on_data(data, context):
    import pandas as pd

    close = data['close']
    sma_20 = close.rolling(window=20).mean()
    sma_50 = close.rolling(window=50).mean()

    for i in range(50, len(close)):
        if sma_20.iloc[i-1] <= sma_50.iloc[i-1] and sma_20.iloc[i] > sma_50.iloc[i]:
            buy(close.iloc[i])
        elif sma_20.iloc[i-1] >= sma_50.iloc[i-1] and sma_20.iloc[i] < sma_50.iloc[i]:
            position = get_position()
            if position > 0:
                sell(close.iloc[i], position)
""".strip()


def test_validator_blocks_import_os():
    """真正危险的 `import os` 必须被拦住 —— 字符串匹配拦不住这个。"""
    r = StrategyValidator().validate_strategy("import os\ndef on_data(d,c):\n    os.system('rm -rf /')")
    assert r["valid"] is False
    assert any("os" in e for e in r["errors"]), r["errors"]


def test_validator_blocks_from_import():
    r = StrategyValidator().validate_strategy("from subprocess import run\ndef on_data(d,c):\n    pass")
    assert r["valid"] is False


def test_validator_blocks_importlib_bypass():
    """`import importlib; importlib.import_module('os')` 也要拦。"""
    code = "import importlib\ndef on_data(d,c):\n    m = importlib.import_module('os')"
    r = StrategyValidator().validate_strategy(code)
    assert r["valid"] is False


def test_validator_blocks_dynamic_exec():
    r = StrategyValidator().validate_strategy("def on_data(d,c):\n    eval('1+1')")
    assert r["valid"] is False


def test_validator_blocks_attribute_call():
    """`obj.eval(...)` 这种属性调用也要拦 —— 只查 Name 会漏。"""
    r = StrategyValidator().validate_strategy("def on_data(d,c):\n    x.eval('1+1')")
    assert r["valid"] is False


def test_validator_ignores_words_in_comments():
    """注释里提到 open / eval 不应误报 —— 字符串匹配会误伤这里。"""
    code = """# 该策略不使用 open 或 eval，只是注释里提到
# we never call open() here
def on_data(data, context):
    close = data['close']
    buy(close.iloc[0])
""".strip()
    r = StrategyValidator().validate_strategy(code)
    assert r["valid"] is True, r["errors"]


def test_validator_allows_pandas_in_strategy():
    """策略内部 import pandas 是允许的（前端模板就是这么写的）。"""
    r = StrategyValidator().validate_strategy(MA_CROSS)
    assert r["valid"] is True, r["errors"]


def test_validator_rejects_syntax_error():
    r = StrategyValidator().validate_strategy("def on_data(data, context)\n    pass")
    assert r["valid"] is False
    assert any("语法错误" in e for e in r["errors"])


def test_validator_flags_no_entrypoint():
    """只有函数定义、没有 on_data、也没有模块级代码 → 报错。"""
    r = StrategyValidator().validate_strategy("def helper():\n    return 1")
    assert r["valid"] is False
    assert any("入口" in e for e in r["errors"]), r["errors"]


def test_validator_warns_without_trade_calls():
    r = StrategyValidator().validate_strategy("def on_data(data, context):\n    x = data['close'].mean()")
    assert r["valid"] is True
    assert any("buy" in w for w in r["warnings"])


def test_validator_deduplicates_messages():
    """同一条消息重复出现应去重 —— 前端是按条展示的,重复十条没意义。"""
    code = "def on_data(d,c):\n    eval('1')\n    eval('2')\n    eval('3')"
    r = StrategyValidator().validate_strategy(code)
    assert len(r["errors"]) == 1, r["errors"]


def test_validator_ignores_local_variables():
    """局部变量不该被当成「未识别的全局名称」。

    2026-09-07 修:原实现对所有 ast.Name 一视同仁,于是
    `close = data['close']` 里的 close、`for i in ...` 里的 i 全被警告。
    一条普通策略能报出 5 条噪音,真正可疑的全局引用反而被淹没。
    """
    r = StrategyValidator().validate_strategy(MA_CROSS)
    assert r["valid"] is True, r["errors"]
    assert r["warnings"] == [], r["warnings"]


def test_validator_still_warns_unknown_globals():
    """消噪不等于放行 —— 真·未绑定的全局引用仍要报出来。"""
    code = "def on_data(d,c):\n    x = some_unknown_thing\n    buy(1)"
    r = StrategyValidator().validate_strategy(code)
    assert any("some_unknown_thing" in w for w in r["warnings"]), r["warnings"]


def test_validator_still_blocks_local_alias_of_dangerous_call():
    """把 eval 赋给局部变量再调用,不能因为「是局部变量」就放过。"""
    code = "def on_data(d,c):\n    f = eval\n    f('1+1')"
    r = StrategyValidator().validate_strategy(code)
    assert r["valid"] is False, r["errors"]
