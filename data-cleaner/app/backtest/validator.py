"""策略代码安全校验（AST 层面）。

迁移自 ``backend/app/services/backtest_engine.py`` 的 ``StrategyValidator``。

用 AST 而不是字符串匹配：字符串匹配会把注释里的 "open" 也算成危险，
却挡不住真正危险的 ``import os``（文本里没有 __import__ 这个词）。

⚠️ 这一层是**防误伤，不是防攻击**。策略代码在 dc 进程里 ``exec``，
与因子计算同一进程。要做真正的隔离得另起沙箱（子进程 / 容器），
那是另一个量级的工程；在此之前，dc 只接受内部用户提交的策略。
"""

from __future__ import annotations

import ast
from typing import Dict, List

#: 允许策略调用的内置函数白名单。策略只需要算术与序列操作。
ALLOWED_BUILTINS = {
    "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "float",
    "int", "len", "list", "max", "min", "pow", "range", "round", "set",
    "sorted", "str", "sum", "tuple", "zip", "True", "False", "None",
}

#: 一律禁止的内置名字。命中的是「调用」而非文本，所以注释里出现这些词没事。
BLOCKED_BUILTINS = {
    "eval", "exec", "compile", "open", "input", "__import__",
    "globals", "locals", "getattr", "setattr", "delattr", "vars", "dir",
}

BLOCKED_MODULES = {
    "os", "sys", "subprocess", "socket", "shutil", "pathlib", "requests",
    "urllib", "http", "pickle", "importlib", "builtins", "ctypes",
}

#: 注入进执行环境的名字，白名单之外不再提示
INJECTED_NAMES = {
    "np", "pd", "data", "context", "buy", "sell",
    "get_position", "get_capital", "on_data",
    "math", "datetime", "timedelta",
}


class StrategyValidator:
    """策略代码验证器（AST 层面）。"""

    @staticmethod
    def validate_strategy(code: str) -> Dict:
        """验证策略代码，返回 {valid, errors, warnings}。"""
        errors: List[str] = []
        warnings: List[str] = []

        if not code or not code.strip():
            return {
                "valid": False,
                "errors": ["策略代码为空"],
                "warnings": warnings,
            }

        # 基础语法检查
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            errors.append(f"语法错误: {e}")
            return {"valid": False, "errors": errors, "warnings": warnings}

        # 危险调用 / 导入
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in BLOCKED_MODULES:
                        errors.append(f"禁止导入模块: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root = node.module.split(".")[0]
                    if root in BLOCKED_MODULES:
                        errors.append(f"禁止导入模块: {node.module}")
            elif isinstance(node, ast.Name):
                if node.id in BLOCKED_BUILTINS:
                    errors.append(f"禁止使用: {node.id}()")
                elif node.id not in ALLOWED_BUILTINS and node.id not in INJECTED_NAMES:
                    # 未列入白名单的内置名：只警告，避免误伤 np/pd 等注入变量
                    warnings.append(f"未识别的全局名称: {node.id}")
            elif isinstance(node, ast.Attribute):
                if node.attr in BLOCKED_BUILTINS:
                    errors.append(f"禁止使用: .{node.attr}")

        # 去重，保持顺序
        errors = list(dict.fromkeys(errors))
        warnings = list(dict.fromkeys(warnings))

        # 必需函数检查：必须有交易动作
        has_trade = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id in {"buy", "sell"}
            for n in ast.walk(tree)
        )
        if not has_trade:
            warnings.append("策略中没有找到 buy() / sell() 调用")

        # 入口检查：要么是模块级可执行代码，要么定义了 on_data
        top_level_stmts = [
            n for n in tree.body
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        has_on_data = any(
            isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "on_data"
            for n in tree.body
        )
        if not top_level_stmts and not has_on_data:
            errors.append(
                "策略没有任何可执行入口：请定义 on_data(data, context)，"
                "或写成模块级代码（目前只有函数/类定义）"
            )

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
        }
