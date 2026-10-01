"""守护测试：`finally` 块里**不允许**出现 `return`。

背景见 [008](../../ai/bug-fix/008-return-in-finally-swallows-the-real-error.md)。
`job_manager` 的条目收尾逻辑写在 `finally` 里，并用 `return` 提前退出。后果有两层：

1. **语义层（真正的伤害）**：`finally` 里的 `return` 会**吞掉正在传播的异常**。
   收尾那一小段代码一旦自己抛异常（`session.commit()` 撞上写锁、`_publish_threadsafe`
   出错），异常会无声消失：条目永远停在 `running`，日志里连一行都没有 ——
   比直接失败难查得多。
2. **可见层**：CPython 只给一句 `SyntaxWarning: 'return' in a 'finally' block`，
   而这条警告**只在源码被重新编译时**（`__pycache__` 缺失或失效）才打印。
   所以它时有时无，靠人盯终端并不可靠。

这两条测试把规则钉死：`app/*.py` 里再出现同类写法会立刻失败。
"""

from __future__ import annotations

import ast
import warnings
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"

_TRY_NODES = tuple(
    node for node in (getattr(ast, "Try", None), getattr(ast, "TryStar", None)) if node is not None
)
# 这些节点会开启新的作用域：里面的 `return` 属于另一个函数，不构成「finally 里 return」。
_NESTED_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _app_modules() -> list[Path]:
    return sorted(APP_DIR.glob("*.py"))


def _returns_in(node: ast.AST) -> list[ast.Return]:
    """收集节点之下的 `return`，但不进入嵌套函数/类。"""
    found: list[ast.Return] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _NESTED_SCOPE_NODES):
            continue
        if isinstance(child, ast.Return):
            found.append(child)
            continue
        found.extend(_returns_in(child))
    return found


def _returns_in_finally(try_node: ast.AST) -> list[ast.Return]:
    return _returns_in(ast.Module(body=try_node.finalbody, type_ignores=[]))


def test_app_package_is_actually_scanned() -> None:
    """守卫：如果扫描路径写错，下面两条测试会「0 个模块也通过」。"""
    names = {path.name for path in _app_modules()}
    assert {"job_manager.py", "main.py"} <= names


def test_app_sources_have_no_syntax_warnings() -> None:
    offenders: list[str] = []
    for path in _app_modules():
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        offenders.extend(
            f"{path.name}:{warning.lineno} {warning.message}"
            for warning in caught
            if issubclass(warning.category, SyntaxWarning)
        )
    assert offenders == [], "源码被重新编译时会打印 SyntaxWarning：" + " | ".join(offenders)


def test_no_return_inside_finally() -> None:
    offenders: list[str] = []
    for path in _app_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if isinstance(node, _TRY_NODES):
                offenders.extend(f"{path.name}:{statement.lineno}" for statement in _returns_in_finally(node))
    assert offenders == [], "finally 里的 return 会吞掉异常，请改成分支收尾：" + ", ".join(offenders)


def test_guard_detects_a_violation() -> None:
    """自检：规则本身要真能抓到违规写法，否则上面的测试只是「恰好通过」。"""
    tree = ast.parse("def f():\n    try:\n        pass\n    finally:\n        return\n")
    try_node = next(node for node in ast.walk(tree) if isinstance(node, _TRY_NODES))
    assert [statement.lineno for statement in _returns_in_finally(try_node)] == [5]


def test_guard_ignores_returns_in_nested_scopes() -> None:
    """自检：`finally` 里定义内层函数是合法的，规则不能因此误报。"""
    tree = ast.parse(
        "def f():\n"
        "    try:\n"
        "        pass\n"
        "    finally:\n"
        "        def inner():\n"
        "            return 1\n"
        "        inner()\n"
    )
    try_node = next(node for node in ast.walk(tree) if isinstance(node, _TRY_NODES))
    assert _returns_in_finally(try_node) == []
