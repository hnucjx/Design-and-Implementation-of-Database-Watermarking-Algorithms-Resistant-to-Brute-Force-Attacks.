# 002 - 后端 API 层解耦：`main.py` 退化为装配根

| 项 | 值 |
| --- | --- |
| 计划时间 | 2026-10-02 16:16 +08:00 |
| 实施时间 | 2026-10-02 16:17 ~ 16:40 +08:00 |
| 依据 | [refactor.md](refactor.md) §4 路线图 R2、§3 判定准则、§5 DoD |
| 起点 commit | `c385646`（R1 提交后） |
| 本轮提交 | 待回填（见文末「提交与回滚」） |
| 结论 | **完成**。`main.py` 722 → 130 行、`create_app` 458 → 89 行；28 条路由拆进 7 个 `APIRouter`；后端 289 passed、前端 69 passed、`tsc` 退出码 0；**线上契约除「新增 12 条 description」外逐字段全等** |

---

## 1. 计划

### 1.1 目标

把 `backend/app/main.py` 里**内联声明的 28 条路由**拆成按资源分模块的 `APIRouter`，把「路由支撑逻辑」
（任务/条目定位、设置读写、产物路径、cookies 元数据）从 `create_app` 里下沉，使 `create_app`
只剩「建依赖 → 挂路由 → 挂静态资源」。

