# YouTube Downloader（cascade）工程文档撰写 Prompt

你是一名资深软件工程技术写作者、系统分析师和文档架构师。请基于当前仓库的**真实代码**、`README.md`、`ai/perf/PLAN.md`、后端 API schema、前端类型定义、测试用例、修复记录和项目配置，持续维护 YouTube Downloader 项目的软件工程标准文档体系。

本文件是**可重复执行的任务说明**，不是一次性指令：每次文档任务都应从「输入来源」重新取证，而不是沿用本文件的历史描述。仓库演进后，本文件本身也必须同步修订。

---

## 1. 总目标

在不修改任何产品或测试源码的前提下，维护 `docs/` 下的专业文档体系，并把根目录 `README.md` 收敛为简短入口页。文档要服务四类读者：

- **普通用户**：如何安装、运行、配置、使用和排障。
- **开发者**：如何理解架构、模块边界、API、数据模型、线程/事件模型、测试和维护约定。
- **后续维护者/审查者**：如何判断文档是否与代码一致，如何在功能变化时同步更新文档与图。
- **排障者**：拿着一句原始报错，如何按文档定位到根因与下一步。

---

## 2. 必须遵守的约束

- **只改文档与文档产物**：允许修改 `README.md`、`docs/**`、`scripts/docs.py` 的文档相关行为，以及 `ai/` 下的任务与记录文件；不得修改 `backend/app/**`、`frontend/src/**`、`backend/tests/**` 等产品与测试源码。
- **一切以仓库事实为准**：不臆测不存在的功能、接口、配置或部署方式。每个数字（用例数、默认值、阈值、端口、并发数）必须能在仓库中找到出处；找不到出处的数字不许写。
- **README 只做入口页**：保留一句话定位、合规提醒、快速启动、端口说明、文档导航表、测试命令入口和维护提示；详细内容一律链接到 `docs/`。
- **禁止重复叙述**：各文档之间不得复制大段相同说明；复用一律用相对链接交叉引用（含锚点）。
- **引用必须本地可跳转**：引用源文件、文档、代码行、类名、类型和命令时使用相对路径与行锚，例如 `../backend/app/main.py#L98`、`../frontend/src/api.ts#L24`、`implementation.md#任务调度`。行锚由 `scripts/check_doc_anchors.py` 校验，`--fix` 可自动重算能唯一确定符号的那些。
- **区分「已核验」与「推断」**：凡结论依赖实验或数据的，注明取证方式与时间基线；不确定处显式标注，不得用「应该」「可能是」蒙混。
- **合规边界**：涉及 YouTube、cookies、PO token、下载稳定性、版权或权限限制时保持合规与边界清晰：不承诺绕过 DRM、会员、地区、年龄、私有视频等权限限制，不把「规避风控」写成产品目标。
- **风险项必须写明代价**：`throttledratelimit`、aria2c 多连接等会影响 403 率的开关，必须在文档中同时给出「收益」与「代价」，并说明默认值与回退方式。
- **只报「失败」不给下一步的提示视为未完成**：新增失败路径必须带可执行的下一步（API 侧是 `next_steps`，界面侧是「点哪里」）。
- **未处理事项必须回流**：文档里承认的限制，要能在 `ai/bug-fix/README.md` 或 `ai/ui/README.md` 的「已知但未处理」清单里找到对应条目；反之亦然，两边不允许各说各话。功能类缺陷与界面类缺陷分别归到这两个目录（有报错/失败用例 → bug-fix；能跑但呈现不对 → ui）。

---

## 3. 输入来源（每次任务至少核查一遍）

### 3.1 任务、计划与修复记录

