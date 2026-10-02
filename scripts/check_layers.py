"""校验模块依赖方向符合 `docs/design.md` 的「模块职责矩阵」。

分层与模块清单的**单一来源**是 `docs/design.md` 的那张矩阵表 —— 本脚本解析它，所以
「新增了模块但忘了登记」也会被抓住（这正是那张表存在的意义：它是架构的机器可读表达）。

规则（[refactor.md §3](../../ai/refactor/refactor.md#3-判定准则) 第 2 条）：

    L0 入口 → L1 契约 → L2 编排 → L3 领域 → L4 基础设施 → L5 纯工具

箭头只向下 —— 层 n 的模块只能 import 层 m ≥ n 的模块。
**唯一例外是 L1 契约**：`config` / `schemas` / `models` 是各层共用的接口定义，
按矩阵自己的说法是「声明…」，被任何层 import 都是正常的。

用法：

    python scripts/check_layers.py          # 校验
    python scripts/check_layers.py --list   # 顺便列出每个模块的层
"""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "backend" / "app"
DESIGN_DOC = REPO_ROOT / "docs" / "design.md"

LAYER_NAMES = {0: "L0 入口", 1: "L1 契约", 2: "L2 编排", 3: "L3 领域", 4: "L4 基础设施", 5: "L5 纯工具"}
CONTRACT_LAYER = 1


def parse_layers(design_text: str) -> dict[str, int]:
    """从模块职责矩阵取「模块名 → 层号」。

    矩阵行形如：`| [main.py](../backend/app/main.py#L39) | L0 入口 | 职责 | 不负责 |`
    一格里可能有多个链接（`[paths.py](…) / [log_safety.py](…)`），所以按链接逐个取。
    """
    layers: dict[str, int] = {}
    for line in design_text.splitlines():
        if not line.startswith("| ["):
            continue
        cells = line.split("|")
        if len(cells) < 5:
            continue
        match = re.search(r"L(\d)", cells[2])
        if match is None:
            continue
        layer = int(match.group(1))
        for name in re.findall(r"\[([^\]/]+\.py|routers/)\]", cells[1]):
            key = name[:-3] if name.endswith(".py") else name.rstrip("/")
            layers[key] = layer
    return layers


def actual_modules() -> dict[str, Path]:
    """`backend/app` 下所有模块，键是相对 `backend/app` 的无后缀路径（如 `routers/jobs`）。"""
    modules: dict[str, Path] = {}
    for path in sorted(APP_DIR.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        modules[path.relative_to(APP_DIR).with_suffix("").as_posix()] = path
    return modules


def layer_of(key: str, layers: dict[str, int]) -> int | None:
    """查一个模块的层；`routers/jobs` 这类没有独立登记时回退到它的包（`routers`）。"""
    if key in layers:
        return layers[key]
    return layers.get(key.split("/", 1)[0])


def imports_of(path: Path) -> set[str]:
    """模块里的包内相对 import（`from .x import …` / `from . import x`），其余不算。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    targets: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level == 0:
            continue
        if node.module:
            targets.add(node.module.replace(".", "/"))
        else:
            for alias in node.names:
                targets.add(alias.name)
    return targets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="列出每个模块的层")
    args = parser.parse_args(argv)

    layers = parse_layers(DESIGN_DOC.read_text(encoding="utf-8"))
    if not layers:
        # 矩阵表格格式一变（列序、标题写法），解析就会得到空表；此时「全部未登记」的错误信息
        # 会误导人以为是代码的问题。宁可明确报「解析失败」。
        print(f"无法从 {DESIGN_DOC} 解析出任何模块：矩阵表格格式可能变了。")
        return 1

    modules = actual_modules()
    problems: list[str] = []

    for key, path in sorted(modules.items()):
        source_layer = layer_of(key, layers)
        if source_layer is None:
            problems.append(f"{key}.py 没有登记在 docs/design.md 的模块职责矩阵里")
            continue
        for target in sorted(imports_of(path)):
            target_layer = layer_of(target, layers)
            if target_layer is None:
                problems.append(f"{key}.py 里的 `from .{target.replace('/', '.')}` 指向未登记的模块")
                continue
            if target_layer < source_layer and target_layer != CONTRACT_LAYER:
                problems.append(
                    f"{key}.py（{LAYER_NAMES[source_layer]}）import 了 {target}.py"
                    f"（{LAYER_NAMES[target_layer]}）—— 依赖方向朝上"
                )

    if args.list:
        for key in sorted(modules, key=lambda k: (layer_of(k, layers) if layer_of(k, layers) is not None else 99, k)):
            layer = layer_of(key, layers)
            print(f"    {LAYER_NAMES[layer] if layer is not None else '?? ?'}  {key}.py")
        print("")

    print(f"解析 docs/design.md 得到 {len(layers)} 个已登记模块；backend/app 下 {len(modules)} 个模块。")

    if problems:
        print("")
        for item in problems:
            print(f"  ✗ {item}")
        print("")
        print(f"分层校验失败：{len(problems)} 处")
        return 1

    print("分层校验通过：依赖方向一致，且没有未登记的模块。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
