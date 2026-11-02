# 架构设计

适用读者：需要理解系统组成、模块边界和数据流的开发者与维护者。

如果需要按软件工程架构规范进行系统性审查，请先阅读 [4+1 架构视图](4-plus-1-view.md)。本页继续保留系统上下文、组件、数据流和状态生命周期的详细说明；模块级的职责划分、依赖方向与运行时并发模型见 [设计文档](design.md)。

## 系统上下文

![系统上下文](assets/diagrams/system-context.svg)

PlantUML 源文件：[system-context.puml](diagrams/system-context.puml)。

系统由本机浏览器中的 React 前端、本机 FastAPI 后端、SQLite 数据库、`yt-dlp`、`ffmpeg`、浏览器 cookies 存储和 YouTube 媒体服务组成。普通使用默认由 FastAPI 在**同一个端口**上托管构建后的前端与 API，端口由 `YTDL_API_PORT` 决定（默认 `8000`，`python -m app` 启动时会打印实际地址），见 [README 快速启动](../README.md#快速启动)；前端热更新开发模式见 [开发文档](development.md#本地运行)。

## 容器与职责

| 容器 | 职责 | 主要入口 |
| --- | --- | --- |
| React/Vite 前端 | 解析表单、下载选项、任务中心、cookies 操作和设置面板。 | [App.tsx](../frontend/src/App.tsx)、[api.ts](../frontend/src/shared/api.ts#L40) |
| FastAPI 后端 | HTTP API、SSE、任务调度、SQLite 持久化、调用 yt-dlp。 | [create_app](../backend/app/main.py#L40) |
| 启动入口 | 解析命令行、启动前做端口预检并识别占用者、把实际监听地址打印出来，再交给 uvicorn。 | [__main__.py](../backend/app/__main__.py#L64)、[dev_server.py](../backend/app/host/dev_server.py#L44) |
| 任务调度与执行 | 队列、worker、暂停/重启/删除、进度聚合、错误分类与终态收敛。 | [JobManager](../backend/app/download/job_manager.py#L51) |
| SQLite | 存储任务、子任务、设置和事件。 | [models.py](../backend/app/contracts/models.py#L27)、[db.py](../backend/app/infra/db.py#L27) |
| yt-dlp 服务 | 元数据解析、下载参数构建、profile 重试、格式选择和依赖诊断。 | [YtDlpService](../backend/app/download/ytdlp_service.py#L189) |
| 停滞看门狗 | 观测 progress 回调，把静默卡死转为可见失败。 | [StallGuard](../backend/app/download/stall_guard.py#L32) |
| 浏览器 cookies 导入器 | 从本机浏览器导入 YouTube/Google cookies，并处理 Edge 锁库和 CDP fallback。 | [BrowserCookieImporter](../backend/app/host/browser_cookies.py#L67) |
| 代理与连通性自检 | 解析代理来源（显式 > 系统 > 环境变量）并**真的发一次请求**验证连通；cookies 体检则把离线格式检查与 `LOGGED_IN` 探针合成一个结论。 | [proxy.py](../backend/app/host/proxy.py#L169)、[connectivity.py](../backend/app/host/connectivity.py#L73)、[cookie_health.py](../backend/app/host/cookie_health.py#L234) |
| 错误翻译与自检留痕 | 把异常链翻译成「诊断 / 原因 / 建议」三段可执行结论；启动时摘除会打坏 JS 运行时的宿主环境变量并留痕。 | [error_advice.py](../backend/app/download/error_advice.py#L116)、[runtime_env.py](../backend/app/host/runtime_env.py#L58) |
| 本机打开器 | 选择可解码播放器打开视频、打开输出目录、把字幕文件交给系统打开（默认关联 / 系统「打开方式」）。**不维护程序偏好** —— 用哪个程序由系统记。 | [system_open.py](../backend/app/host/system_open.py#L23) |
| B站 上传编排 | 单 worker 串行投稿、投稿间隔、状态写库与重启收敛、环境体检、事件发布。与下载链路**完全解耦**：独立账号、独立登录文件、独立队列。 | [BiliManager](../backend/app/bilibili/bili_manager.py#L423) |
| B站 领域与边界 | 单次投稿的领域逻辑（对账后定结论）、`biliup` 子进程边界、B站 会员态客户端、扫码登录与凭据摘要、元数据与硬限、文案常量。 | [bili_upload.py](../backend/app/bilibili/bili_upload.py#L299)、[bili_bin.py](../backend/app/bilibili/bili_bin.py#L271)、[bili_api.py](../backend/app/bilibili/bili_api.py#L124)、[bili_auth.py](../backend/app/bilibili/bili_auth.py#L75)、[bili_meta.py](../backend/app/bilibili/bili_meta.py#L202)、[bili_policy.py](../backend/app/bilibili/bili_policy.py#L38) |

## 组件关系

![组件关系](assets/diagrams/component-overview.svg)

PlantUML 源文件：[component-overview.puml](diagrams/component-overview.puml)。

`backend/app` 的第一层目录按功能划分（`api/` `contracts/` `download/` `bilibili/` `host/` `infra/` `common/`），层号写在 [设计文档的模块职责矩阵](design.md#目录结构按功能分包) 里而不是目录名上 —— 目录回答「服务于谁」，矩阵回答「能依赖谁」。后端把 HTTP 层拆成「装配根 + 按资源分模块的路由」：`main.py` 只做装配与静态托管，路由在 [routers/](../backend/app/api/routers/__init__.py) 下按资源分模块（诊断、cookies、解析、任务、任务文件、设置、事件、bilibili），跨路由复用的支撑逻辑在 [api_support.py](../backend/app/api/api_support.py) 与 [job_artifacts.py](../backend/app/download/job_artifacts.py)，请求级依赖容器是 [api_context.py](../backend/app/api/api_context.py)；再往下，任务管理、读模型、下载服务、cookies 导入、格式选择、降级决策与降级文案、代理解析与连通性自检各自独立成模块，细节见 [设计文档的模块职责矩阵](design.md#模块职责矩阵)。前端由 [App.tsx](../frontend/src/App.tsx) 只做状态编排与布局，展示职责按功能分目录（[download-request/](../frontend/src/download-request/) / [task-center/](../frontend/src/task-center/) / [settings/](../frontend/src/settings/) / [cookies/](../frontend/src/cookies/) / [bilibili/](../frontend/src/bilibili/) / [notifications/](../frontend/src/notifications/)，基础件与跨功能纯逻辑在 [shared/](../frontend/src/shared/)）：解析面板 [UrlAnalyzer](../frontend/src/download-request/UrlAnalyzer.tsx#L29)、解析结果 [AnalysisPanel](../frontend/src/download-request/AnalysisPanel.tsx#L12)、下载选项 [DownloadOptionsPanel](../frontend/src/download-request/DownloadOptionsPanel.tsx#L26)、设置 [SettingsPanel](../frontend/src/settings/SettingsPanel.tsx#L70)、任务中心 [JobQueue](../frontend/src/task-center/JobQueue.tsx#L24)、cookies 区 [CookieSection](../frontend/src/cookies/CookieSection.tsx#L121)、代理区 [ProxySection](../frontend/src/settings/ProxySection.tsx#L140)、语言选择 [SearchableLanguageSelect](../frontend/src/download-request/SearchableLanguageSelect.tsx#L16)、基础件 [Toggle](../frontend/src/shared/ui/Toggle.tsx#L9) / [StatusPill](../frontend/src/shared/ui/StatusPill.tsx#L4)、B站 上传的 [BiliUploadButton](../frontend/src/bilibili/BiliUploadButton.tsx#L42) / [BiliUploadDialog](../frontend/src/bilibili/BiliUploadDialog.tsx#L65) / [BiliJobStatus](../frontend/src/bilibili/BiliJobStatus.tsx#L41) / [BiliLoginSection](../frontend/src/bilibili/BiliLoginSection.tsx#L35) / [BiliIcon](../frontend/src/bilibili/BiliIcon.tsx#L12)，以及被多处共用的说明浮层 [HelpPopover](../frontend/src/shared/ui/HelpPopover.tsx#L42)；纯计算在 [formatting.ts](../frontend/src/shared/formatting.ts)、[quality.ts](../frontend/src/shared/quality.ts)、[subtitles.ts](../frontend/src/shared/subtitles.ts)、[cookieLock.ts](../frontend/src/cookies/cookieLock.ts)，B站 上传另有 [biliForm.ts](../frontend/src/bilibili/biliForm.ts)（表单词汇表、本地预检与字符预算，含合集**渲染后**的标题长度）、[biliStatus.ts](../frontend/src/bilibili/biliStatus.ts)（稿件/合集状态判定）、[biliProgress.ts](../frontend/src/bilibili/biliProgress.ts)（上传进度分段）、[biliHealth.ts](../frontend/src/bilibili/biliHealth.ts)（体检的超时与降级）、[errors.ts](../frontend/src/shared/errors.ts)（异常 → 一句人话）；任务完成提醒的判定与呈现也分成两层 —— [notifications.ts](../frontend/src/notifications/notifications.ts)（终态判定、提醒文案、浮层队列生命周期，纯逻辑）与 [CompletionToasts.tsx](../frontend/src/notifications/CompletionToasts.tsx#L21)（右下角浮层容器 + Web Audio 现场合成的提示音），HTTP 边界集中在 [api.ts](../frontend/src/shared/api.ts#L40)。

B站 上传这一组还额外把「**界面状态从哪来**」定死了：任务行上那一档徽标（未上传 / 上传中 / 已上传 / 待核对 / 部分失败 / 失败）由后端从该任务的全部记录现场聚合（[derive_badge](../backend/app/bilibili/bili_read_model.py#L60)），前端只负责渲染颜色与文案。前端自己判「算不算已上传」迟早会和后端的两套状态机漂移。

## 关键数据流

### 单视频

![单视频时序](assets/diagrams/single-video-sequence.svg)

PlantUML 源文件：[single-video-sequence.puml](diagrams/single-video-sequence.puml)。

用户先调用 `POST /api/analyze` 获取元数据，再通过 `POST /api/jobs` 创建任务。`JobManager` 将任务入队，worker 调用 `YtDlpService.prepare_download()` 预检测，再调用 `download()` 下载。进度通过数据库、SSE 和 `/api/jobs` 回到前端。下载过程中 [StallGuard](../backend/app/download/stall_guard.py) 观测 progress 回调；如果在配置的窗口内没有新的字节峰值，任务会以可读原因失败，而不是停留在 `running`。逐段数据流见 [设计文档](design.md#关键数据流)。

### Playlist

![Playlist 时序](assets/diagrams/playlist-sequence.svg)

PlantUML 源文件：[playlist-sequence.puml](diagrams/playlist-sequence.puml)。

Playlist 解析后由前端提交选中的条目索引。后端为每个条目创建 `JobItem`，worker 按并发上限同时处理多个子视频，单项失败不会阻断已完成项的状态记录。

### Cookies

![Cookies 流程](assets/diagrams/cookies-flow.svg)

PlantUML 源文件：[cookies-flow.puml](diagrams/cookies-flow.puml)。

Cookies 可手动上传或从浏览器导入。解析阶段遇到需要登录或 bot 校验时，后端会尝试自动导入并重试一次，逻辑见 [_extract_metadata_with_cookies](../backend/app/api/api_support.py#L87)。下载阶段遇到同类错误时，任务管理器会刷新 cookies 并重试当前子视频，逻辑见 [_download_with_cookie_refresh](../backend/app/download/job_manager.py#L765)。

### 上传到 B站

下载完成的视频可以再投稿到 B站，这是一条**独立数据流**（详细步骤见 [设计文档](design.md#上传到-b站)）：

1. 前端 `GET /api/bilibili/prefill` 拿预填值（单视频一组值；合集是模板 + 逐条预览 + 超限定位）。
2. `POST /api/bilibili/uploads`（或 `/collections`）先做前置条件与硬限校验：不满足就 `409`/`400` 而不入队 —— 一次可解释的拒绝，好过留下一条要用户去清理的失败记录。
3. `BiliManager` 的单 worker 串行处理：抽帧封面（可选）→ `biliup` 子进程投稿 → **回读账号稿件列表对账**（标题前缀匹配）→ 发并置顶评论（可选）。
4. 状态写库后经 SSE 通知；前端按 `GET /api/bilibili/uploads/summary`（任务中心整列一次拿全）或单任务读模型重拉。
5. 稿件已在平台上之后，把下载阶段存下来的字幕作为**可关闭的 CC 字幕**一并提交（SRT/VTT 转成 BCC，挂在分P 的 `cid` 上）。它有自己的三档结论（`posted` / `skipped` / `failed`），**独立于稿件状态、也不进徽标**，失败还可以只重传一次字幕而不重投视频。
6. 合集的视频全部确认发布后，再单独走一次归集：建 `season` → 逐条加进 section。归集是另一次调用、另一张表，失败不影响已发布的稿件。

与下载最大的不同在于**「完成」的判据不在本机**：子进程返回码 0 只说明命令成功，平台上到底有没有这条稿件必须回读才知道，因此 `unverified`（待核对）是这套状态机里的一等状态，而不是失败的变体。

## 状态生命周期

![下载生命周期](assets/diagrams/download-lifecycle.svg)

PlantUML 源文件：[download-lifecycle.puml](diagrams/download-lifecycle.puml)。

任务状态由 [JobStatus](../backend/app/contracts/models.py#L12) 定义：`queued`、`running`、`paused`、`succeeded`、`failed`、`cancelled`。任务级进度由子视频进度聚合得到，读模型见 [job_read_model.py](../backend/app/download/job_read_model.py#L12)。

B站 上传有**自己的两套状态**，与 `JobStatus` 无关：

- 稿件 [BiliUploadStatus](../backend/app/contracts/models.py#L104)：`queued` → `uploading` → `submitted`（命令成功、待对账）→ `published` / `reviewing` / `rejected`（按平台状态码折合）；命令失败落 `failed`，对账查不到落 `unverified`（待核对）。**`unverified` 不是失败的变体**：它表达「我们还不知道」，所以它的重试只重读平台、绝不重投。
- 合集 [BiliSeasonStatus](../backend/app/contracts/models.py#L122)：`pending` → `grouping` → `grouped` / `failed`。`grouped` 只表示「已提交归集」——B站 对合集还有一道人工审核。
- 字幕 `subtitle_state`：`skipped`（默认，没有字幕可传）→ `posted` / `failed`。它**不是** `BiliUploadStatus` 的一档：字幕挂在投稿**之后**，把「已发布 + 字幕失败」折成一种失败，用户能做的唯一正确动作（重传字幕）就被隐藏了。下载侧另有 `subtitle_source`（`human` / `auto` / `none`），其中 `none` 是「这个视频没有可用中英字幕」的**结论**，与 `NULL`（这一轮还没有结论）不同。

进程重启后，停在 `uploading` 的记录（含 `queued`）一律折成**可重试的失败**，而不是永远停在「上传中」，见 [resume_interrupted](../backend/app/bilibili/bili_manager.py#L465)。

**下载侧的策略与它相反**：残留的 `queued`/`running` 条目默认**重新排队并断点续传**，见 [recover_orphaned_work](../backend/app/download/job_manager.py#L83)。判据是「重做一次的代价」：下载是幂等且可续传的（`.part` 接着用），最坏只是多下一遍；投稿重投会在平台上留下重复稿件。同一条原则在两个链路上导出两个相反的默认值 —— 别把其中一个当成通用规则。用户按过「暂停」的任务在任何策略下都不恢复。

## 数据模型

![数据模型](assets/diagrams/data-model.svg)

PlantUML 源文件：[data-model.puml](diagrams/data-model.puml)。

核心表是 `Job`、`JobItem`、`JobEvent` 和 `Setting`；B站 上传另有两张自己的表 [`BiliUpload`](../backend/app/contracts/models.py#L132)（一条稿件一行，入参与结果都存）和 [`BiliSeason`](../backend/app/contracts/models.py#L180)（一个任务的合集一行）。旧数据库兼容列通过 `_ensure_columns()` 自动补齐，见 [db.py](../backend/app/infra/db.py#L27)。

B站 的**凭据不在这两张表里**，也不在任何表里：登录态只以文件形式放在 `data/bilibili/cookies.json`（gitignored），API 只回传名字与计数。凭据落库会被读模型、备份与 SQL 转储一并带出去，而这个文件形态还能直接被 `biliup` 复用，不必在两层之间搬运凭据。

## 文档工具链

文档写作和 UML 生成环境也作为工程系统的一部分交付。仓库内 [docs.py](../scripts/docs.py) 管理固定版本 PlantUML jar 缓存、SVG 渲染、本地链接检查和 UML 产物一致性检查；Java 与 Graphviz 作为开发机系统依赖。安装和使用方式见 [文档写作与生成环境](documentation-workflow.md)。

## 设计取舍

- 下载能力集中封装在 `YtDlpService`，避免 API 层暴露任意 yt-dlp 参数。
- 任务执行与 API 读模型分离，API 只读取投影，任务管理器负责状态转换。
- 分辨率降级只在下载前可判断的场景自动发生；媒体流 403/连接重置不会中途自动降级重下，详见 [技术文档](technical.md#分辨率降级原因)。
- 稳定性默认值来自 [PLAN.md](../ai/perf/PLAN.md) 的修复基线：节流守卫默认关闭（`YTDL_THROTTLED_RATE_KBPS=0`），改用 90 秒停滞看门狗兜底；aria2c 多连接默认关闭，仅在显式启用且需要给单视频提速时使用。稳定优先的运行方式是把并发设为 1。
- 单端口部署：FastAPI 在 `frontend/dist` 存在时挂载静态资源并提供首页，见 [main.py](../backend/app/main.py#L127)；否则只提供 API，页面走 Vite dev server。
- B站 上传**不做「下载完自动投」**：投稿要填标题、分区、标签这类只有人知道的信息，自动投等于替用户做了一个他没法复核的决定。所以它是任务行上的一个显式按钮 + 一张可改的预填表单。
- B站 上传的**队列与下载队列分开**（`BiliManager` 的单 worker vs `JobManager` 的并发 worker）：平台限的是账号而不是本地并发，两者共用一个队列会逼出一个不属于下载的限速规则。
- **「完成」的判据放在平台侧**：命令返回码 0 之后必须回读账号稿件列表对账，因此多出一个 `unverified` 状态。这条带来一个已知代价 —— 对账靠标题前缀匹配，投稿后改名就对不上（见 [设计文档的已知限制](design.md#已知限制与设计债)）。
- 合集**不重复建**：归集失败时保留已拿到的 `season_id`，重试只补加视频缺口。重复建合集在平台侧是不可撤销的污染，比「多等一次」贵得多。