| 文件 | 用途 |
| --- | --- |
| `ai/docs/docs.md` | 文档任务的原始需求（软件工程规范、UML、可跳转引用、README 收敛、持续同步）。 |
| `ai/docs/docs-prompt.md` | 本文件，文档任务的可重复说明书。 |
| `ai/perf/PLAN.md` | 下载性能与稳定性修复计划的根因分析、修复项、验证与回滚；文档中引用性能相关结论时以此为权威来源。 |
| `ai/bug-fix/README.md` | 功能类修复记录索引与「已知但未处理」清单（当前 12 条）。**文档中的限制、遗留风险与「尚未验证」声明必须与此处一致。** |
| `ai/bug-fix/NNN-*.md` | 逐条修复记录（问题 / 原因 / 修复方案 / 效果 / 验证 / 风险与回滚 / 关联）。文档里写「为什么这么设计」时优先引用这些记录而不是重新推导。 |
| `ai/ui/README.md` | **界面缺陷记录**（能跑、不报错，但呈现错位或不一致）的索引与约定，含「这类缺陷的自动化覆盖边界」一节：jsdom 不做布局，布局类问题只能靠样式源文不变式 + 真实浏览器实测。 |
| `ai/ui/NNN-*.md` | 逐条界面缺陷记录，与 bug-fix 同格式，但**必须给出可测量的像素证据**与截图。文档里解释「某处样式为什么这么写」时优先引用这些记录。 |
| `ai/plan.md`、`ai/tasks.md`、`ai/brainstorming.md` | 历史计划，用于确认设计意图。 |
| `ai/refactor/refactor.md` | **重构总纲**：目标与非目标、判定准则、基线快照、路线图与逐轮索引。文档里解释「某处结构为什么这么分」时优先引用它。 |
| `ai/refactor/NNN-*.md` | 逐轮重构记录（计划 / 实施方案 / 实施情况 / 验证 / 未覆盖），带明确时间戳。 |

### 3.2 后端（`backend/app/`，`app/*.py` 34 个 + `app/routers/` 8 个）

分层含义：L0 入口 / L1 契约 / L2 编排 / L3 领域 / L4 基础设施 / L5 纯工具。依赖只向下，L3 不得反向依赖 L2 或 L0。

