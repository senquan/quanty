"""自定义因子 formula 沙箱表达式引擎

支持受限表达式语法，仅允许白名单函数与列名，禁止任意代码执行。
表达式示例: "close / delay(close, 5) - 1" , "rank(close)", "ma(close, 20)"

白名单函数:
  delay(x, n)   = x.shift(n)
  ref(x, n)     = x.shift(n)        (同 delay)
  ma(x, n)      = x.rolling(n).mean()
  std(x, n)     = x.rolling(n).std()
  max(x, n)     = x.rolling(n).max()
  min(x, n)     = x.rolling(n).min()
  rank(x)       = x.rank(pct=True)
  ts_mean(x,n)  = x.rolling(n).mean()
  ts_std(x,n)   = x.rolling(n).std()
  ts_max(x,n)   = x.rolling(n).max()
  ts_min(x,n)   = x.rolling(n).min()
  abs(x)        = x.abs()
  log(x)        = x.log()
  sign(x)       = x.sign()
  zscore(x)     = (x - x.mean())/x.std()
可用列名 (按 symbol 分组后计算): open/high/low/close/volume/adj_open/adj_high/adj_low/adj_close
"""
import ast
import operator

import pandas as pd

# 白名单二元/一元运算符
_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.Pow: operator.pow, ast.Mod: operator.mod,
}
_UNARYOPS = {ast.USub: operator.neg, ast.UAdd: operator.pos}

_WHITELIST_FUNCS = {
    "delay", "ref", "ma", "std", "max", "min", "rank",
    "ts_mean", "ts_std", "ts_max", "ts_min", "abs", "log", "sign", "zscore",
}
_ALLOWED_COLS = {
    "open", "high", "low", "close", "volume",
    "adj_open", "adj_high", "adj_low", "adj_close",
}


class FormulaError(Exception):
    """表达式语法/安全违规"""


def _compile_func(name: str, args: list):
    if name not in _WHITELIST_FUNCS:
        raise FormulaError(f"不允许的函数: {name}")
    if name == "delay" or name == "ref":
        x, n = args[0], int(args[1])
        return x.shift(n)
    if name == "ma" or name == "ts_mean":
        x, n = args[0], int(args[1])
        return x.rolling(n, min_periods=max(2, n // 2)).mean()
    if name == "std" or name == "ts_std":
        x, n = args[0], int(args[1])
        return x.rolling(n, min_periods=max(2, n // 2)).std()
    if name == "max" or name == "ts_max":
        x, n = args[0], int(args[1])
        return x.rolling(n, min_periods=max(2, n // 2)).max()
    if name == "min" or name == "ts_min":
        x, n = args[0], int(args[1])
        return x.rolling(n, min_periods=max(2, n // 2)).min()
    if name == "rank":
        return args[0].rank(pct=True)
    if name == "abs":
        return args[0].abs()
    if name == "log":
        return args[0].apply(lambda v: pd.Series(v).apply(lambda x: __import__("math").log(x) if x > 0 else 0).values)
    if name == "sign":
        return args[0].apply(lambda s: s.apply(lambda v: 1 if v > 0 else (-1 if v < 0 else 0)))
    if name == "zscore":
        x = args[0]
        return (x - x.mean()) / (x.std() + 1e-9)
    raise FormulaError(f"未实现函数: {name}")


def _eval_node(node: ast.AST, cols: dict) -> pd.Series:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, cols)
    if isinstance(node, ast.BinOp):
        op = _BINOPS.get(type(node.op))
        if not op:
            raise FormulaError("不支持的运算符")
        return op(_eval_node(node.left, cols), _eval_node(node.right, cols))
    if isinstance(node, ast.UnaryOp):
        op = _UNARYOPS.get(type(node.op))
        if not op:
            raise FormulaError("不支持的一元运算符")
        return op(_eval_node(node.operand, cols))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise FormulaError("函数调用必须使用白名单名称")
        name = node.func.id
        args = [_eval_node(a, cols) for a in node.args]
        return _compile_func(name, args)
    if isinstance(node, ast.Name):
        if node.id not in _ALLOWED_COLS:
            raise FormulaError(f"不允许的列名: {node.id}")
        return cols[node.id]
    if isinstance(node, ast.Constant):
        return node.value
    raise FormulaError(f"不支持的语法节点: {type(node).__name__}")


def compile_formula(expr: str):
    """解析并返回 lambda(df) -> Series（按 symbol 分组计算）

    解析阶段即做静态安全检查：禁止白名单之外的函数与列名。
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise FormulaError(f"公式语法错误: {e}") from e

    # 静态安全校验：遍历所有节点，确认函数与列名均在白名单
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise FormulaError("函数调用必须使用白名单名称")
            if node.func.id not in _WHITELIST_FUNCS:
                raise FormulaError(f"不允许的函数: {node.func.id}")
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            raise FormulaError("不允许的名称上下文")
        # 禁止属性访问（如 obj.attr）
        if isinstance(node, ast.Attribute):
            raise FormulaError("不允许的属性访问")

    def compute(df: pd.DataFrame) -> pd.Series:
        out = []
        for _, g in df.groupby("symbol", sort=False):
            cols = {c: g[c] for c in _ALLOWED_COLS if c in g.columns}
            series = _eval_node(tree, cols)
            out.append(pd.Series(series.values, index=g.index))
        return pd.concat(out).reindex(df.index)

    return compute
