# YouTube Downloader（cascade）工程文档撰写 Prompt

你是一名资深软件工程技术写作者、系统分析师和文档架构师。请基于当前仓库的**真实代码**、`README.md`、`PLAN.md`、后端 API schema、前端类型定义、测试用例和项目配置，持续维护 YouTube Downloader 项目的软件工程标准文档体系。

本文件是**可重复执行的任务说明**，不是一次性指令：每次文档任务都应从「输入来源」重新取证，而不是沿用本文件的历史描述。仓库演进后，本文件本身也必须同步修订。

---

## 1. 总目标

在不修改任何产品或测试源码的前提下，维护 `docs/` 下的专业文档体系，并把根目录 `README.md` 收敛为简短入口页。文档要服务三类读者：

- **普通用户**：如何安装、运行、配置、使用和排障。
- **开发者**：如何理解架构、模块边界、API、数据模型、线程/事件模型、测试和维护约定。
- **后续维护者/审查者**：如何判断文档是否与代码一致，如何在功能变化时同步更新文档与图。

---

## 2. 必须遵守的约束

- **只改文档与文档产物**：允许修改 `README.md`、`docs/**`、`scripts/docs.py` 的文档相关行为，以及 `ai/` 下的任务文件；不得修改 `backend/app/**`、`frontend/src/**`、`backend/tests/**` 等产品与测试源码。
- **一切以仓库事实为准**：不臆测不存在的功能、接口、配置或部署方式。每个数字（用例数、默认值、阈值、端口、并发数）必须能在仓库中找到出处。
- **README 只做入口页**：保留一句话定位、合规提醒、快速启动、文档导航表、测试命令入口和维护提示；详细内容一律链接到 `docs/`。
- **禁止重复叙述**：各文档之间不得复制大段相同说明；复用一律用相对链接交叉引用（含锚点）。
- **引用必须本地可跳转**：引用源文件、文档、代码行、类名、类型和命令时使用相对路径与行锚，例如 `../backend/app/main.py#L98`、`../frontend/src/api.ts#L24`、`implementation.md#任务调度`。
- **区分「已核验」与「推断」**：凡结论依赖实验或数据的，注明取证方式与时间基线；不确定处显式标注。
- **合规边界**：涉及 YouTube、cookies、PO token、下载稳定性、版权或权限限制时保持合规与边界清晰：不承诺绕过 DRM、会员、地区、年龄、私有视频等权限限制，不把「规避风控」写成产品目标。
- **风险项必须写明代价**：`throttledratelimit`、aria2c 多连接等会影响 403 率的开关，必须在文档中同时给出「收益」与「代价」，并说明默认值与回退方式。

---

## 3. 输入来源（每次任务至少核查一遍）

### 3.1 任务与计划文档

- `ai/docs.md`：文档任务的原始需求（软件工程规范、UML、可跳转引用、README 收敛、持续同步）。
- `ai/docs-prompt.md`：本文件，文档任务的可重复说明书。
- `PLAN.md`（仓库根）：下载性能与稳定性修复计划的根因分析、修复项、验证与回滚；文档中引用性能相关结论时以此为权威来源。
- `ai/plan.md`、`ai/refactor.md`、`ai/tasks.md`、`ai/brainstorming.md`：历史计划与重构记录。
- `ai/perf/`：性能相关的临时材料目录（若为空则不引用）。

### 3.2 后端（`backend/app/`）

