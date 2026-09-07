"""策略代码安全校验（AST 层面）。

2026-09-06 R5:从 ``backtest_engine.py`` 拆出。

**为什么留它**:策略 CRUD 的三个入口都用它(``endpoints/quant.py`` 的
创建 / 更新 / 校验),跟回测撮合没有半点关系 —— 回测已经迁去 dc,
但「用户提交的代码能不能存进库」这件事仍然归 backend 管。

用 AST 而不是字符串匹配:字符串匹配会把注释里的 "open" 也算成危险,
却挡不住真正危险的 ``import os``(文本里没有 __import__ 这个词)。
"""
import ast
from typing import Dict, List

#: 允许策略调用的内置函数白名单。策略只需要算术与序列操作。
ALLOWED_BUILTINS = {
    'abs', 'all', 'any', 'bool', 'dict', 'divmod', 'enumerate', 'float',
    'int', 'len', 'list', 'max', 'min', 'pow', 'range', 'round', 'set',
    'sorted', 'str', 'sum', 'tuple', 'zip', 'True', 'False', 'None',
}

#: 一律禁止的内置名字。命中的是「调用」而非文本,所以注释里出现这些词没事。
BLOCKED_BUILTINS = {
    'eval', 'exec', 'compile', 'open', 'input', '__import__',
    'globals', 'locals', 'getattr', 'setattr', 'delattr', 'vars', 'dir',
}

BLOCKED_MODULES = {
    'os', 'sys', 'subprocess', 'socket', 'shutil', 'pathlib', 'requests',
    'urllib', 'http', 'pickle', 'importlib', 'builtins', 'ctypes',
}

#: 引擎注入进策略命名空间的全局变量 —— 出现在策略里不算「未识别」
INJECTED_NAMES = {
    'np', 'pd', 'data', 'context', 'buy', 'sell', 'get_position',
    'get_capital', 'on_data', 'math', 'datetime', 'timedelta',
}


def _collect_local_names(tree: ast.AST) -> set:
    """收集策略代码里所有「本地绑定过」的名字(赋值目标 / 参数 / 导入别名 / 函数名…)。

    这些不是全局引用,不该出现在「未识别的全局名称」警告里。
    """
    names = set()
    for node in ast.walk(tree):
        # 赋值目标、for 目标、with ... as、推导式变量
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split('.')[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
    return names


class StrategyValidator:
    """策略代码验证器（AST 层面）。"""

    @staticmethod
    def validate_strategy(code: str) -> Dict:
        """验证策略代码。"""
        errors: List[str] = []
        warnings: List[str] = []

        # 基础语法检查
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            errors.append(f"语法错误: {str(e)}")
            return {'valid': False, 'errors': errors, 'warnings': warnings}

        # 先收集**局部作用域**里绑定过的名字。
        # 不做这一步的话,`close = data['close']` 里的 close 也会被当成
        # 「未识别的全局名称」—— 局部变量天天被误报,真正可疑的全局引用反而淹没在噪音里。
        local_names = _collect_local_names(tree)

        # 危险调用 / 导入
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split('.')[0]
                    if root in BLOCKED_MODULES:
                        errors.append(f"禁止导入模块: {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root = node.module.split('.')[0]
                    if root in BLOCKED_MODULES:
                        errors.append(f"禁止导入模块: {node.module}")
            elif isinstance(node, ast.Name):
                if node.id in BLOCKED_BUILTINS:
                    errors.append(f"禁止使用: {node.id}()")
                elif node.id not in ALLOWED_BUILTINS:
                    # 未列入白名单的内置名：只警告，避免误伤 np/pd 等注入变量
                    if node.id not in INJECTED_NAMES and node.id not in local_names:
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
            and n.func.id in {'buy', 'sell'}
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
            isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == 'on_data'
            for n in tree.body
        )
        if not top_level_stmts and not has_on_data:
            errors.append(
                "策略没有任何可执行入口：请定义 on_data(data, context)，"
                "或写成模块级代码（目前只有函数/类定义）"
            )

        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings,
        }
