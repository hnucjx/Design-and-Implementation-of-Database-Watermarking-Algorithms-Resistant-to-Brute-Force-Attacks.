# 007 - CI 门槛 + 分层依赖校验

| 项 | 值 |
| --- | --- |
| 计划时间 | 2026-10-02 17:29 +08:00 |
| 实施时间 | 2026-10-02 17:29 ~ 17:38 +08:00 |
| 依据 | [refactor.md](refactor.md) §4.1 第 4 项「没有 lint / format / CI 门槛」 |
| 起点 commit | `ffe096c`（R6 提交后） |
| 本轮提交 | 待回填（见文末「提交与回滚」） |
| 结论 | **部分完成**。建了 CI（`ci.yml`）与两个**本仓库自己的**门槛（分层依赖、契约漂移已在 006）；**仍不引入** ruff / eslint / prettier —— 这是有意的，不是遗漏 |

---

## 1. 计划

### 1.1 目标

[refactor.md §4.1](refactor.md#41-后续候选清单登记为本轮三轮不做此后逐项单独一轮) 第 4 项：

> **没有 lint / format / CI 门槛**：仓库无 `.github/`、无 ruff/eslint/prettier 配置。
> 引入它们会对 6000+ 行既有代码产生大量机械改动，属独立议题。

这句话里其实**藏着两件事**：一件是「没有自动化门槛」，另一件是「没有通用风格检查」。
本轮做前者（且是**对症的**门槛），不做后者 —— 理由见 §2.1。

### 1.2 边界：本轮**不做**的事

1. **不引入 ruff / eslint / prettier** —— 本项**唯一**让步的地方，理由见 §2.1。
2. **不新增任何依赖**（pip / npm 都不加；CI 里只用官方 GitHub Action）。
3. **不改任何应用代码**：`backend/app/**` 与 `frontend/src/**` 一行未动。
4. **不把 `docs.py check` 搬进 CI**：它需要 Java + PlantUML jar + Graphviz，属本地工具链（§2.3）。
5. **不做「格式统一」类改动**：那正是 ruff / prettier 的副产物，也是本节第 1 条要避免的。

### 1.3 风险与对策

| 风险 | 对策 |
| --- | --- |
| 校验脚本永远绿、其实抓不住违规 | **注入式取证**：把一条「依赖朝上」的 import 塞进 L3 模块，确认 EXIT=1 并精确报出（§4.2） |
| `docs/design.md` 的表格格式一变，解析出空表 → 「全部模块未登记」的误导性报错 | 解析为空时**显式**报「无法解析出任何模块」并退出 1，而不是让下游报一堆假错 |
| CI 配置写错但本机发现不了 | YAML 用 `yaml.safe_load` 校验；每条命令本地跑过、退出码语义确认（§4.3）；仍无法验证 runner 层 —— 见 §5 第 1 条 |

---

## 2. 实施方案

### 2.1 为什么不是 ruff / eslint / prettier

三条理由，按分量排序：

1. **它们都要装**（pip / npm）→ 撞 [§3.1 第 6 条](refactor.md#31-目标与非目标)。
2. **对 6000+ 行既有代码会产生大量机械改动** —— 一旦开始，这一轮的 diff 里就再也看不出
   「架构上真正改了什么」。
3. **本仓库真正需要的不是通用风格检查，而是架构约束的机器化。**
   [§3 第 2 条](refactor.md#3-判定准则)（依赖方向单一且向下）是本仓库用真实缺陷换来的准则之一，
   而 ruff / eslint **默认都不检查它**（要看分层得自己写插件或依赖 import-linter 之类的额外包）。
   所以这里写了一个**更对症**的脚本 —— 它比通用 lint 更小、更准、零依赖。

**候选 4 因此只完成了一半**：CI 与门槛有了，format / lint 没有。这是决策，不是漏做。

### 2.2 分层校验 `scripts/check_layers.py`（142 行）

把 §3 第 2 条从「口头准则」变成「可执行判据」。

**关键设计：单一来源是 `docs/design.md` 的模块职责矩阵 —— 脚本解析它。**

```text
解析矩阵 → {模块名: 层号}
  ↓
与 backend/app 下**实际**的模块集合对比
  ├─ 差集非空 → 报「XXX.py 没有登记在矩阵里」
  └─ 逐条 import 边检查：层 n 的模块只能 import 层 m ≥ n 的模块
       （唯一例外：L1 契约 —— config / schemas / models 是各层共用的接口定义）
```

这个设计让同一个脚本同时干两件事：**校验架构** 与 **校验文档完整性**。
后者立刻见效 —— 见 §3.1。

### 2.3 CI（`.github/workflows/ci.yml`，79 行）

| job | 步骤 |
| --- | --- |
| 后端 | `pip install -e "backend[dev]"` → `pytest` → `check_layers.py` → `check_api_contract.py` → `check_doc_anchors.py` |
| 前端 | `npm ci` → `tsc --noEmit` → `vitest run --environment jsdom` → `vite build` |

**刻意不入 CI 的两项**（都写在 `ci.yml` 顶部的注释里）：

| 不做 | 为什么 |
| --- | --- |
| `pytest --basetemp=tmp_pytest/runNNN` | 那个参数是**本机沙箱**拦删除导致的绕过（见 refactor.md §5），干净的 Linux runner 不需要，带上反而会让人以为它是标准做法 |
| `docs.py check` | 它要逐字节比对 UML SVG 与「现场用 Java + PlantUML + Graphviz 渲染」的结果，需要下载 jar。属本地工具链，留在 DoD。CI 仍跑 `check_doc_anchors.py`（纯标准库） |

`check_api_contract.py` **不带** `--skip-yaml`：让它在 CI 里自己判断并**打印**「跳过
docs/openapi.yaml（本机没有 PyYAML）」—— 与其静默只跑一半，不如让日志里能直接看到跑了哪一半。

---

## 3. 实施情况

| 指标 | 值 |
| --- | --- |
| 新增 | `.github/workflows/ci.yml`（79 行）、`scripts/check_layers.py`（142 行） |
| 改动的既有文件 | `docs/design.md`（补 005 遗留的登记）、`docs/development.md`（补「结构性检查」与「CI」两节）、`refactor.md` |
| 应用代码改动 | **零** |
| 新增依赖 | **零** |
| 分层校验覆盖 | 34 个已登记模块 / 40 个实际模块（`routers/` 下 7 个文件共用矩阵里的一行） |

### 3.1 顺带修掉的一处 005 遗留（也是这个门槛的必要性证明）

`check_layers.py` **第一次运行**就报出两处：

```text
  ✗ job_manager.py 里的 `from .safe_delete` 指向未登记的模块
  ✗ safe_delete.py 没有登记在 docs/design.md 的模块职责矩阵里
```

即：005 新增的 `safe_delete.py` 漏了矩阵登记 —— 005 那轮只跑了 `check_doc_anchors.py`，
而锚点检查**看不到**「新模块没进矩阵」这件事（它只校验已存在的锚点指向的行号）。

本轮把它补进 `docs/design.md`（按 **L3 领域**，与同类的 `output_paths.py` 一致）。
补后重跑：34 个已登记 / 40 个实际，**EXIT=0**。

> 上一轮刚犯的错，这一轮的脚本当场就抓住了 —— 这比任何「我觉得它有用」的论证都硬。

---

## 4. 验证

### 4.1 门槛（[refactor.md §5](refactor.md#5-每轮的固定流程dod) 第 3 步）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 分层校验 | `python scripts/check_layers.py` | **EXIT=0**（34 已登记 / 40 实际） |
| 契约校验 | `python scripts/check_api_contract.py` | **EXIT=0** |
| 代码锚点 | `python scripts/check_doc_anchors.py` | **EXIT=0**，没有漂移 |
| 文档链接与 UML | `python scripts/docs.py check` | 通过 |
| 后端测试 | `python -m pytest backend/tests -q --basetemp=tmp_pytest/runNNN` | **354 passed**（未改应用代码） |
| 前端测试 | `npx vitest run --environment jsdom` | **69 passed** |
| 空白错误 | `git diff --check` | 无输出 |

### 4.2 取证：证明分层校验抓得住（[§3 第 6 条](refactor.md#3-判定准则)）

「脚本能通过」是弱证据（永远返回 0 的脚本也能通过）。两个真实证据：

| 证据 | 怎么来的 | 结果 |
| --- | --- | --- |
| **未登记模块** | **天然发生**：脚本首跑时 005 的 `safe_delete.py` 还没登记 | EXIT=1，报出 2 处（见 §3.1） |
| **依赖朝上** | **主动注入**：在 L3 的 `safe_delete.py` 里加 `from .job_manager import JobManager`（L2） | EXIT=1，报 `safe_delete.py（L3 领域）import 了 job_manager.py（L2 编排）—— 依赖方向朝上` |

注入后已还原（`git diff backend/app/safe_delete.py` 为空），还原后复跑 EXIT=0。

### 4.3 CI 在本机验证到什么程度

| 验证了 | 没验证 |
| --- | --- |
| `ci.yml` 能被 `yaml.safe_load` 解析（2 个 job、7 + 6 个 step） | **GitHub Actions 真的会不会跑通**（本机没有 runner） |
| 每条命令都在本机跑过、通过 | Action 版本可用性（`actions/checkout@v4` / `setup-python@v5` / `setup-node@v4`） |
| 三个脚本的退出码语义（0 = 通过 / 1 = 失败） | Ubuntu runner 上的环境差异（如 yt-dlp / ffmpeg 的可用性） |
| —— | `npm ci` 在干净环境能否复现（本机用的是已装好的 `node_modules`） |

**第一次 push 之后 CI 可能红。** 这是本轮最大的不确定项，见 §5 第 1 条。

---

## 5. 未覆盖 / 如实说明

1. **CI 没有被真正跑过 —— 本轮最大的不确定项。**
   本机没有 GitHub runner，`ci.yml` 的正确性只到「语法正确 + 每条命令本地通过 + 退出码语义对」这一层。
   `actions/*` 的版本、runner 镜像里 Python 3.12 / Node 22 的实际行为、`npm ci` 在干净环境的结果，
   **都没有验证过**。第一次 push 后需要单独看一次 CI 结果（可能还要调一轮）。

2. **候选 4 只完成了一半。** format / lint **没有**引入（理由见 §2.1）。
   这一项的标题是「没有 lint / format / CI 门槛」，本轮补的是「CI 门槛」与两个本仓库自己的检查，
   **不是** lint / format。不要把它当成「候选 4 已完成」。

3. **分层校验只做静态 import 分析。** 不看运行时动态导入（`importlib`、字符串形式的模块名）。
   当前代码库没有这类用法，但脚本对「将来引入动态导入」无防护。

4. **分层判据依赖矩阵的表格格式。** 列序、表头写法、行首格式一变，解析就会失效 ——
   脚本在解析为空时会**显式报错**（不会静默放过），但「解析到了但解析错」这种情况无法自检。
   例如把某行的层写成 `L3`，而它其实该是 `L2` —— 脚本无法发现，因为它**信任矩阵**。

5. **`routers/` 在矩阵里只有一行（L0）**，7 个 router 文件共用。将来若有某个 router 需要单独分层，
   矩阵格式要先改，脚本才能表达。

6. **契约校验在 CI 里只跑一半**（没装 PyYAML 就跳过 `openapi.yaml`）—— 详见 [006](006-api-contract-drift.md) §5 第 5 条。

7. **CI 不跑 `git diff --check`。** 它检查的是「工作区 / 索引 vs HEAD」，在 CI 的干净检出上恒为空，
   放进去只是形式。留在本地 DoD。

8. **CI 不跑 `docs.py check`**，所以 UML SVG 与 `.puml` 源不一致这件事，**只能靠本机发现**。

---

## 6. 风险与回滚

| 项 | 值 |
| --- | --- |
| 风险等级 | 低（新增 CI 配置与只读校验脚本；不改应用代码、不改契约） |
| 回滚命令 | `git revert <hash>` |
| 回滚影响 | 无。删除 `.github/` 与 `check_layers.py` 即回到「没有自动化门槛」的状态 |

---

## 7. 关联

- **上游**：[refactor.md](refactor.md) §4.1 第 4 项、§3 第 2 条（分层准则）。
- **依赖**：[006](006-api-contract-drift.md) 的校验脚本被接进 CI —— 到这一步，
  006 说的「漂移会让 CI 红」才**真正成立**（在此之前它只是一个可手动跑的命令）。
- **补漏**：修掉 005 遗留的模块矩阵登记缺口。

---

## 8. 提交与回滚

| 项 | 值 |
| --- | --- |
| 提交 hash | 待回填（记录与改动在同一次提交里，hash 无法写进自身） |
| 回滚命令 | `git revert <hash>` |
| 记录约定 | 与 [bug-fix](../bug-fix/README.md) / [ui](../ui/README.md) 一致：下次触碰 `ai/refactor/` 时回填 hash |