| 模块 | 层 | 关注点 |
| --- | --- | --- |
| `main.py` | L0 | **只做装配**：建 `ApiContext`、挂载 `API_ROUTERS`、静态资源托管、lifespan；仅保留 1 条条件注册的 `GET /`。 |
| `routers/*` | L0 | 按资源分模块声明 27 条路由（`diagnostics` / `cookies` / `analyze` / `jobs` / `job_files` / `settings` / `events`），`__init__.py` 的 `API_ROUTERS` 只声明挂载顺序。 |
| `api_context.py` | L0 | `ApiContext`（`frozen` dataclass）+ `get_context` / `get_session` + `ContextDep` / `SessionDep`。 |
| `api_support.py` | L0 | 路由支撑：任务/条目定位与 404、设置读写与响应构造、cookies 元数据提取、目录选择。 |
| `job_artifacts.py` | L0 | 产物定位一条链（`output_file` / `item_folder` / `output_folder` / `job_folder`）与本机打开。 |
| `__main__.py` | L0 | 本地启动入口：命令行解析、端口预检与占用者识别、`--port` / `--auto-port` / `--reload` / `--host`。 |
| `config.py` | L1 | `AppSettings` 全部字段、`YTDL_` 前缀、默认值、约束、仓库根 `.env`（绝对路径）。 |
| `schemas.py` | L1 | 全部请求/响应模型与 `Literal` 枚举，是 API 文档与 OpenAPI 的唯一真源。 |
| `models.py` | L1 | `Job` / `JobItem` / `JobEvent` / `Setting` 表结构与 `JobStatus`。 |
| `job_manager.py` | L2 | 队列、worker、并发、暂停/重启/删除、进度 hook、错误分类、终态聚合、cookies 刷新重试；**降级只消费判定结果**，不做判定。 |
| `job_read_model.py` | L2 | 读模型投影：聚合分辨率/格式、`elapsed_seconds`、降级消息。 |
| `events.py` | L2 | SSE 事件代理（每订阅者一个队列）。 |
| `ytdlp_service.py` | L3 | yt-dlp 边界层：元数据解析、预检测、下载参数、profile 重试链、依赖诊断、错误分类。 |
| `ytdlp_formats.py` | L3 | 格式选择器、分辨率/格式/大小提取、降级候选计算、720p 底线。 |
| `stall_guard.py` | L3 | 停滞看门狗与 `DownloadStalled` 语义。 |
| `fallback_policy.py` | L3 | 降级原因常量与用户可读消息、重启建议（不做决策）。 |
| `resolution_decisions.py` | L3 | 降级**决策**（纯函数）：值不值得找降级候选、降到哪、为什么、降不了时报哪句话。 |
| `download_progress.py` | L3 | 多子流进度聚合与分母策略。 |
| `progress_persist.py` | L3 | 进度落库节流门（首次/终态/250ms/0.5%）。 |
| `transfer_stats.py` | L3 | 平均速度计算。 |
| `browser_cookies.py` | L3 | 浏览器 cookies 导入、域名过滤、锁库与 CDP fallback。 |
| `cookie_health.py` | L3 | cookies 体检：格式/域名/鉴权项/过期 + `LOGGED_IN` 联网探针合成结论。 |
| `proxy.py` | L3 | 代理**解析**：显式 > 系统 > 环境变量，归一化与凭据脱敏。 |
| `connectivity.py` | L3 | 代理**验证**：一次朴素 HTTPS 探针，返回状态码/耗时/字节数/原始异常。 |
| `error_advice.py` | L3 | 异常链 → 可执行诊断（JS challenge / cookies / 代理 / 媒体流四类）。 |
| `output_paths.py` | L3 | 输出文件/中间文件/sidecar 路径解析与发现。 |
| `system_open.py` | L3 | 本地播放器选择与文件夹打开。 |
| `db.py` | L4 | engine 创建、SQLite pragma（WAL、`busy_timeout=5000`、`synchronous=NORMAL`）、`_ensure_columns` 补列、`checkpoint_wal`。 |
| `logging_setup.py` | L4 | root/uvicorn logger 配置、落盘 `data/logs/app.log` 与轮转、`YTDL_LOG_LEVEL`。 |
| `dev_server.py` | L5 | 端口可用性探测、向后找可用端口、`netstat` / `tasklist` 输出解析。 |
| `runtime_env.py` | L5 | 摘除会打坏 JS 运行时的宿主环境变量（当前覆盖 `NODE_OPTIONS`），返回可留痕记录。 |
| `paths.py` | L5 | 路径名安全化。 |
| `log_safety.py` | L5 | 日志敏感信息清洗。 |

### 3.3 前端（`frontend/src/`）

- `App.tsx`：**只做状态编排与布局** —— SSE 订阅、解析/建任务/设置保存流程、cookies 交互。
- `components/`：
  - `UrlAnalyzer.tsx`：解析面板（链接输入、cookies 行、锁库提示、解析按钮）。
  - `AnalysisPanel.tsx`：解析结果（缩略图/标题/时长、playlist 勾选表、单视频汇总）。
  - `DownloadOptionsPanel.tsx`：下载选项（模式、清晰度、字幕、开关项、限速与重试、提交）。
  - `SettingsPanel.tsx`：设置（下载目录、并发、单视频并发下载数（aria2c）、代理；失焦即保存；单视频并发下载数旁挂 `Aria2cHelpPopover`，说明 aria2c 的查询与启用）。
  - `SearchableLanguageSelect.tsx` / `Toggle.tsx` / `StatusPill.tsx`：基础件。
  - `CookieSection.tsx`：cookies 状态、校验按钮与结论展示。
  - `ProxySection.tsx`：代理检测结果与「真的发一次请求」的入口。
  - `HelpPopover.tsx`：**通用非模态说明浮层**。默认收起；桌面端悬停即看、点击钉住；`≤640px` 或无 hover 能力时渲染为贴底抽屉；`Esc` / 点外部 / 右上角 × 关闭；不 `autoFocus`、无遮罩、不抢输入框焦点；钉住状态写 `localStorage`（键前缀 `cascade.help.open.v1.`），关闭同时清除记忆。
  - `JobQueue.tsx`：任务中心展示与本地文件操作入口。
