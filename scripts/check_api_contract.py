"""校验「可执行契约」与它的两份手写副本没有漂移。

单一来源是**运行时的 `app.openapi()`** —— 它由 `backend/app/schemas.py` 与各路由的响应模型生成，
是唯一「改代码就跟着变」的那份。另外两份是手写的副本，本脚本把它们与运行时对照：

- `frontend/src/types.ts`：按 `SCHEMA_PAIRS` 逐接口校验**字段名集合完全相同**；
- `docs/openapi.yaml`：校验**路径 × 方法**集合与 **paths 引用到的 schema** 集合。

**为什么不生成？** 生成 `types.ts` 或 `openapi.yaml` 需要引入代码生成工具链（连带 npm 依赖），
违反 [refactor.md §3.1](../../ai/refactor/refactor.md#31-目标与非目标) 第 6 条。所以这里取
「单一来源 + 漂移校验」：不生成，但让漂移在 CI 里**红**。

**为什么不做深度结构比对？** 实测（见 [ai/refactor/006](../../ai/refactor/006-api-contract-drift.md)）：
两侧在 `title`（FastAPI 自动生成）、`example` / `default`（文档增强）、nullable 的
`anyOf` vs `type` 表达上差异 **731 处** —— 深度比对会淹没真正要紧的信号（少一个字段、少一个接口）。

用法：

    python scripts/check_api_contract.py             # 全量校验（有 PyYAML 时含 openapi.yaml）
    python scripts/check_api_contract.py --skip-yaml # 只校验 types.ts
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))


# TS 接口名 → 运行时 schema 名。
#
# 名字不一致是历史原因：后端用 `Read` 后缀区分「读模型」与「写入模型」，而前端只消费读模型。
# 值为 None = 前端比运行时**更具体**（运行时那边是内联的宽松结构），无法做字段集合比对 ——
# 这类接口仍要求「必须存在于 types.ts」，避免被悄悄删掉而无人察觉。
SCHEMA_PAIRS: dict[str, str | None] = {
    "FormatOption": "FormatOption",
    "SubtitleOption": "SubtitleOption",
    "VideoEntry": "VideoEntry",
    "AnalyzeResponse": "AnalyzeResponse",
    "DownloadOptions": "DownloadOptions",
    "ResolutionFallback": "ResolutionFallback",
    "JobItem": "JobItemRead",
    "Job": "JobRead",
    "JobBatchActionResponse": "JobBatchActionResponse",
    "DeleteJobItemsResponse": "DeleteJobItemsResponse",
    "Settings": "SettingsRead",
    "CookieStatus": "CookieStatus",
    "ProxyTestResult": "ProxyTestRead",
    "Diagnostics": "DiagnosticsRead",
    "CookieHealth": "CookieHealthRead",
    # 运行时里 `sanitized_environment` 是 `array of object(str→str)`，没有具名 schema。
    "EnvironmentRisk": None,
    # 错误响应的 detail 不在运行时的 schemas 里（见 frontend/src/api.ts 的手写解析）。
    "ApiErrorDetail": None,
}

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "options", "head")

# `docs/openapi.yaml` 是**手写**的对外文档，它在两处刻意不跟随 FastAPI 的自动命名：
#   1) 错误响应用领域化的 `Error` / `CookieImportError`，而不是 FastAPI 生成的 `HTTPValidationError`；
#   2) 文件上传的请求体写成显式 requestBody，不暴露 `Body_upload_*` 这种由函数签名拼出来的 schema。
# 这是**有意**的差异，登记在这里；一旦集合再变化（多一个或少一个），校验就会失败，
# 要求人过一遍 —— 而不是让它成为「反正一直红着，没人看」的噪音。
DOCUMENT_SCHEMA_SURPLUS = {"CookieImportError", "Error"}
RUNTIME_SCHEMA_SURPLUS = {"Body_upload_cookies_api_cookies_post", "HTTPValidationError"}
TS_INTERFACE = re.compile(r"^export interface (?P<name>\w+)\s*\{(?P<body>.*?)^\}", re.M | re.S)
TS_FIELD = re.compile(r"^\s*(?P<name>\w+)\??\s*:", re.M)


def parse_typescript(source: str) -> dict[str, set[str]]:
    """从 `types.ts` 取出每个 `interface` 的字段名集合（`type` 别名不参与）。"""
    interfaces: dict[str, set[str]] = {}
    for match in TS_INTERFACE.finditer(source):
        interfaces[match.group("name")] = {field.group("name") for field in TS_FIELD.finditer(match.group("body"))}
    return interfaces


def operations(document: dict) -> set[tuple[str, str]]:
    return {
        (path, method.lower())
        for path, item in (document.get("paths") or {}).items()
        for method in item
        if method.lower() in HTTP_METHODS
    }


def referenced_schemas(document: dict) -> set[str]:
    """`paths` 下所有指向 `#/components/schemas/...` 的引用名（只看 API 表面，不看不相关的独立 schema）。"""
    names: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
                names.add(ref.rsplit("/", 1)[-1])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(document.get("paths") or {})
    return names


def check_types(runtime: dict, interfaces: dict[str, set[str]]) -> tuple[list[str], int]:
    """返回（问题列表, 实际比对了多少个接口）。"""
    problems: list[str] = []
    schemas = (runtime.get("components") or {}).get("schemas") or {}
    compared = 0

    for ts_name, schema_name in SCHEMA_PAIRS.items():
        if ts_name not in interfaces:
            problems.append(f"types.ts 缺少接口 {ts_name}（已在 SCHEMA_PAIRS 登记，必须存在）")
            continue
        if schema_name is None:
            continue
        schema = schemas.get(schema_name)
        if schema is None:
            problems.append(f"运行时契约缺少 schema {schema_name}（types.ts 的 {ts_name} 指向它）")
            continue
        expected = set(schema.get("properties") or {})
        actual = interfaces[ts_name]
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        compared += 1
        if missing or extra:
            detail = []
            if missing:
                detail.append(f"types.ts 缺 {missing}")
            if extra:
                detail.append(f"types.ts 多 {extra}")
            problems.append(f"{ts_name} ↔ {schema_name} 字段不一致：{'；'.join(detail)}")

    unregistered = sorted(set(interfaces) - set(SCHEMA_PAIRS))
    if unregistered:
        problems.append(f"types.ts 有未登记的接口：{unregistered}（新接口要么进 SCHEMA_PAIRS，要么说明它不属于线上契约）")

    return problems, compared


def check_document(runtime: dict, document: dict) -> tuple[list[str], int]:
    """`docs/openapi.yaml` 与运行时契约的「API 表面」对照。返回（问题列表, 比对的条目数）。"""
    problems: list[str] = []

    runtime_ops, document_ops = operations(runtime), operations(document)
    if runtime_ops != document_ops:
        extra = sorted(document_ops - runtime_ops)
        missing = sorted(runtime_ops - document_ops)
        problems.append(f"路径 × 方法不一致：文档多 {extra}，文档少 {missing}")

    runtime_refs, document_refs = referenced_schemas(runtime), referenced_schemas(document)
    surplus = document_refs - runtime_refs
    missing = runtime_refs - document_refs
    if surplus != DOCUMENT_SCHEMA_SURPLUS or missing != RUNTIME_SCHEMA_SURPLUS:
        problems.append(
            "paths 引用的 schema 出现**未登记**的差异："
            f"文档多 {sorted(surplus)}，文档少 {sorted(missing)}"
            f"（登记在案的是 文档多 {sorted(DOCUMENT_SCHEMA_SURPLUS)}、文档少 {sorted(RUNTIME_SCHEMA_SURPLUS)}）"
        )

    return problems, len(runtime_ops) + len(runtime_refs)


def runtime_openapi(repo: Path) -> dict:
    """构建应用并取运行时契约。副作用被限制在仓库内的 scratch 目录（gitignore）。

    注意：`import app.main` 会执行模块末尾的 `app = create_app()`。所以先把 `configure_logging`
    换成 no-op、并把日志整体静音 —— 否则本脚本的结论会被十几行 INFO 淹没。
    """
    import logging

    import app.logging_setup as logging_setup

    # main.py 用 `from .logging_setup import configure_logging`，在它执行时绑定；
    # 先改模块属性，能让那次绑定的就是 no-op。
    logging_setup.configure_logging = lambda *_args, **_kwargs: None
    logging.disable(logging.CRITICAL)
    try:
        import app.main as main_module
    finally:
        logging.disable(logging.NOTSET)

    from app.config import AppSettings

    scratch = repo / "tmp_acceptance" / "api_contract_scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    settings = AppSettings(
        data_dir=scratch / "data",
        download_dir=scratch / "downloads",
        database_path=scratch / "data" / "app.sqlite3",
        default_concurrency=1,
    )
    return main_module.create_app(settings=settings, sanitize_environment_variables=False).openapi()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT, help="仓库根目录")
    parser.add_argument("--skip-yaml", action="store_true", help="跳过 docs/openapi.yaml（或本机没有 PyYAML 时）")
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    runtime = runtime_openapi(repo)

    problems: list[str] = []

    types_path = repo / "frontend" / "src" / "types.ts"
    interfaces = parse_typescript(types_path.read_text(encoding="utf-8"))
    type_problems, compared = check_types(runtime, interfaces)
    problems += type_problems
    print(f"[1/2] frontend/src/types.ts ↔ app.openapi()：比对了 {compared} 个接口")
    for item in type_problems:
        print(f"      ✗ {item}")

    if args.skip_yaml:
        print("[2/2] docs/openapi.yaml：按 --skip-yaml 跳过")
    else:
        try:
            import yaml
        except ModuleNotFoundError:
            print("[2/2] docs/openapi.yaml：跳过（本机没有 PyYAML；pip install pyyaml 后自动启用）")
        else:
            document = yaml.safe_load((repo / "docs" / "openapi.yaml").read_text(encoding="utf-8"))
            document_problems, checked = check_document(runtime, document)
            problems += document_problems
            print(f"[2/2] docs/openapi.yaml ↔ app.openapi()：比对了 {checked} 个条目（路径×方法 + 引用 schema）")
            for item in document_problems:
                print(f"      ✗ {item}")

    print("")
    if problems:
        print(f"契约校验失败：{len(problems)} 处漂移")
        return 1
    print("契约校验通过：运行时契约与两份手写副本一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