| 模块 | 关注点 |
| --- | --- |
| `main.py` | FastAPI 应用装配、全部路由、错误状态码、静态资源托管、设置读写、cookies 端点、lifespan。 |
| `config.py` | `AppSettings` 全部字段、`YTDL_` 前缀、默认值、环境变量语义。 |
| `schemas.py` | 全部请求/响应模型与 `Literal` 枚举，是 API 文档与 OpenAPI 的唯一真源。 |
| `models.py` | `Job` / `JobItem` / `JobEvent` / `Setting` 表结构与 `JobStatus`。 |
| `db.py` | engine 创建、SQLite pragma（WAL、`busy_timeout`、`synchronous`）、`_ensure_columns` 补列、`checkpoint_wal`。 |
| `job_manager.py` | 队列、worker、并发、暂停/重启/删除、进度 hook、错误分类、终态聚合、cookies 刷新重试。 |
| `job_read_model.py` | 读模型投影：聚合分辨率/格式、`elapsed_seconds`、降级消息。 |
| `ytdlp_service.py` | yt-dlp 边界层：元数据解析、预检测、下载参数、profile 重试链、依赖诊断、错误分类。 |
| `ytdlp_formats.py` | 格式选择器、分辨率/格式/大小提取、降级候选计算、720p 底线。 |
| `stall_guard.py` | 停滞看门狗与 `DownloadStalled` 语义。 |
| `download_progress.py` | 多子流进度聚合与分母策略。 |
| `progress_persist.py` | 进度落库节流门。 |
| `transfer_stats.py` | 平均速度计算。 |
| `fallback_policy.py` | 降级原因常量与用户可读消息、重启建议。 |
| `events.py` | SSE 事件代理。 |
| `output_paths.py` | 输出文件/中间文件/sidecar 路径解析与发现。 |
| `paths.py` | 路径名安全化。 |
| `log_safety.py` | 日志敏感信息清洗。 |
| `browser_cookies.py` | 浏览器 cookies 导入、域名过滤、锁库与 CDP fallback。 |
| `system_open.py` | 本地播放器选择与文件夹打开。 |

### 3.3 前端（`frontend/src/`）

- `App.tsx`：状态编排、SSE 订阅、解析/建任务/设置保存流程、cookies 交互、各面板（`UrlAnalyzer`、`AnalysisPanel`、`DownloadOptionsPanel`、`SettingsPanel`）。
- `api.ts`：HTTP 边界与 `ApiError` 语义。
- `types.ts`：与后端 schema 逐字段对应的前端类型。
- `components/JobQueue.tsx`：任务中心展示与本地文件操作入口。
- `quality.ts`、`formatting.ts`：清晰度选项/降级按钮文案、格式化工具。
- `App.test.tsx`、`test/`：前端测试范围与夹具。

### 3.4 配置、脚本与测试

- `backend/pyproject.toml`、`frontend/package.json`：依赖、脚本、测试配置。
- `backend/tests/`（10 个 `test_*.py` + `fakes.py`）、`frontend/src/App.test.tsx`：测试策略与覆盖范围。
- `scripts/docs.py`：文档工具链（`bootstrap` / `render` / `check`）与固定版本 PlantUML。
- `scripts/bench_concurrency.py`、`scripts/bench_throttle_guard.py`：可离线复现的并发与节流基准。
- `.gitignore`、`.editorconfig`：哪些产物不入库、写作格式约定。

> 每次任务请用 `git log --oneline` 与 `git status -sb` 确认当前基线，并在文档中的性能/行为结论处标注对应的计划或提交。

---

## 4. 交付物

文档位于 `docs/`，每份文档必须包含：清晰标题、适用读者、相关文档链接、必要的源码引用。

### 4.1 核心文档

| 文档 | 职责 |
| --- | --- |
| `docs/index.md` | 文档总入口：按读者角色给出阅读路径、文档职责清单、UML 索引。 |
| `docs/user-manual.md` | 用户手册：启动、解析、选项、任务中心、cookies、下载目录与产物、排障入口。 |
| `docs/requirements.md` | 需求分析：目标、用户角色、功能需求（带代码依据）、非功能需求、约束边界、验收口径。 |
| `docs/architecture.md` | 架构设计：系统上下文、容器职责、组件关系、关键数据流、状态生命周期、数据模型、设计取舍。 |
| `docs/4-plus-1-view.md` | Kruchten 4+1 视图审查入口：逻辑/开发/进程/物理/场景视图与持续更新规则。 |
| `docs/design.md` | 设计文档：核心模块职责矩阵、依赖方向与分层规则、线程与事件循环模型、关键数据流（解析/下载/进度/设置变更/删除）、并发与持久化设计取舍、扩展点与已知限制。 |
| `docs/development.md` | 开发文档：技术栈、环境要求、依赖安装、运行模式、目录结构、**完整环境变量表（默认值必须与 `config.py` 一致）**、文档工具链、开发检查。 |
| `docs/api.md` | API 文档：endpoint 全表、请求/响应模型、错误语义与状态码、任务状态、诊断字段，并链接 OpenAPI spec。 |
| `docs/openapi.yaml` | OpenAPI 3.x spec：全部 endpoint、参数、请求体、响应、错误响应与可运行示例。 |
| `docs/technical.md` | 技术文档：清晰度/格式选择、预检测、字幕来源、降级原因、稳定下载策略（含节流守卫、停滞看门狗、aria2c）、cookies、PO token、失败排查顺序、文件删除语义。 |
| `docs/implementation.md` | 实现文档：后端入口、数据模型、任务调度、yt-dlp 封装、进度与平均速度、读模型、前端实现、日志安全。 |
| `docs/testing.md` | 测试文档：自动测试命令、后端/前端测试范围、手动验收步骤、高风险回归点、文档验收。 |
| `docs/maintenance.md` | 维护文档：文档同步原则、变更 checklist、排障流程、UML 更新流程、提交流程、审查重点。 |
| `docs/documentation-workflow.md` | 文档写作与生成环境：工具链交付策略、初始化、渲染、一致性检查、推荐写作流程、渲染器版本敏感性说明。 |
| `docs/safety-review.md` | 安全审计报告：审查范围、结论摘要、逐类风险（命令注入/路径遍历/SQL 注入/XSS/CSRF/敏感信息/依赖/竞态/网络）、检查清单与建议。审计基线变更时更新基线 commit 与日期。 |