- `api.ts`：HTTP 边界与 `ApiError` 语义。`types.ts`：与后端 schema 逐字段对应的前端类型。
- 纯模块：`formatting.ts`（展示格式化）、`quality.ts`（清晰度选项与标签）、`subtitles.ts`（字幕文案与来源归一）、`cookieLock.ts`（`browser_locked` 错误 → 界面状态）。
- `quality.ts`、`formatting.ts`：清晰度选项/降级按钮文案、格式化工具。
- `styles.css`：设计 token 与浮层/抽屉样式（浮层内必须显式重置 `white-space` 与字重，否则会继承宿主元素）。**共用基类不得声明只为某一处用法需要的版式**（宽度、外边距）：基类若声明在变体之后，会以同优先级把变体的重置静默吃掉，见 `ai/ui/001`。同理，**固定宽度的网格轨道里，文本必须能断行**：右栏是写死的 `390px`（内容区 352px），一个不能断行的长 token（Windows 路径最典型）就足以让面板的 min-content 超过轨道并撑出整页横向滚动条；修法是 `overflow-wrap: anywhere`（**不能**用 `break-word`，后者不改变 min-content），见 `ai/ui/002`。再加一条：**会随数据变长的标签不得被 `nowrap` 锁成一行** —— `nowrap` 之下 `overflow-wrap` 与 `min-width: 0` 都无效，而 `text-overflow: ellipsis` 只有在元素真的被约束时才会出现（先确认 `scrollWidth > clientWidth` 成立），否则「省空间」会静默变成「撑版面」，见 `ai/ui/003`。
- `vite-env.d.ts`：`vite/client` 类型引用，供测试用 `?raw` 读取样式源文。
- `frontend/src/*.test.tsx`（11 个文件，按功能面拆分）、`test/appHarness.ts`、`test/appFixtures.ts`、`test/cssRules.ts`、`test/setup.ts`：前端测试范围与共享件。测试台 `appHarness.ts` 是**唯一**的后端 `fetch` 替身与前后置钩子来源；`styles.test.ts` 有一条**样式源文不变式**（读 `styles.css?raw`），因为 jsdom 不做布局，布局类缺陷只有这一部分能进单测。每个测试文件都必须能单独跑绿（见 `ai/refactor/008`）。

### 3.4 配置、脚本与测试

- `backend/pyproject.toml`、`frontend/package.json`：依赖、脚本、测试配置。
- `backend/tests/`：22 个 `test_*.py` + `fakes.py`（当前 **354 passed**）。
- `frontend/src/*.test.tsx` + `src/styles.test.ts`：11 个测试文件，当前 **69 passed**（`npx vitest run --environment jsdom`）。
- `scripts/`：
  - `docs.py`：文档工具链（`bootstrap` / `render` / `check`），固定版本 PlantUML。
  - `check_doc_anchors.py`：代码行锚点漂移检查（`--fix` 自动重算）。
  - `check_layers.py`：**分层依赖校验**——解析 `docs/design.md` 的模块职责矩阵，与 `backend/app` 实际模块集合对比，并检查 import 方向只向下。
  - `check_api_contract.py`：**契约漂移校验**——以运行时 `app.openapi()` 为单一来源，比对 `docs/openapi.yaml` 与 `frontend/src/types.ts`。
  - `bench_concurrency.py`、`bench_throttle_guard.py`：可离线复现的并发与节流基准。
  - `acceptance_network.py`、`acceptance_real.py`：需要真实网络的验收脚本（本机常因环境无法执行，属「未验证」事项）。
  - `export_cookies_via_cdp.py`：独立配置的有头浏览器导出 cookies（有域名校验，失败退出码 2）。