判据（[refactor.md §3](refactor.md#3-判定准则) 第 3 条「组合根只做装配」）：
**`create_app` 里出现业务分支即为越界。**

### 1.2 边界：本轮**不做**的事

1. **不改任何线上行为**（[refactor.md §3.1](refactor.md#31-目标与非目标) 第 5 条）：路径、方法、
   operationId、状态码、请求/响应模型一个都不动，用户可见文案逐字保留。
2. **不动 `schemas.py`**（线上契约）与 **`job_manager.py`**（属 R3 的领域层）。
3. **不改前端一行**。
4. **不收敛「产物路径的两条计算链」**：`job_artifacts.output_file`（本轮从 `main.py` 原样搬出）
   与 `job_manager._item_output_paths` 仍各算一次。收敛需要同时改 API 层与编排层，已登记为
   [refactor.md §4.1](refactor.md#41-后续候选清单登记为本轮三轮不做此后逐项单独一轮) 第 1 条。
5. **不引入依赖注入框架**（`dependency-injector` / `punq` 等）：用一个 `frozen` dataclass 挂在
   `app.state` 上即可，引依赖违反 §3.1 第 6 条。
6. **不给每个模块写新测试**：本轮只做结构搬迁（§3.1 第 7 条），新增测试仅用于给新抽出的**纯逻辑**
   兜底 —— 本轮没有新纯逻辑，因此 0 新增用例；行为不变性靠 OpenAPI 全等取证（见 §4.2）。

### 1.3 风险与对策

| 风险 | 对策 |
| --- | --- |
| **线上契约被静默改掉**（这是本项目影响面最大的一类事故） | 用 `app.openapi()` 做机器可读快照，逐 operation 比对路径/方法/operationId/参数/模型/状态码（见 §4.2） |
| 搬迁时手滑改写用户可见文案 | 同上：`description` 只允许「新增」或「在原文之后追加」，一旦改写就报错 |
| 路由拆走后 `Depends` 拿不到依赖 | 请求级依赖统一由 `api_context.ApiContext` 提供，路由不再直接读 `request.app.state.*` |
| 测试里 `monkeypatch.setattr` 的目标路径失效 | 3 处 `app.main.test_proxy` → `app.routers.diagnostics.test_proxy`（见 §3） |
| 文档里的代码锚点（`main.py#L135` 这类）整片失效 | 30 处锚点用带「期望命中次数」断言的脚本批量重定向（见 §4.3） |
| 分模块后挂载顺序变化影响 OpenAPI 里的 operation 排列 | `API_ROUTERS` 沿用重构前的声明次序，只把同一资源的聚在一起（`/api/cookies/verify` 随 cookies 归位） |

---

## 2. 实施方案

### 2.1 分层与文件落位

新增 **4 个模块 + 1 个包（7 个路由模块）**，全部落在 L0 入口层：

| 新文件 | 行数 | 承担什么 |
| --- | --- | --- |
| `backend/app/routers/__init__.py` | 29 | `API_ROUTERS`：只声明**挂载顺序**，不声明依赖 |
| `backend/app/routers/diagnostics.py` | 88 | `/health`、`/api/diagnostics`、`/api/diagnostics/runtime`、`/api/proxy/test` |
| `backend/app/routers/cookies.py` | 94 | `/api/cookies/verify`、`POST /api/cookies`、`/api/cookies/from-browser`、`DELETE /api/cookies` |
| `backend/app/routers/analyze.py` | 28 | `POST /api/analyze` |
| `backend/app/routers/jobs.py` | 190 | 建/列/取/取消/暂停/重启/重启子项/删子项/删任务/批量（10 条） |
| `backend/app/routers/job_files.py` | 45 | 4 个本地打开接口（播放 / 打开文件夹 × 任务 / 子项） |
| `backend/app/routers/settings.py` | 98 | `GET`/`PUT /api/settings`、`/api/settings/download-dir/select` |
| `backend/app/routers/events.py` | 24 | `GET /api/events`（SSE） |
| `backend/app/api_context.py` | 51 | `ApiContext`（`@dataclass(frozen=True)`）+ `get_context` / `get_session` + `ContextDep` / `SessionDep` 别名 |
| `backend/app/api_support.py` | 208 | 14 个路由支撑函数：`read_job_or_404` / `require_job` / `require_job_item` / `single_job_item` / `selected_entries` / `job_download_dir` / `extract_metadata_with_cookies` / `cookie_status_from_import` / `is_cookie_required_error` / `cookie_import_status_code` / `set_setting` / `apply_stored_settings` / `settings_response` / `select_directory_with_tkinter` |
| `backend/app/job_artifacts.py` | 98 | 产物**定位**一条链（`output_file` / `item_folder` / `output_folder` / `job_folder`）+ 本机**打开**（`open_local_path` / `open_video_path`） |

`main.py` 现在只剩：`configure_logging` → 依赖装配 → `ApiContext` 挂 `app.state` →
`for route_module in API_ROUTERS: app.include_router(...)` → 静态资源 → 1 条内联 `GET /`。

### 2.2 三条「落位判据」（写进了各新文件的文件头注释）

1. **需要 `Request` 吗？** 需要 → `routers/`；只是一段被多个路由复用的逻辑 → `api_support.py` / `job_artifacts.py`。
2. **它会改状态吗？** 会 → 一律委托 `JobManager`，路由层只写状态码。
3. **它算路径吗？** 算 → 只有 `job_artifacts.py` 一处，**禁止在路由层再写第三处候选计算**。

### 2.3 逐字搬迁的实现纪律

`api_support.py` 与 `job_artifacts.py` 的函数体是**逐字**从旧 `main.py` 的私有函数搬出的，
只去掉下划线前缀。两处例外，都写进了代码注释：

- `POST /api/cookies/verify` 的 docstring 保留 RST 双反引号 `` ``data/cookies.txt`` `` 原文 ——
  它已经发布在 `/openapi.json` 里，本轮**不改写任何既有的对外文字**。
- `job_artifacts.py` 文件头登记了「与 `job_manager._item_output_paths` 的收敛是下一轮候选」，
  防止后来者在路由层再写第三处候选计算。

---

## 3. 实施情况

| 指标 | 重构前 | 重构后 |
| --- | --- | --- |
| `backend/app/main.py` | **722** | **130**（−592） |
| `create_app` 函数体 | **458**（L53–L510） | **89**（L39–L127） |
| `main.py` 里内联的路由装饰器 | **28** | **1**（条件注册的 `GET /`） |
| `routers/*.py` 里的路由装饰器 | — | **27**（7 个模块） |
| 后端模块（`backend/app/*.py`） | 29 个 / 6026 行 | **32 个 / 5791 行**，另有 `routers/` 8 个 / 596 行 |
| 后端测试用例 | 289 | 289（未增未减） |
| 前端测试用例 | 69 | 69（未动） |

本轮**只改了一个测试文件**：`backend/tests/test_api.py` 的 3 处 `monkeypatch.setattr` 目标
`"app.main.test_proxy"` → `"app.routers.diagnostics.test_proxy"`。原因是 `test_proxy` 随路由
搬进了 `routers/diagnostics.py` —— 这是**测试的探针路径随实现位置变化**，不是断言变化。

---

## 4. 验证

### 4.1 门槛（[refactor.md §5](refactor.md#5-每轮的固定流程dod) 第 3 步）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 后端测试 | `./.venv/Scripts/python.exe -m pytest backend/tests -q -p no:cacheprovider --basetemp=tmp_pytest/run202` | **289 passed**（69.94s，1 warning） |
| 前端测试 | `cd frontend && npx vitest run --environment jsdom` | **69 passed**（11.61s） |
| 类型检查 | `cd frontend && npx tsc --noEmit` | 退出码 0 |
| 文档锚点 | `python scripts/check_doc_anchors.py` | **没有漂移**（「待人工复核」148 处，属正常项） |
| 文档链接 + UML | `python scripts/docs.py check` | 通过：本地链接有效，UML SVG 与 PlantUML 源一致 |
| 空白检查 | `git diff --check` | 干净 |

### 4.2 「行为不变」的取证（本轮重点）

脚本：`tmp_acceptance/refactor_r2_invariance.py`（一次性，gitignore，不进 CI）。
做法：用 `git worktree add --detach` 取 `HEAD`（= `c385646`）的整棵树，把工作区的
`frontend/dist` 复制进去（它是构建产物、不入库，缺了会让单端口模式的两条路由不注册），
两边各自 `import app.main` 并导出 `app.openapi()`，然后逐 operation 比对。

判据（写死在脚本里）：

- **路径集合、方法、operationId、参数、请求体、响应模型、状态码：必须全等。**
- `description` 是唯一允许的差异，且只允许两种形态：**新增**，或**在原文之后追加**。

结果：

```
operations：HEAD 28 条，重构后 28 条
components 相等：True
info 相等：True
新增 description 的 operation：12 条（唯一允许的差异）

全部通过：除新增 description 外，线上契约逐字段全等（路径、方法、operationId、参数、模型、状态码）。
```

**12 条新增 `description` 的来源**：给拆出去的处理函数补了 docstring，FastAPI 会把它当成
operation 的 `description`。这是「新增」，不是改写 —— 重构前这 12 条 operation 没有 `description`。

### 4.3 同步的文档

| 类别 | 文件 | 改了什么 |
| --- | --- | --- |
| 代码锚点 | 12 个 `docs/*.md` | **30 处** `main.py#Lxxx` 锚点重定向到 `routers/*`、`api_support.py`、`job_artifacts.py`；另有 3 处 `create_app` 锚点由 `--fix` 重算（`L53 → L39`）、1 处 `select_download_dir`（`L79 → L80`） |
| 锚点重定向脚本 | `tmp_acceptance/refactor_r2_retarget_anchors.py` | 30 条映射，每条带「期望命中次数」断言，命中数不符即报错 |
| 结构描述 | `docs/design.md` | 模块职责矩阵把 `main.py` 的职责收窄为「只做装配」，新增 5 行（`routers/` / `api_context` / `api_support` / `job_artifacts`）；扩展点表两行改写；分层规则新增第 6 条「组合根只做装配」 |
| 结构描述 | `docs/architecture.md`、`docs/4-plus-1-view.md`、`docs/implementation.md` | 「组件关系」「开发视图」「后端入口」三处整段重写，覆盖新的路由分层 |
| UML | `module-dependencies.puml` / `component-overview.puml` / `four-plus-one-development-view.puml` + 对应 3 张 SVG | L0 包由 `main.py` 一个节点扩成 5 个；新增 `Routers → ApiCtx/ApiSupport/Artifacts` 等边；note 补 L0 内部方向约定 |
| 总纲 | `ai/refactor/refactor.md` | §1 表格的行数/组件数写准；§2 新增「2.1 每轮结束后的刷新」（R2 后的新基线）；§4.1 第 1 条的 `main.py` 改为 `job_artifacts.py`；§6 索引回填 R1 hash 并写准 R2 的模块数 |

**没有动的文档**：`docs/index.md`、`docs/maintenance.md`、`docs/troubleshooting.md`、
`docs/safety-review.md` 的正文（只改了其中 2 处锚点）、`docs/user-manual.md` 的正文（只改锚点）。

---

## 5. 未覆盖 / 如实说明

1. **`docs/design.md` 被本进程之外的一次写入整文件覆盖过一次。** 现象：本轮的 5 处逻辑改动
   （模块矩阵 5 行 + 扩展点 2 行 + 规则第 6 条 + `config.py` 一行的 `main.py`→`api_support.py`）
   在 16:30 前后从工作区消失，文件变成 prettier 风格的重排版本（表格管道对齐、`job_manager.py`
   转义成 `job\_manager.py`），并且把 `[__main__.py]` **误转成 `[**main**.py]`**（语义变了：
   显示名从文件名变成加粗的 "main"）。
   处理：先把该版本原样备份到 `tmp_acceptance/design.md.external-reformat.bak`
   （sha256 `fde818707c199bbbd5ed8e6c2c9f8b8936fb2409ca3c034759c755dfefc19799`），
   再把 `docs/design.md` 复原为「`HEAD` + 本轮 5 处逻辑改动」重建，**不把该重排吸收进本轮提交**。
   已用归一化 diff 确认：除上述 `**main**` 一处语义差异外，那份重排与 `HEAD` 的差别只有表格对齐与转义。
   **这不是本轮的产出，也没有被测过**；它现在只存在于备份文件里。
2. **本轮只搬不收敛。** `job_artifacts.py` 的候选链与 `job_manager._item_output_paths` 仍是两条
   独立实现，两条链之间**没有**一致性测试。本轮的价值是「把它从 `main.py` 挪到一个有名字的地方」，
   不是「消除重复」。
3. **`create_app` 里仍有 1 条内联路由**（`GET /`）。它只在 `frontend/dist/index.html` 与
   `dist/assets/` 都存在时**条件注册**，搬进 `routers/` 需要一个额外的「是否托管前端」开关，
   属于把「装配期才知道的事实」透传给路由层。判为不值得，故保留。
4. **12 条新增 `description` 已进入线上契约。** 它们不改变任何行为，但会让 `openapi.yaml`（手写）
   与 `app.openapi()`（运行时）多 12 处差异。本轮**没有**重新生成/校对 `openapi.yaml` ——
   该文件与 `frontend/src/types.ts` 的单一来源问题已登记为
   [refactor.md §4.1](refactor.md#41-后续候选清单登记为本轮三轮不做此后逐项单独一轮) 第 3 条。
5. **本轮的自动化边界**：OpenAPI 全等能证明「契约没变」，**不能**证明「路由内部逻辑逐字没变」。
   逐字性是靠「搬迁时函数体原样剪切 + 289 个既有用例」间接覆盖的，没有做函数体级的多重集合比对
   （R1 对前端做过，后端没有对应的便宜做法：函数都是方法绑定到 `app` 上的闭包，静态比对会大量假阳性）。
6. **沙箱环境专有坑（新增一条，建议并入 [refactor.md §5](refactor.md#5-每轮的固定流程dod) 的环境说明）**：
   `--basetemp` 指向的目录**必须不存在**。pytest 在会话启动时若发现 `--basetemp` 已存在，会
   `shutil.rmtree` 它；工作区里一次删除 137 个文件会触发沙箱的批量删除守卫
   （`SAFE_DELETE_BULK_CONFIRM_REQUIRED`，阈值 50），表现为**每个用到 `tmp_path` 的用例都
   error at setup**（本次实测：104 passed / 185 errors），而错误栈指向 `pytest_asyncio` 的
   fixture 钩子，**看起来像插件坏了**。本次的 `tmp_pytest/run200` 就是因为在更早的一次运行中
   被创建过而复现。换一个从未存在过的目录名（`run202`）即恢复正常。

---

## 6. 风险与回滚

- **回滚方式**：`git revert <本轮提交 hash>`。本轮是「纯新增文件 + `main.py` 一处重写 + 测试 3 行 +
  文档锚点重定向」，revert 无冲突预期。
- **回滚后需要重跑的**：`python scripts/check_doc_anchors.py --fix`（30 处锚点会漂回 `main.py` 的老行号）、
  `python scripts/docs.py render`（3 张 UML 要退回旧版）。
- **已知副作用**：`backend/app` 的模块数 29 → 32（+ `routers/` 8 个）。文件粒度更细，代价是
  「找一个接口」需要先知道它属于哪个资源。

---

## 7. 关联

- 上级：[refactor.md](refactor.md) §4 路线图 R2、§3 判定准则第 3 条、§5 每轮 DoD。
- 前一轮：[001 前端视图层解耦](001-frontend-view-layer.md)（`c385646`）；本轮起点即该提交。
- 下一轮：[003 降级决策纯化](003-resolution-decisions.md)（R3，尚未开始）。
- 背景缺陷：本项目历史上最贵的一类问题是「降级链路被判据漏掉」，
  见 [010-unselectable-probe-raise-skips-the-fallback](../bug-fix/010-unselectable-probe-raise-skips-the-fallback.md)，
  那是 R3 要解决的问题域；本轮与它无交集。

---

## 8. 提交与回滚

| 项 | 值 |
| --- | --- |
| 提交 hash | 待回填（记录与改动在同一次提交里，hash 无法写进自身） |
| 回滚命令 | `git revert <hash>` |
| 记录约定 | 与 [bug-fix](../bug-fix/README.md) / [ui](../ui/README.md) 一致：下次触碰 `ai/refactor/` 时回填 hash |