### 4.2 内容一致性要求

- API 表格中的每个 endpoint 必须能在 `main.py` 找到对应路由函数；schema 字段必须能在 `schemas.py` 找到定义。
- 环境变量表的每一项必须能在 `config.py` 找到字段，默认值逐字一致。
- 性能与稳定性相关的数字（重试上限、停滞阈值、chunk 大小、并发默认值、profile 链顺序）必须与 `ytdlp_service.py`、`job_manager.py`、`config.py` 一致；可由环境变量覆盖的，写明变量名。
- 测试命令与用例总数必须与当前实际运行结果一致。
- 同一事实只在一处详述；其他文档用链接引用。

---

## 5. UML 与图片交付物

使用 PlantUML 编写源码，并渲染为 SVG。`.puml` 放在 `docs/diagrams/`，SVG 放在 `docs/assets/diagrams/`，两者都必须纳入 Git。

统一使用仓库内工具链，**不要要求开发者手工安装全局 `plantuml` CLI 或自行寻找 jar**：

```powershell
python scripts\docs.py bootstrap   # 初始化并校验固定版本 PlantUML
python scripts\docs.py render      # 渲染全部 puml 到 SVG
python scripts\docs.py check       # 校验本地链接 + SVG 与源一致
```

### 5.1 必须存在的图

| 源码 | 用途 |
| --- | --- |
| `system-context.puml` | 系统上下文/容器视图：用户、浏览器、FastAPI、SQLite、yt-dlp、ffmpeg、YouTube。 |
| `component-overview.puml` | 前后端主要组件与模块边界。 |
| `download-lifecycle.puml` | 任务与子项生命周期状态图。 |
| `single-video-sequence.puml` | 单视频解析、建任务、下载、进度回传时序。 |
| `playlist-sequence.puml` | 合集选条目、按并发并行下载、聚合状态时序。 |
| `cookies-flow.puml` | cookies 上传/浏览器导入/锁库/CDP fallback 流程。 |
| `data-model.puml` | SQLite 表与 API 读模型关系。 |
| `four-plus-one-logical-view.puml` | 4+1 逻辑视图。 |
| `four-plus-one-development-view.puml` | 4+1 开发视图。 |
| `four-plus-one-process-view.puml` | 4+1 进程视图。 |
| `four-plus-one-physical-view.puml` | 4+1 物理视图。 |
| `four-plus-one-scenario-view.puml` | 4+1 场景视图。 |
| `module-dependencies.puml` | 后端模块依赖方向与分层规则（设计文档使用）。 |
| `download-data-flow.puml` | 下载主数据流：请求 → 队列 → yt-dlp → 进度聚合 → 落库 → SSE → 读模型。 |
| `runtime-concurrency.puml` | 事件循环/worker 线程/`asyncio.to_thread`/SQLite session 的运行时并发模型。 |

### 5.2 图的使用规则