- `.gitignore`、`.editorconfig`：哪些产物不入库、写作格式约定（UTF-8 / LF / 去行尾空格）。
- `.github/workflows/ci.yml`：CI 门槛（`pytest` → `check_layers.py` → `check_api_contract.py` → `check_doc_anchors.py`，以及前端 `vitest` / `tsc` / `build`）。**不跑** `docs.py check`（UML 一致性依赖本机 Java + PlantUML + Graphviz 工具链），本地与 CI 的差异见 `docs/development.md`。

> 每次任务请用 `git log --oneline` 与 `git status -sb` 确认当前基线，并在文档中的性能/行为结论处标注对应的计划、提交或修复记录编号。

---

## 4. 交付物

文档位于 `docs/`，每份文档必须包含：清晰标题、适用读者、相关文档链接、必要的源码引用。

### 4.1 核心文档

| 文档 | 职责 |
| --- | --- |
| `docs/index.md` | 文档总入口：按读者角色给出阅读路径、文档职责清单、UML 索引。 |
| `docs/user-manual.md` | 用户手册：启动、解析、选项、任务中心、cookies、代理与网络、下载目录与产物、辅助说明的查看方式、排障入口。 |
| `docs/troubleshooting.md` | 排障手册：按「你看到的那句话」（原始报错原文）组织的症状驱动处置，含代理、cookies、JS 运行时三篇、端口占用、「重启服务后页面还是旧样子」，以及日志阅读方法。 |
| `docs/requirements.md` | 需求分析：目标、用户角色、功能需求（带代码依据）、非功能需求、约束边界、验收口径。 |
| `docs/architecture.md` | 架构设计：系统上下文、容器职责、组件关系、关键数据流、状态生命周期、数据模型、设计取舍。 |
| `docs/4-plus-1-view.md` | Kruchten 4+1 视图审查入口：逻辑/开发/进程/物理/场景视图与持续更新规则。 |
| `docs/design.md` | 设计文档：核心模块职责矩阵（含分层）、依赖方向与分层规则、线程与事件循环模型、关键数据流、并发与持久化设计取舍、扩展点与已知限制。 |
| `docs/development.md` | 开发文档：技术栈、环境要求、依赖安装、运行模式、目录结构、**完整环境变量表（默认值必须与 `config.py` 逐字一致）**、文档工具链、开发检查。 |
| `docs/api.md` | API 文档：endpoint 全表、请求/响应模型、错误语义与状态码、任务状态、诊断字段，并链接 OpenAPI spec。 |
| `docs/openapi.yaml` | OpenAPI 3.1 spec：全部 endpoint、参数、请求体、响应、错误响应与可运行示例。 |
| `docs/technical.md` | 技术文档：清晰度/格式选择、预检测、字幕来源、降级原因、稳定下载策略（含节流守卫、停滞看门狗、aria2c）、cookies、PO token、失败排查顺序、文件删除语义。 |
| `docs/implementation.md` | 实现文档：后端入口、数据模型、任务调度、yt-dlp 封装、进度与平均速度、读模型、前端实现、日志安全与落盘。 |
| `docs/testing.md` | 测试文档：自动测试命令、后端/前端测试范围、手动验收步骤、高风险回归点、文档验收。 |
| `docs/maintenance.md` | 维护文档：文档同步原则、变更 checklist、排障流程、UML 更新流程、提交流程、审查重点。 |
| `docs/documentation-workflow.md` | 文档写作与生成环境：工具链交付策略、初始化、渲染、一致性检查、推荐写作流程、渲染器版本敏感性说明（含本机渲染基线表）。 |
| `docs/safety-review.md` | 安全审计报告：审查范围、结论摘要、逐类风险（命令注入/路径遍历/SQL 注入/XSS/CSRF/敏感信息/依赖/竞态/网络）、检查清单与建议。审计基线变更时更新基线 commit 与日期。 |

