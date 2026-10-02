# 设计文档

适用读者：需要判断"这段逻辑该放哪一层""改动会不会破坏运行时假设"的开发者与维护者。

本页是 [架构设计](architecture.md) 的下一层：架构页回答"系统由什么组成、数据怎么流"，本页回答"模块怎么划分、依赖往哪个方向走、并发和持久化如何保证一致"。HTTP 字段级细节见 [API 文档](api.md)，参数与策略细节见 [技术文档](technical.md)。

## 设计目标与约束

| 目标 | 具体表现 | 代价 |
| --- | --- | --- |
| 本机单用户、零运维 | 单个 Python 进程 + 单文件 SQLite + 静态前端，无外部服务、无账号体系。 | 不做多用户隔离、不做水平扩展、不做跨机部署。 |
| 稳定优先 | 节流守卫默认关闭、停滞看门狗兜底、保留断点续传、同清晰度多 profile 重试。 | 单条流被限速时不再靠"换 fresh URL"自愈，只能慢速下完或等待看门狗判失败。 |
| 最小改动、可回退 | 每个行为开关都能用环境变量回退；数据库只做追加式补列。 | 没有正式迁移框架，无法安全删除/改名历史列。 |
| 失败必须可见 | 任何静默卡死最终都要变成任务中心里一条可读原因。 | 需要看门狗、错误分类和文案约束（文案不能含 `timed out` / `reset` / `403` 等会被误分类的词）。 |
| 不暴露 yt-dlp 全量能力 | API 只接受 `DownloadOptions`，不转发任意 yt-dlp 参数。 | 高级用法需要改代码，而不是改请求体。 |

## 模块职责矩阵

每个模块只承担一类职责。"不负责"一列用于判断新代码应该落到哪里。