- 每张图都必须服务于某份文档中的具体说明，不生成装饰性图。
- 在相关 Markdown 中嵌入 SVG，并在图附近链接对应 `.puml` 源文件。
- 修改架构、模块边界或运行时流程后，同步更新受影响的图。
- **注意渲染器版本敏感性**：SVG 由本机 Java + Graphviz + 固定版本 PlantUML 生成，Graphviz 版本差异会导致布局字节不同；`docs.py check` 在渲染器版本变更后会报「需要重新渲染」。这是预期行为，处理方式是重新 `render` 并提交 SVG，同时在 `documentation-workflow.md` 中记录本机渲染基线。

---

## 6. README 收敛要求

重写根目录 `README.md`，只保留：

- 项目一句话定位与合规提醒。
- 最短可用的快速启动命令（构建前端 + 启动单端口后端）。
- 首页截图与「哪个端口做什么」的一句话区分。
- 文档导航表，链接到 `docs/index.md` 与各关键文档。
- 测试命令入口（链接到 `docs/testing.md`，不重复展开）。
- 维护提示：功能、命令、配置、API、架构或测试方式变化时必须同步更新 `docs/`。

详细 API、cookies、排障、配置解释、架构和实现说明不得回流到 README。

---

## 7. 质量标准与验证

文档必须与当前代码一致，特别是 endpoint、schema 字段、环境变量、默认值、任务状态、下载策略、错误文案分类和测试命令。

提交前必须运行并通过：

```powershell
python -m compileall backend\app
python -m pytest backend\tests -q
cd frontend && npm test && npm run build
python scripts\docs.py check
git diff --check
```

补充要求：

- `python scripts\docs.py check` 必须报告「文档检查通过：本地链接有效，UML SVG 与 PlantUML 源一致」；因渲染器版本导致的过期 SVG 需重新渲染后提交。
- `git diff --check` 无输出（无行尾空白、无冲突标记）。
- OpenAPI spec 必须能被 YAML 解析，且 endpoint/字段与 `main.py`、`schemas.py` 一致；如果能通过标准校验工具（如 `openapi-spec-validator`）则一并运行。
- 若某项验证因环境原因无法执行（例如无网络访问真实 YouTube），必须在文档与最终回复中明确说明**未执行**的原因，不得声称已通过。

---

## 8. 提交与推送要求

按阶段拆分提交，每个阶段一个或多个语义化 commit，并**每次推送**：

```powershell
git status -sb          # 确认变更范围仅限文档与文档产物
git add <files>
git commit -m "<type>: <scope>"
git push origin main
git ls-remote origin main   # 校验远端 sha 与本地一致
```

推送前自查：

- 变更只涉及 `README.md`、`docs/**`、`ai/**`、`scripts/docs.py`（如涉及）。
- 没有把 `data/`、`downloads/`、`.tools/`、`frontend/dist/`、`node_modules/` 等产物加入暂存区。
- 没有提交 cookies、token、visitor data 等敏感信息或个人联系信息。
- `.puml` 与 `.svg` 成对提交。

---

## 9. 输出要求

请直接修改仓库文件，不要只给建议。完成后在最终回复中简要说明：

- 已创建/更新的主要文档与图，以及每份文档承担的新增职责。
- PlantUML 图是否成功渲染（张数、是否有重新渲染的图）。
- OpenAPI spec 的位置、覆盖的 endpoint 数量与校验方式。
- 已运行的验证命令及结果（含用例数）。
- 每个阶段的 commit 与 push 结果。
- 尚未完成或因环境限制无法验证的事项。

---

## 10. 分阶段执行清单（当前基线）

> 该清单描述本次任务的阶段划分，便于后续以同样结构复用本 prompt。执行时必须重新核对基线，不要假定它仍然成立。

1. **阶段一：更新本文件**。按仓库现状修订输入来源清单、交付物清单、UML 清单与质量门槛，提交并推送。
2. **阶段二：按本文件全面更新 `docs/`**。修正过期内容、补齐缺失章节、更新受影响的图，提交并推送。
3. **阶段三：架构与设计文档**。新增 `docs/design.md` 与 `module-dependencies.puml`、`download-data-flow.puml`、`runtime-concurrency.puml`，说明核心模块职责与数据流，提交并推送。
4. **阶段四：OpenAPI v3 spec**。生成 `docs/openapi.yaml`，覆盖全部接口、完整描述与示例，并从 `docs/api.md`、`docs/index.md` 链接过去，提交并推送。