### 4.2 截图产物

界面截图放 `docs/assets/screenshots/`，由真实浏览器（headless + CDP）实拍后随文档提交。当前四张：`home.png`（首页）、`help-collapsed.png`、`help-open-desktop.png`、`help-open-mobile.png`（辅助说明的三种状态）。

规则：

- 截图必须与当前界面一致；界面改版后必须重拍并替换。
- **截图不得带本机个人信息**：桌面端展开图要裁掉含用户名的绝对路径区域。
- 文档引用截图时给出一句「这张图说明什么」，不要只放图。

### 4.3 内容一致性要求

- API 表格中的每个 endpoint 必须能在 `main.py` 找到对应路由函数；schema 字段必须能在 `schemas.py` 找到定义。
- 环境变量表的每一项必须能在 `config.py` 找到字段，默认值逐字一致；表头若写「列出全部字段」，就不能漏项。
- 性能与稳定性相关的数字（重试上限、停滞阈值、chunk 大小、并发默认值、profile 链顺序）必须与 `ytdlp_service.py`、`job_manager.py`、`config.py` 一致；可由环境变量覆盖的，写明变量名。
- 测试命令与用例总数必须与当前实际运行结果一致（当前后端 354、前端 69）。
- 前端组件、后端模块的增减必须反映到 `design.md` 的模块矩阵、`implementation.md` 的对应章节，以及受影响的 UML 图。
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

### 5.1 必须存在的图（当前 15 张）

| 源码 | 用途 |
| --- | --- |
| `system-context.puml` | 系统上下文/容器视图：用户、浏览器、FastAPI、SQLite、yt-dlp、ffmpeg、YouTube。 |
| `component-overview.puml` | 前后端主要组件与模块边界（含前端 11 个组件与后端 L0~L5 分层分组）。 |
| `download-lifecycle.puml` | 任务与子项生命周期状态图。 |
| `single-video-sequence.puml` | 单视频解析、建任务、下载、进度回传时序。 |
| `playlist-sequence.puml` | 合集选条目、按并发并行下载、聚合状态时序。 |
| `cookies-flow.puml` | cookies 上传/浏览器导入/锁库/CDP fallback 流程。 |
| `data-model.puml` | SQLite 表与 API 读模型关系。 |
| `four-plus-one-logical-view.puml` | 4+1 逻辑视图。 |
| `four-plus-one-development-view.puml` | 4+1 开发视图（源码模块与测试边界）。 |
| `four-plus-one-process-view.puml` | 4+1 进程视图。 |
| `four-plus-one-physical-view.puml` | 4+1 物理视图。 |
| `four-plus-one-scenario-view.puml` | 4+1 场景视图。 |
| `module-dependencies.puml` | 后端模块依赖方向与分层规则（设计文档使用）。 |
| `download-data-flow.puml` | 下载主数据流：请求 → 队列 → yt-dlp → 进度聚合 → 落库 → SSE → 读模型。 |
| `runtime-concurrency.puml` | 事件循环/worker 线程/`asyncio.to_thread`/SQLite session 的运行时并发模型。 |

新增图必须与上表同步登记到 `docs/index.md` 的图索引。

### 5.2 图的使用规则

- 每张图都必须服务于某份文档中的具体说明，不生成装饰性图。
- 在相关 Markdown 中嵌入 SVG，并在图附近链接对应 `.puml` 源文件。
- 修改架构、模块边界、前端组件构成或运行时流程后，同步更新受影响的图（`component-overview` 与 `four-plus-one-development-view` 最容易因新增组件而过期）。
- **注意渲染器版本敏感性**：SVG 由本机 Java + Graphviz + 固定版本 PlantUML 生成，Graphviz 版本差异会导致布局字节不同；`docs.py check` 在渲染器版本变更后会报「需要重新渲染」。这是预期行为，处理方式是重新 `render` 并提交 SVG，同时在 `documentation-workflow.md` 中记录本机渲染基线。

