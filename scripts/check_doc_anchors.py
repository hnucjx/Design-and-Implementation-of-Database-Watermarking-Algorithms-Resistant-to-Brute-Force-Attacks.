#!/usr/bin/env python
"""核对文档里的代码锚点（``[符号](../path#Lnn)``）有没有随代码漂移。

为什么需要它：文档用行号锚点直接指到源码，代码一改锚点就会静默指错地方 ——
而「点进去看到的是别的函数」比没有链接更误导人。这个脚本把这件事变成一条可执行的检查：

    python scripts/check_doc_anchors.py          # 只报告；有漂移时退出码 1
    python scripts/check_doc_anchors.py --fix    # 能唯一确定符号的锚点直接重算

**它只改「标签就是符号名」的锚点**（含反引号、``Class.method`` 的点号形式，以及路由路径字符串）。
标签是文件名或散文的（例如 ``[main.py](../backend/app/main.py#L500)``）会被列进
「待人工复核」——那种锚点指向的是文件里的某个区域，语义只有作者知道，不能猜。

用法上建议：改动 ``backend/app`` / ``frontend/src`` 之后跑一次，把结果并进同一个 commit。
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


REPO_ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"\[([^\]]+)\]\((\.\./[^#)\s]+)#L(\d+)\)")
# 后端 `def name(` / `async def name(` / `class Name(`；前端 `export function name` / `const name`。
DEF_PATTERNS = (
    r"\s*(?:async\s+)?def\s+{name}\b",
    r"\s*class\s+{name}\b",
    r"\s*(?:export\s+)?(?:async\s+)?function\s+{name}\b",
    r"\s*(?:export\s+)?(?:const|let)\s+{name}\b",
)


def resolve(label: str, lines: list[str]) -> list[int]:
    """把链接标签解析成目标文件里的候选行号。解析不出来返回空列表。"""
    name = label.strip("`\"'")
    # `BrowserCookieImportError.to_detail` 这种点号形式，取最后一段。
    if "." in name and "/" not in name:
        name = name.split(".")[-1]

    hits: list[int] = []
    if re.fullmatch(r"[A-Za-z_][\w]*", name):
        patterns = [pattern.format(name=re.escape(name)) for pattern in DEF_PATTERNS]
        # 全大写标识符通常是模块级常量。
        patterns.append(rf"\s*{re.escape(name)}\s*(?::[^=]+)?=")
        patterns.append(rf"\s*{re.escape(name)}\s*=\s*")
        hits = [i for i, row in enumerate(lines, start=1) if any(re.match(p, row) for p in patterns)]
    if hits:
        return hits

    # 路由：`POST /api/analyze` / `/api/cookies` 这类标签按路径字符串找装饰器。
    route = re.search(r"(/api[\w/{}\-]*|/health)", name)
    if route:
        return [
            i
            for i, row in enumerate(lines, start=1)
            if row.strip().startswith("@app.") and f'"{route.group(1)}"' in row
        ]
    return []


def check(fix: bool) -> int:
    docs = sorted((REPO_ROOT / "docs").glob("*.md")) + [REPO_ROOT / "README.md"]
    drifted: list[tuple[str, str, str, int, int]] = []
    unresolved: list[tuple[str, str, str]] = []
    files_changed: set[Path] = set()

    for doc in docs:
        text = original = doc.read_text(encoding="utf-8")

        def replace(match: re.Match[str]) -> str:
            label, target, line = match.group(1), match.group(2), int(match.group(3))
            path = (doc.parent / target).resolve()
            if not path.exists():
                unresolved.append((doc.name, label, target))
                return match.group(0)
            hits = resolve(label, path.read_text(encoding="utf-8").splitlines())
            if len(hits) != 1:
                unresolved.append((doc.name, label, target))
                return match.group(0)
            if hits[0] == line:
                return match.group(0)
            drifted.append((doc.name, label, path.name, line, hits[0]))
            if fix:
                files_changed.add(doc)
                return match.group(0).replace(f"#L{line}", f"#L{hits[0]}")
            return match.group(0)

        text = LINK.sub(replace, text)
        if fix and text != original:
            doc.write_text(text, encoding="utf-8")

    if drifted:
        verb = "已重算" if fix else "已漂移"
        print(f"[{verb}] {len(drifted)} 处：")
        for doc, label, name, old, new in drifted:
            print(f"  {doc} | {label} | {name} | L{old} -> L{new}")
    else:
        print("文档代码锚点：没有漂移。")

    if unresolved:
        print(f"\n[待人工复核] {len(unresolved)} 处（标签不是符号名，只能人工判断）：")
        for doc, label, target in unresolved:
            print(f"  {doc} | {label} | {target}")

    if drifted and not fix:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fix", action="store_true", help="重算能唯一确定符号的锚点")
    args = parser.parse_args(argv)
    return check(fix=args.fix)


if __name__ == "__main__":
    sys.exit(main())