| 模块 | 层 | 职责 | 不负责 |
| --- | --- | --- | --- |
| [main.py](../backend/app/main.py#L39) | L0 入口 | **只做装配**：建 `ApiContext`、挂载 `routers/` 下的 `APIRouter`、静态资源托管、lifespan 启停。 | 不声明路由（路由在 `routers/`），不写业务规则，不做状态转换，不构造 yt-dlp 参数。 |
| [routers/](../backend/app/routers/__init__.py#L21) | L0 入口 | 按资源分模块声明 HTTP 路由（`diagnostics` / `cookies` / `analyze` / `jobs` / `job_files` / `settings` / `events`）：取参、调辅助、定状态码。 | 不做状态转换，不直接调用 yt-dlp，不写领域规则（产物定位见 `job_artifacts`，设置与错误映射见 `api_support`）。 |
| [api_context.py](../backend/app/api_context.py#L27) | L0 入口 | 请求级依赖容器 `ApiContext`（`frozen` dataclass）+ `get_context` / `get_session` 依赖别名。 | 不装配依赖（装配在 `main.py`），不写业务规则。 |
| [api_support.py](../backend/app/api_support.py#L26) | L0 入口 | 路由支撑：任务/条目定位与 404、设置读写与响应构造、cookies 元数据提取、目录选择。 | 不声明路由，不做状态转换。 |
| [job_artifacts.py](../backend/app/job_artifacts.py#L29) | L0 入口 | 产物**定位**（一条候选链）与本机**打开**（播放 / 打开所在目录）。 | 不删文件（删除带白名单校验，属 `job_manager`），不决定用什么播放器（属 `system_open`）。 |
| [__main__.py](../backend/app/__main__.py#L64) | L0 入口 | 本地启动入口：解析命令行、启动前做端口预检并识别占用者、交给 uvicorn。 | 不装配应用（那是 `main.py`），不写业务规则。 |
| [config.py](../backend/app/config.py#L19) | L1 契约 | 声明全部设置字段、默认值与约束、目录准备。 | 不读数据库（`Setting` 覆盖在 `api_support.py` 里做）。 |
| [schemas.py](../backend/app/schemas.py#L14) | L1 契约 | 定义 HTTP 线上模型与枚举。 | 不引用 yt-dlp 类型，不含业务逻辑。 |
| [models.py](../backend/app/models.py#L27) | L1 契约 | 定义持久化表结构与 `JobStatus`。 | 不做读写编排。 |
| [job_manager.py](../backend/app/job_manager.py#L43) | L2 编排 | 队列、worker、并发、暂停/重启/删除、进度 hook、错误分类、终态收敛、事件发布。 | 不直接调用 yt-dlp（经 `YtDlpService`），不解析 yt-dlp 内部结构（经 formats 工具），**不做降级判定**（判定在 `resolution_decisions`）。 |
| [job_read_model.py](../backend/app/job_read_model.py#L12) | L2 编排 | 把表投影成 API 读模型：聚合分辨率/格式、`elapsed_seconds`、降级消息。 | 不写库、不改状态。 |
| [events.py](../backend/app/events.py#L7) | L2 编排 | 进程内 SSE 扇出（每个订阅者一个队列）。 | 不持久化事件（持久化在 `job_manager`）。 |
| [ytdlp_service.py](../backend/app/ytdlp_service.py#L88) | L3 领域 | yt-dlp 边界：元数据、预检测、参数构建、profile 重试链、错误分类、依赖诊断。 | 不碰数据库，不决定任务状态。 |
| [ytdlp_formats.py](../backend/app/ytdlp_formats.py#L9) | L3 领域 | 格式选择器、分辨率/大小/codec 提取、降级候选计算。 | 不导入 yt-dlp。 |
| [stall_guard.py](../backend/app/stall_guard.py#L32) | L3 领域 | 纯函数式停滞判定与 `DownloadStalled`。 | 不写库、不发布事件。 |
| [fallback_policy.py](../backend/app/fallback_policy.py#L10) | L3 领域 | 降级原因常量与用户可读文案、重启建议。 | 不做降级决策（决策在 `resolution_decisions`）。 |
| [resolution_decisions.py](../backend/app/resolution_decisions.py#L71) | L3 领域 | 降级**决策**：值不值得找降级候选、降到哪、为什么、降不了时报哪句话。纯函数，可单测。 | 不发请求、不读库、不改 `JobItem`；可用清晰度由 `job_manager` 取好传进来。 |
| [download_progress.py](../backend/app/download_progress.py#L21) | L3 领域 | 多子流进度聚合与分母策略。 | 不知道数据库和 SSE 的存在。 |
| [progress_persist.py](../backend/app/progress_persist.py#L5) | L3 领域 | 决定某个进度快照是否值得落库。 | 不执行写库。 |
| [transfer_stats.py](../backend/app/transfer_stats.py#L5) | L3 领域 | 由字节增量累加平均速度。 | 不处理瞬时速度（瞬时速度直接取 payload）。 |
| [browser_cookies.py](../backend/app/browser_cookies.py#L55) | L3 领域 | 浏览器 cookies 提取、域名过滤、锁库与 CDP fallback。 | 不决定何时刷新（决策在 `job_manager`/`main`）。 |
| [cookie_health.py](../backend/app/cookie_health.py#L234) | L3 领域 | cookies **体检**：格式/域名/鉴权项/过期 + 一次 `LOGGED_IN` 探针合成结论。 | 不修改 cookies 文件，不决定解析用哪份（决策在 `main`）。 |
| [proxy.py](../backend/app/proxy.py#L169) | L3 领域 | 代理**解析**：显式 > 系统 > 环境，归一化与凭据脱敏。 | 不验证通不通（那是 `connectivity`），不发请求。 |
| [connectivity.py](../backend/app/connectivity.py#L73) | L3 领域 | 代理**验证**：一次朴素 HTTPS 探针，返回状态码/耗时/原始异常。 | 不解析代理来源，不复用 yt-dlp（避免混淆病因）。 |
| [error_advice.py](../backend/app/error_advice.py#L116) | L3 领域 | 异常链 → 可执行诊断（JS challenge / cookies / 代理 / 媒体流四类）。 | 纯字符串判定，不联网、不读库、不改变重试行为。 |
| [runtime_env.py](../backend/app/runtime_env.py#L58) | L5 纯工具 | 摘除会打坏 JS 运行时的宿主环境变量，并返回可留痕的记录。 | 只动本进程 `os.environ`，不改系统设置。 |
| [dev_server.py](../backend/app/dev_server.py#L44) | L5 纯工具 | 端口可用性探测、向后找可用端口、`netstat` / `tasklist` 输出的解析。 | 不启动服务、不改配置；拿不到占用者信息时返回 `None` 而不是猜一个名字。 |
| [output_paths.py](../backend/app/output_paths.py#L13) | L3 领域 | 输出文件、中间文件与 sidecar 的候选路径解析与发现。 | 不删除文件（删除由 `job_manager` 在受限根目录内执行）。 |
| [safe_delete.py](../backend/app/safe_delete.py#L22) | L3 领域 | 删除产物的**路径安全判定**：允许范围（`DeleteScope`）、某路径是否可删、任务目录是否可收走。纯函数，可单测。 | 不删任何东西（`unlink` / `rmdir` 由 `job_manager` 执行），不枚举候选（枚举在 `output_paths`）。 |
| [system_open.py](../backend/app/system_open.py#L23) | L3 领域 | 选择可解码播放器、打开目录、窗口置前。 | 不校验文件是否存在（调用方先解析路径）。 |
| [db.py](../backend/app/db.py#L27) | L4 基础设施 | engine 创建、SQLite pragma、补列、WAL checkpoint、session 依赖。 | 不知道业务表语义。 |
| [logging_setup.py](../backend/app/logging_setup.py#L48) | L4 基础设施 | 配置 root/uvicorn logger，落盘 `data/logs/app.log` 并轮转。 | 不决定打什么日志，不解析业务语义。 |
| [paths.py](../backend/app/paths.py#L7) / [log_safety.py](../backend/app/log_safety.py#L11) | L5 纯工具 | 文件名安全化、日志敏感信息清洗。 | 无项目内依赖，可单独测试。 |

## 前端组件边界

`App.tsx` 只负责状态编排（解析、建任务、设置回灌、SSE 订阅）与整体布局；展示职责全部在 `components/` 下，
纯计算在 `formatting.ts` / `quality.ts` / `subtitles.ts` / `cookieLock.ts`。判断新代码放哪：**需要 `useState` 或发请求吗？**
需要就留在 `App.tsx`（或抽成 hook），只把 props 变成界面就进 `components/`，只看数据变换就进纯模块。

| 组件 / 模块 | 职责 | 不负责 |
| --- | --- | --- |
| [UrlAnalyzer.tsx](../frontend/src/components/UrlAnalyzer.tsx#L29) | 解析面板：链接输入、cookies 行（状态 + 上传/清除 + 浏览器导入）、锁库提示、解析按钮。只持有「选了哪个浏览器」这类纯界面状态。 | 不发请求（回调交 `App`），不决定用哪份 cookies。 |
| [AnalysisPanel.tsx](../frontend/src/components/AnalysisPanel.tsx#L12) | 解析结果：缩略图/标题/时长、playlist 条目勾选表、单视频汇总行。 | 不持有选中集合（状态归 `App`，提交要用同一份）。 |
| [DownloadOptionsPanel.tsx](../frontend/src/components/DownloadOptionsPanel.tsx#L19) | 下载选项：模式、清晰度、字幕语言/来源/格式、开关项、限速与重试、提交按钮。 | 不持有状态：所有值来自 `options`，改动经 `onOptionChange` 回到 `App`。 |
| [SearchableLanguageSelect.tsx](../frontend/src/components/SearchableLanguageSelect.tsx#L16) | 可搜索的字幕语言多选。标签里 join 的是**语言代码**，项数无上界 —— 不许 `nowrap`，见 [ai/ui/003](../ai/ui/003-language-trigger-label-overflows-the-page.md)。 | 不拉取可用语言（由 `App` 从解析结果里汇总）。 |
| [SettingsPanel.tsx](../frontend/src/components/SettingsPanel.tsx#L18) | 设置：下载目录、并发、aria2c 连接数、代理（含连通性检测）。持有一份 `draft`，**失焦即保存**。 | 不解析代理来源（后端回传 `proxy_source`），不决定代理语义（留空=自动、`direct`=直连由后端定义）。 |
| [Toggle.tsx](../frontend/src/components/Toggle.tsx#L4) / [StatusPill.tsx](../frontend/src/components/StatusPill.tsx#L4) | 基础件：勾选行、顶栏状态点。 | 不含业务判断（`ok === undefined` 一律按警告渲染）。 |
| [CookieSection.tsx](../frontend/src/components/CookieSection.tsx#L121) | cookies 状态、校验按钮与结论展示；两个说明浮层（[获取方式](../frontend/src/components/CookieSection.tsx#L53)、[结论解读](../frontend/src/components/CookieSection.tsx#L96)）就近挂在这里。 | 不决定用哪份 cookies（后端决定），不构造请求体。 |
| [ProxySection.tsx](../frontend/src/components/ProxySection.tsx#L140) | 代理检测结果展示与「真的发一次请求」的入口；[常用端口](../frontend/src/components/ProxySection.tsx#L75)与[排查顺序](../frontend/src/components/ProxySection.tsx#L112)两个说明浮层。 | 不解析代理来源（后端回传 `proxy_source`），不保存设置（保存由 `SettingsPanel` 触发）。 |
| [HelpPopover.tsx](../frontend/src/components/HelpPopover.tsx#L42) | **通用非模态说明浮层**：定位、钉住、关闭、记忆，以及窄屏抽屉形态。所有辅助说明共用它。 | 不含任何业务文案（文案由调用方以 children 传入）。 |
| [JobQueue.tsx](../frontend/src/components/JobQueue.tsx#L16) | 任务中心展示与本地文件操作入口。 | 不直接访问本机文件系统（一律走后端受控打开/删除接口）。 |
| [subtitles.ts](../frontend/src/subtitles.ts#L12) | 字幕文案与来源归一：`formatSubtitleInfo`（那行「字幕：来源 X · 格式 Y」）、`effectiveSubtitleSourceForAnalysis`（提交前把「两者都要」收敛成该视频真有的那一类）。 | 不做请求、不读存储。 |
| [cookieLock.ts](../frontend/src/cookieLock.ts#L20) | 把后端 `browser_locked` 错误映射成界面状态（含 `pendingAnalyzeUrl`，用于「关闭 Edge 并导入」后自动补一次解析）。判据是 `detail.code`，不是文案。 | 不渲染界面（渲染在 `UrlAnalyzer`）。 |

说明浮层的设计约束（这几条是界面改版时最容易破坏的，改之前先读）：

1. **默认收起，且同一时刻最多展开一块**：说明是次要信息，不允许常驻版面。钉住状态写 `localStorage`（键前缀 `cascade.help.open.v1.`），关闭时一并清除记忆 —— 所以刷新后最多只有一块保持展开，见 [rememberOpen](../frontend/src/components/HelpPopover.tsx#L29)。
2. **不抢焦点、不阻塞操作**：面板 `aria-modal=false`、打开时不 `autoFocus`、没有遮罩层，触发按钮在 `mousedown` 时阻止默认行为。这样在点开「常用端口」时，代理输入框里的光标和未保存的输入都不会被打断。
3. **窄屏改形态而不是改内容**：`≤640px` 或设备无 hover 能力时渲染为贴底抽屉（`is-sheet`），左右各留 8px、高度上限 62vh，见 [SHEET_QUERY](../frontend/src/components/HelpPopover.tsx#L18)。
4. **浮层内部不允许横向溢出**：面板宽度上限 440px，三列表格会在浮层内撑出滚动条并把列截掉 —— 代理端口对照因此用两列网格而不是表格。浮层挂在 `white-space: nowrap` 的宿主元素（例如状态文字）内时，必须显式重置 `white-space` 与字重，否则说明文字会继承成一行粗体。
5. **固定宽度的轨道里，文本必须能断行**：`.grid` 的右栏是写死的 `390px`（[styles.css](../frontend/src/styles.css#L104)），内容区只剩 352px，而 grid item 的 `min-width: auto` 不允许它被压窄 —— 因此「放不进一行的长 token」（自检回显的 Windows 路径、URL、后端错误原文）会让面板的 min-content 超过轨道，把整页撑出横向滚动条。右栏因此整体声明 `overflow-wrap: anywhere`（[styles.css](../frontend/src/styles.css#L124)，靠继承覆盖全部后代），见 [ai/ui/002](../ai/ui/002-side-column-overflow-breaks-the-page.md)。**必须用 `anywhere`**：`overflow-wrap: break-word` 不改变 min-content，挡不住这一类溢出。
6. **会随数据变长的标签必须允许换行，不得用 `nowrap` 把它锁成一行**：字幕语言选择器的标签是 `已选 N 项：en, zh-Hans, …`（join 的是**语言代码**，N 无上界）。它曾经是 `white-space: nowrap` + `overflow: hidden; text-overflow: ellipsis`，意图是「太长就省略号」—— 但 nowrap 之下没有断行机会，`overflow-wrap` 对它**无效**，标签的 min-content 就等于整段文字，而它是 flex item、父级按它的 min-content 算宽度 → 放不下时不是被裁，是把右栏轨道和整个网格一路顶宽（12 种语言实测 84px），见 [ai/ui/003](../ai/ui/003-language-trigger-label-overflows-the-page.md)。判据：**`ellipsis` 只在元素真的被约束时才会出现** —— 先看 `scrollWidth > clientWidth` 成不成立；不成立就说明「省下来的空间」并没有省下来，只是换了个样子出问题。

## 依赖方向与分层规则

![后端模块依赖](assets/diagrams/module-dependencies.svg)

PlantUML 源文件：[module-dependencies.puml](diagrams/module-dependencies.puml)。

规则：

1. 依赖只向下：L0 → L1/L2/L3/L4，L2 → L1/L3/L4，L3 → L3/L5，L4 → L1，L5 不依赖任何项目模块。
2. **L3 不得反向依赖 L2 或 L0**。典型反例：让 `ytdlp_service` 去更新 `JobItem` 状态。正确做法是把结果返回给 `job_manager`，由编排层决定状态。
3. **L1 保持纯净**：`schemas.py` 不导入 yt-dlp，`config.py` 不导入数据库层。这样 API 契约可以脱离运行时被单独审阅、测试和生成文档。
4. 新能力优先落到 L3 的单一模块（例如"判断是否需要合并音视频"属于 `ytdlp_formats.requires_ffmpeg`），只有需要"在多个模块之间做决定"时才上移到 L2。
5. 同一层内部的横向依赖要显式说明理由。当前存在的横向依赖：`job_manager` → `ytdlp_formats`（复用降级计算）与 `job_read_model` → `output_paths`（复用路径发现），两者都是复用纯函数，不构成循环。L0 内部另有一组单向横向依赖：`routers/*` → `api_support` / `job_artifacts` / `api_context`，方向固定（路由是叶子，支撑模块不反向导入任何路由），因此不会成环。
6. **组合根只做装配**：`create_app` 里出现业务分支即为越界 —— 判断「这段代码该放哪」的判据是"它需不需要一个 `Request`"，需要就进 `routers/`，只是一段被多个路由复用的逻辑就进 `api_support.py` / `job_artifacts.py`。

## 运行时并发模型

![运行时并发](assets/diagrams/runtime-concurrency.svg)

PlantUML 源文件：[runtime-concurrency.puml](diagrams/runtime-concurrency.puml)。

单进程内部有两种执行载体：

- **事件循环（主线程）**：FastAPI 请求处理、`EventBroker` 扇出、`JobManager` 的 worker 任务与队列。所有状态机的状态转换发生在这一侧（其中一部分经锁保护后落到工作线程）。
- **工作线程**：每个在途视频占用一个线程。worker 任务用 `asyncio.to_thread(self._run_item_work, item_id)` 把整段下载搬出事件循环，因此 yt-dlp 的同步阻塞不会卡住 API 和 SSE。

由此推出三条必须遵守的约定：

1. **进度 hook 在工作线程上执行**，所以它使用自己的 `Session` 写库，不能复用请求级 session。
2. **从线程发布事件要跨回事件循环**：[_publish_threadsafe](../backend/app/job_manager.py#L1108) 先写 `JobEvent` 行，再 `loop.call_soon_threadsafe` 调度 `broker.publish`。异步路径直接用 [_publish](../backend/app/job_manager.py#L1095)。
3. **锁只保护状态转换，不保护下载**：`_item_claim_lock` 只覆盖"刷新 → 校验 queued → 置 running → commit"；`_cookie_import_lock` 只在 403 后的 cookies 刷新导入期间持有。下载本身靠 `should_cancel` 回调协作取消，而不是靠锁。
4. **单个条目的收尾出错不能带走 worker**：worker 循环对每条 item 的整段工作加了兜底 —— 崩溃的条目被标记为 `failed`（而不是永远停在 `running`），worker 继续消费队列。没有这层兜底，一个条目的记账错误就会静默地少掉一个并发口。见 [008](../ai/bug-fix/008-return-in-finally-swallows-the-real-error.md)。

并发度语义：并发是**视频级**的——worker 数等于当前并发设置，队列里流动的是 `JobItem` id。单视频任务只有 1 个 `JobItem`，因此并发设置对它无效；`set_concurrency()` 通过新增 worker 任务或投放 `None` 哨兵来调整规模。

## 关键数据流

![下载数据流](assets/diagrams/download-data-flow.svg)

PlantUML 源文件：[download-data-flow.puml](diagrams/download-data-flow.puml)。

### 解析链接

`POST /api/analyze` → `YtDlpService.extract_metadata()`（`extract_flat="in_playlist"`，带 cookies）→ `AnalyzeResponse`。若 yt-dlp 抛出的错误同时命中 cookies 提示词，则自动导入浏览器 cookies 后**重试一次**，见 [_extract_metadata_with_cookies](../backend/app/main.py#L120)。

### 建任务与入队

`POST /api/jobs` → 解析 → 选条目（单视频合成 1 条 `VideoEntry`）→ 写 `Job` + N 个 `JobItem` → `enqueue()` 把 queued item id 放进队列。playlist 任务会额外在下载根目录下按安全化标题创建子目录，见 [_job_download_dir](../backend/app/main.py#L644)。

### 下载与进度

worker 领取 item → 声明式预检测（`prepare_download`，命中则不再二次 `extract_metadata`）→ 下载 → 进度 hook 依次经过 `StallGuard` 与 `DownloadProgressAggregator`，再由 `ProgressPersistGate` 决定是否落库 → `JobEvent` 持久化 + SSE 通知 → 前端收到信号后重新拉取 `/api/jobs` 读模型。终态写入平均速度并收敛任务状态。

### 设置变更

`PUT /api/settings` → 写 `Setting` 行 → 按字段分派副作用：并发调整 worker 数；限速/重试改写 queued/running/paused 任务的 `options_json`，并把正在运行的 `JobItem` 标记为待重启（`_runtime_restart_items`），让当前 yt-dlp 实例抛出 `DownloadCancelled` 后重新入队，靠 `.part` 续传应用新参数；aria2c 连接数只更新设置与 `YtDlpService`。

### 删除与文件清理

删除统一走 `JobManager`：先删数据库记录与关联 `JobEvent`，再按需删除文件。文件删除只在"下载根目录"或"该任务下载目录"之内执行，并对 `output_path`、合并后的最终文件与 sidecar 分别枚举候选，见 [_delete_output_files](../backend/app/job_manager.py#L351)。删除 playlist 的最后一个子项时父任务一并删除。

## 状态机与一致性

`Job` 与 `JobItem` 共用 `JobStatus` 六态，但驱动者不同：

- `JobItem` 由 worker 驱动：认领时置 `running`，结束按结果置 `succeeded` / `failed` / `cancelled` / `paused`。
- `Job` 由 [_maybe_finish_job](../backend/app/job_manager.py#L493) 收敛：只要还有 running 或 queued 子项就保持 `running`；全部结束才由 [_finish_job](../backend/app/job_manager.py#L767) 判定终态（有失败项 → `failed`；被暂停 → `paused`；被取消 → `cancelled`；否则 `succeeded`）。
- 任务级进度是子项进度的算术平均，见 [_refresh_job_counts](../backend/app/job_manager.py#L815)。

并发一致性依赖三点：单条 `JobItem` 只会被一个 worker 认领（`_item_claim_lock` + 状态校验）；worker 线程各自持有 session 并独立 commit；读模型只读不写，避免与写入路径争抢状态。

## 持久化设计

| 决策 | 内容 | 理由 |
| --- | --- | --- |
| WAL + `busy_timeout=5000` + `synchronous=NORMAL` | 每个连接建立时下发 pragma，见 [_configure_sqlite_connection](../backend/app/db.py#L14)。 | 多 worker 并发写入时"等待"而不是直接 `database is locked`；被其他进程锁住时只跳过 pragma，不让应用起不来。 |
| 追加式补列 | [_ensure_columns](../backend/app/db.py#L43) 用 `PRAGMA table_info` + `ALTER TABLE ADD COLUMN`。 | 兼容旧库，无需迁移框架。**只支持追加**，不支持删除/改名/改类型。 |
| WAL checkpoint | lifespan 启动与停止各执行一次 `wal_checkpoint(TRUNCATE)`，见 [checkpoint_wal](../backend/app/db.py#L72)。 | 避免进程被强杀后 `-wal` 侧车无界增长，也避免每次读都回放 WAL。失败只记日志。 |
| 进度写入节流 | `ProgressPersistGate`：首次、终态、间隔 ≥250ms 或进度跳变 ≥0.5% 才写。 | 5 路并行时避免把每个 chunk 回调都变成一次事务。 |
| 进度聚合 | `DownloadProgressAggregator` 按子流分别累计已下载字节，分母取"各子流已知总和的较大者"并以下限钳制。 | 字幕/chunk/分离音视频都会各自回调；不能让小文件或当前 chunk 的 100% 冒充整体完成，也不能把已下载字节归零。 |

## 错误分类与可观测性

错误分类集中在 `YtDlpService` 的静态判定函数上，降级判定集中在 [resolution_decisions.py](../backend/app/resolution_decisions.py#L87)，`job_manager` 只消费这两者的结果、负责 IO 与状态写入：

| 分类 | 判定依据 | 处理 |
| --- | --- | --- |
| cookies 缺失/需登录 | 错误链中出现 cookies 提示词 + 登录/bot 关键词，见 [is_cookie_required_error](../backend/app/ytdlp_service.py#L795)。 | 刷新浏览器 cookies 后重试一次；仍失败则给出两条可操作建议。 |
| 媒体流阻断 | 403/`forbidden` 或连接重置族关键词，见 [is_media_stream_blocked_error](../backend/app/ytdlp_service.py#L692)。 | 在同清晰度下换 profile 重试；终态文案前置 cookies 状态，并给出降清晰度重启建议。 |
| 目标格式不可用（**预检阶段**） | 错误文本含 `requested format is not available`，由 [prepare_download](../backend/app/ytdlp_service.py#L393) 抛出。 | 归一成 `is_selectable=False`，按原因分类后**真的**用降级清晰度重新预检并下载。 |
| 目标格式不可用（**下载阶段**） | 同上，但出现在预检通过之后（两次请求之间格式列表变了）。 | 标注 `requested_resolution_unselectable` 并把错误替换成降级说明；按 [架构文档](architecture.md) 的约定，**不**自动重下。 |
| 停滞 | 看门狗判定字节峰值超时未刷新。 | 直接失败，不换 profile、不重试。 |
| 其他 | 兜底。 | 透传清洗后的错误文本，见 [readable_error_message](../backend/app/ytdlp_service.py#L766)。 |

可观测性由三处构成：任务中心读模型（进度/速度/大小/实际分辨率/格式/错误）、`/api/diagnostics`（依赖与稳定性参数，且不回显 token 原文）、以及 `JobEvent` 表（事件审计，可与 SSE 推送交叉核对）。

## 扩展点

| 想做的事 | 正确落点 |
| --- | --- |
| 新增一个下载 profile（新的 player client / impersonation 组合） | 在 `ytdlp_service.py` 加常量并扩展 `_download_profiles()`、`_youtube_extractor_args()`、`_impersonation_target()`；若属于 anti-403 链路需同步 `YOUTUBE_ANTI403_PROFILES`。 |
| 新增一个可配置项 | `config.py` 加字段 → 需要的话在 `api_support.py` 的 `apply_stored_settings` / `settings_response` 与 `Setting` 表打通 → 补 `/api/settings` 路由（`routers/settings.py`）与诊断字段 → 更新环境变量文档。 |
| 新增一个降级原因 | 在 `fallback_policy.py` 加常量与文案、补一条 `restart_resolution` 规则，并在 `resolution_decisions.py` 里加上产出该常量的一条判定；不要在 `job_manager` 里内联中文字符串。`test_resolution_decisions.py` 有一条参数化用例会遍历所有 reason 常量，漏加文案会当场报错。 |
| 新增一个 API | 在 `routers/` 下对应资源的模块里声明路由（新资源就新建一个模块并登记进 `API_ROUTERS`），模型放 `schemas.py`，跨路由复用的逻辑放 `api_support.py` / `job_artifacts.py`；涉及状态转换的一律委托 `JobManager`，不直接改表。 |
| 新增一种错误分类 | 在 `YtDlpService` 加静态判定函数，在 `_log_item_failure` 加类别名，并补单测确认不会与其他分类互相误命中（尤其注意关键词冲突）。 |

## 已知限制与设计债

1. **停滞判定依赖 progress 回调**：如果 yt-dlp 完全阻塞在 socket 读且不回调，看门狗不会触发，此时只有 `socket_timeout=30` 兜底。因此不能声称"解决了所有卡死"。
2. **进程重启不恢复在途任务**：历史 `queued`/`running` item 在进程退出后不会自动重新入队或标记为取消，界面上表现为"永远排队"。这是可靠性问题，已在 [PLAN.md](../ai/perf/PLAN.md) 第 8 节记录为待单开计划处理。
3. **无正式迁移框架**：`_ensure_columns` 只能加列。删除列、改类型或数据回填都需要手工 SQL，且没有版本记录表。
4. **单视频无法内部并行**：yt-dlp 内建 http 下载器是单连接，`concurrent_fragment_downloads=1`，因此单视频提速只能靠 aria2c（默认关闭，且会推高 403 风险）。
5. **profile 链是串行且昂贵的**：每个 profile 都是一次完整 extract，失败路径的耗时按 profile 数累加；`DownloadStalled` 与 `DownloadCancelled` 已跳过链路，其余错误仍会逐级重试。
6. **`notify_on_complete` 未接线**：请求模型接受该字段但下载链路不消费，属于历史遗留字段。
7. **`data/` 下的旧库可能存在大量历史 item**：影响验收统计，也说明缺少归档/清理策略。

以上限制的共同验证方式是 [测试文档](testing.md) 的自动测试与离线基准；涉及真实网络的验收必须以真实的 cookies 与连通性为前提，不能用手工观察替代。