---

## 6. README 收敛要求

根目录 `README.md` 只保留：

- 项目一句话定位与合规提醒。
- 最短可用的快速启动命令（构建前端 + 启动单端口后端）。
- 首页截图与「哪个端口做什么」的一句话区分。
- 端口来源说明（`YTDL_API_PORT` 是唯一来源，前后端读同一个 `.env`）。
- 文档导航表，链接到 `docs/index.md` 与各关键文档。
- 测试命令入口（链接到 `docs/testing.md`，不重复展开）。
- 维护提示：功能、命令、配置、API、架构或测试方式变化时必须同步更新 `docs/`。

详细 API、cookies、排障、配置解释、架构和实现说明不得回流到 README。

---

## 7. 质量标准与验证

文档必须与当前代码一致，特别是 endpoint、schema 字段、环境变量、默认值、任务状态、下载策略、错误文案分类、前端组件构成和测试命令。

提交前必须运行并通过（Windows 用仓库根 `.venv\Scripts\python.exe`，它装有 `sqlmodel` / `yt_dlp` 等后端依赖；系统 Python 或托管 venv 缺包会导致收集失败，那是环境问题不是代码问题）：

```powershell
python -m compileall backend\app
python -m pytest backend\tests -q                 # 当前基线 354 passed
cd frontend; npx vitest run --environment jsdom   # 当前基线 69 passed
cd frontend; npx tsc --noEmit                     # 类型检查
python scripts\docs.py check                      # 本地链接 + UML 产物一致
python scripts\check_doc_anchors.py               # 代码行锚点无漂移
python scripts\check_layers.py                    # 分层依赖方向 + 模块矩阵登记
python scripts\check_api_contract.py              # 运行时契约与 openapi.yaml / types.ts 的漂移
git diff --check                                  # 无行尾空白与冲突标记
```

补充要求：

- `python scripts\docs.py check` 必须报告「文档检查通过：本地链接有效，UML SVG 与 PlantUML 源一致」；因渲染器版本导致的过期 SVG 需重新渲染后提交。
- `python scripts\check_doc_anchors.py` 不得有漂移项。输出里的「待人工复核」是「标签不是符号名」的正常项，不是漂移。
- OpenAPI spec 必须能被 YAML 解析，且 endpoint/字段与 `main.py`、`schemas.py` 一致；能通过 `openapi-spec-validator` 则一并运行。
- 若某项验证因环境原因无法执行（例如需要真实 YouTube 访问的 `scripts/acceptance_real.py`），必须在文档与最终回复中明确说明**未执行**的原因，不得声称已通过。
- 涉及界面改动时，优先用真实浏览器（headless + CDP）探针验证交互断言，而不是靠读代码推断。

---

## 8. 提交与推送要求

按阶段拆分提交，每个阶段一个或多个语义化 commit，并**每次推送**：

```powershell
git status -sb          # 确认变更范围仅限文档与文档产物
git add <files>
git commit -m "<type>(<scope>): <subject>"
git push origin main
git ls-remote origin main   # 校验远端 sha 与本地一致
```

推送前自查：

- 变更只涉及 `README.md`、`docs/**`、`ai/**`、`scripts/docs.py`（如涉及）。
- 没有把 `data/`、`downloads/`、`.tools/`、`frontend/dist/`、`node_modules/`、`tmp_acceptance/` 等产物加入暂存区。
- 没有提交 cookies、token、visitor data、PO token 等敏感信息或个人联系信息；截图里也不含本机用户名路径。
- `.puml` 与 `.svg` 成对提交。
- 工作区若存在**与本次任务无关**的在途改动，提交时必须显式列出文件，不要把别人的改动吞进自己的 commit。

---

## 9. 输出要求

请直接修改仓库文件，不要只给建议。完成后在最终回复中简要说明：

- 已创建/更新的主要文档与图，以及每份文档承担的新增职责。
- PlantUML 图是否成功渲染（张数、是否有重新渲染的图）。
- OpenAPI spec 的位置、覆盖的操作数量与校验方式。
- 已运行的验证命令及结果（含用例数）。
- 每个阶段的 commit 与 push 结果（`ls-remote` 校验值）。
- 尚未完成或因环境限制无法验证的事项。

---

## 10. 分阶段执行清单（可复用）

> 该清单是**每一轮文档任务的标准流程**，不是一次性计划。执行时必须重新核对基线，不要假定上次记录的仍然成立。

1. **阶段一：更新本文件**。按仓库现状修订输入来源清单、交付物清单、UML 清单、验证命令与质量门槛，提交并推送。
2. **阶段二：取证与差异定位**。跑一遍第 7 节的验证命令，列出「文档与代码不一致」的清单（断链、过期默认值、缺失模块/组件、漂移的行锚、过期的图），再动手改。
3. **阶段三：按差异清单更新 `docs/`**。先修断链与锚点，再补缺失章节（新增模块、新增组件、新增端点、新增环境变量），最后统一术语与文案语气。提交并推送。
4. **阶段四：图与 4+1 视图**。模块边界、组件构成或运行时流程变化后更新对应 `.puml`，`render` 后提交 SVG，并同步 `docs/index.md` 的图索引与 `4-plus-1-view.md`。提交并推送。
5. **阶段五：README 与全量回归**。确认 README 仍是入口页（没有详细内容回流），跑完整验证并报告结果。提交并推送。

---

## 11. 当前基线快照（2026-10-02，自 commit `18537e8` 起；本轮已同步三轮重构 R1~R3 与后续六个候选 R4~R9 之后的模块、测试与记录基线）

每次执行任务时更新本节；它只用于快速判断「哪些数字变了」，不作为事实来源。
不改本节的 commit 值——写了就必然自我指涉，反查请用
`git log --oneline -- ai/docs/docs-prompt.md`。

| 项 | 值 |
| --- | --- |
| 后端模块 | 34 个 `.py`（`app/*.py` 34 个，另有 `app/routers/` 8 个） |
| 后端测试 | 22 个 `test_*.py` + `fakes.py`，**354 passed** |
| 前端测试 | `frontend/src/*.test.tsx` + `src/styles.test.ts`（11 个文件）+ `src/test/` 三个共享件，**69 passed** |
| HTTP 操作 | 28 个（`openapi.yaml` 的 `operationId` 数：26 个 `/api/*` + `/health` + 静态首页 `/`） |
| 环境变量 | `YTDL_` 前缀，`AppSettings` 共 22 个字段（另有 `YTDL_LOG_LEVEL` 不属于 `AppSettings`） |
| UML 图 | 15 张（`.puml` 与 `.svg` 成对） |
| 截图 | 4 张 |
| 修复记录 | `ai/bug-fix/` 001~010（功能类），已知未处理 12 条 |
| 界面缺陷记录 | `ai/ui/` 001（布局对齐）、002（固定轨道被内容撑破）、003（会随数据变长的标签被 `nowrap` 锁成一行 → 顶宽整页）；记录插图 1 张（`ai/ui/assets/`）；已知未处理 1 条（解析后可能残留一个界面上摘不掉的语言代码）；目录约定见 `ai/ui/README.md` |
| 重构记录 | `ai/refactor/` 001~009（路线图 R1~R3 + 后续候选 R4~R9，六项候选已全部结项），索引与判定准则见 `ai/refactor/refactor.md` |
| 本机渲染基线 | PlantUML `1.2026.5`、Java 25.0.3、Graphviz 15.1.1 |
