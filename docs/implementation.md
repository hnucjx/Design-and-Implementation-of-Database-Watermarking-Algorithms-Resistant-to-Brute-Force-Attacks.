# 实现文档

适用读者：需要修改代码或排查行为的后端、前端维护者。架构层面的说明见 [架构设计](architecture.md)。

## 后端入口

FastAPI 应用由 [create_app](../backend/app/main.py#L40) 创建，启动时：

- 创建配置和目录。
- 初始化 SQLite engine 和表结构。
- 创建 `EventBroker`。
- 创建 `YtDlpService` 并注入 PO token、chunk、throttled rate、aria2c 等配置。
- 创建 `JobManager` 并在 lifespan 中启动/停止 worker。
- 创建 [BiliManager](../backend/app/bilibili/bili_manager.py#L423)（B站 投稿队列，单 worker）并同样在 lifespan 中启停；启动时会先把上次退出时停在 `uploading` 的记录折成可重试的失败。
- 把这些依赖装进 [ApiContext](../backend/app/api/api_context.py#L28) 并挂到 `app.state`。

`create_app` 只做装配：HTTP 路由按资源拆到 [routers/](../backend/app/api/routers/__init__.py#L21) 下（`diagnostics` / `cookies` / `analyze` / `jobs` / `job_files` / `settings` / `events` / `bilibili`），由 `API_ROUTERS` 统一挂载。路由只做「取参 → 调辅助 → 定状态码」：请求级依赖经 [api_context.py](../backend/app/api/api_context.py#L45) 的 `ContextDep` / `SessionDep` 取，跨路由复用的任务/条目定位、设置读写与 cookies 处理在 [api_support.py](../backend/app/api/api_support.py#L26)，产物定位与本机打开在 [job_artifacts.py](../backend/app/download/job_artifacts.py#L31)。任务序列化逻辑委托给 [job_read_model.py](../backend/app/download/job_read_model.py#L12)，状态转换一律委托 `JobManager`；B站 那一组同理由 [bili_manager.py](../backend/app/bilibili/bili_manager.py) 承担，读模型投影在 [bili_read_model.py](../backend/app/bilibili/bili_read_model.py#L60)。

## 数据模型

核心持久化模型见 [models.py](../backend/app/contracts/models.py#L27)。

| 表 | 作用 |
| --- | --- |
| `Setting` | 保存下载目录、并发、默认清晰度、字幕语言、限速和重试次数等用户设置。 |
| `Job` | 保存任务级 URL、标题、状态、进度、错误、下载目录和聚合计数。 |
| `JobItem` | 保存 playlist 子视频或单视频的实际进度、输出文件、分辨率、格式、错误和降级原因，以及字幕结论（`subtitle_language` 族名 / `subtitle_source` `human`·`auto`·`none` / `subtitle_path`）。 |
| `JobEvent` | 保存任务事件，配合 SSE 推送。 |
| `BiliUpload` | B站 投稿记录：入参（标题/简介/标签/分区/视频路径/是否做封面/评论）+ 结果（`bvid`/`aid`/`cid`、进度、对账结论 `verified_state`、`attempt`、`log_tail`）+ 字幕结论（`subtitle_state` / `subtitle_lan` / `subtitle_message`，**独立于稿件状态**）。**入参也落库**，这样用户在关掉页面之后重试仍能照原样重投。 |
| `BiliSeason` | B站 合集归集记录：`season_id`（建好后即使补加视频失败也保留，重试只补加不重建）、归集状态与错误。 |

数据库初始化调用 [init_db](../backend/app/infra/db.py#L38)。为了兼容旧数据库，[_ensure_columns](../backend/app/infra/db.py#L43) 会补齐新增列。[create_app_engine](../backend/app/infra/db.py#L27) 在每条 SQLite 连接上先设置 `busy_timeout=5000`，再启用 WAL 和 `synchronous=NORMAL`，让多个下载 worker 的进度写入互相等待而不是立刻报 `database is locked`。如果数据库已被其他进程锁住（例如本机已有 uvicorn），WAL pragma 会跳过而不是让应用启动失败。应用启动和停止时各执行一次 `PRAGMA wal_checkpoint(TRUNCATE)`（[checkpoint_wal](../backend/app/infra/db.py#L109)）：避免进程被强杀后遗留的 `-wal` 文件无界增长，也避免每次读取都要回放 WAL；该操作失败只记日志，不影响启动或停止。

## 任务调度

[JobManager](../backend/app/download/job_manager.py#L51) 负责队列、worker、暂停、重启、删除、任务状态和事件发布。

关键流程：

1. `POST /api/jobs` 写入 `Job` 和 `JobItem`，然后调用 [enqueue](../backend/app/download/job_manager.py#L178) 把待处理子视频入队。
2. worker 从队列取出 `JobItem` id，调用 [_run_item_work](../backend/app/download/job_manager.py#L487)。
3. 并发上限按**视频**计，而不是按任务计：一个合集的多个子视频可以同时占用多个 worker。
4. 单项由 [_run_item](../backend/app/download/job_manager.py#L566) 处理，负责预检测、下载、进度 hook、错误分类和终态写入。
5. 每个子项结束后调用 [_maybe_finish_job](../backend/app/download/job_manager.py#L541)；没有 queued/running 子项时才 [_finish_job](../backend/app/download/job_manager.py#L822) 聚合任务终态。
6. worker 对每条 item 的整段工作有兜底：抛出的异常记进日志，并由 [_mark_item_failed_after_crash](../backend/app/download/job_manager.py#L441) 把该条目落成 `failed`（已经落定的条目不动），worker 继续消费队列。因此 `finally` 这类收尾块里**不允许**出现 `return` —— 那会吞掉正在传播的异常，见 [008](../ai/bug-fix/008-return-in-finally-swallows-the-real-error.md)。
7. 进程启动时先调 [recover_orphaned_work](../backend/app/download/job_manager.py#L83) 收上一次退出留下的孤儿条目，**然后**才允许 worker 消费队列，见本节的「进程退出与恢复」。

暂停和重启会重置运行中字段，但保留可重新执行的任务记录，见 [restart](../backend/app/download/job_manager.py#L220) 和 [restart_item](../backend/app/download/job_manager.py#L271)。Playlist 子视频删除由 `delete_items()` 处理：删除指定 `JobItem` 后刷新父任务聚合状态；如果删除最后一个子视频，父任务也会被删除。

运行时下载设置由 `PUT /api/settings` 触发：并发调用 `set_concurrency()` 立即调整 worker；限速和重试次数调用 `set_runtime_download_defaults()` 更新 queued/running/paused 任务的 `options_json`。当当前 `JobItem` 正在下载时，`should_cancel` 会让 yt-dlp 在下一个可中断点退出，该子项被重新置为 queued 并重新入队，依靠断点续传应用新的限速或重试次数。前端在下载选项面板复用设置保存状态样式，并用请求序号避免快速输入时旧响应覆盖最后一次输入。

两把锁的边界：

- `_item_claim_lock` 只包住「刷新状态 → 检查 queued → 置 running → commit」，不覆盖下载过程。
- `_cookie_import_lock` 只在 403 触发的 cookies 刷新导入期间短暂持有。

事件有两份记录：内存中的 `EventBroker`（SSE 推送）和持久化的 `JobEvent` 行。worker 线程通过 [_publish_threadsafe](../backend/app/download/job_manager.py#L1228) 写库，再用 `loop.call_soon_threadsafe` 把推送调度回事件循环；纯异步路径直接用 [_publish](../backend/app/download/job_manager.py#L1215)。

**订阅者必须被显式注销**：[EventBroker.subscribe](../backend/app/infra/events.py#L16) 是一个异步生成器，
它在 `finally` 里把自己从订阅集合里 `discard` 掉。但 `async for` **不会**关闭子异步生成器（PEP 525 的老坑），
只写 `async for message in broker.subscribe()` 的话，注销要等垃圾回收才发生 —— 每关一个标签页就留一个订阅者，
而 `publish()` 仍会逐个喂它，队列是无界的。因此路由层显式持有订阅对象并在 `finally` 里
[aclose](../backend/app/api/routers/events.py#L30)（见 [event_stream](../backend/app/api/routers/events.py#L20)）：
**注销判据仍收在 `subscribe()` 一处**，HTTP 层只负责「保证它被调用」（见 [ai/bug-fix/027](../ai/bug-fix/027-sse-disconnect-keeps-the-listener.md)）。

### 进程退出与恢复

队列是**内存**的：进程一退，`queued` 条目的 id 就没了，而 `jobitem.status` 还留着。重启后没有任何代码把这两个状态重新交给 worker，于是界面上是一排**永远不动、且看不出原因**的任务 —— 生产库曾累积 332 个 `queued` + 3 个 `running`（[`ai/perf/PLAN.md`](../ai/perf/PLAN.md) §8）。

[recover_orphaned_work](../backend/app/download/job_manager.py#L83) 在 lifespan 里、`start()` 之前执行。它把工作拆成两半：

- [orphan_recovery.recover_orphaned_items](../backend/app/download/orphan_recovery.py#L77) 只改**条目**状态并返回报告，不碰任务终态；
- 任务终态回到运行期同一个 [_maybe_finish_job](../backend/app/download/job_manager.py#L541)，避免「什么算结束」出现两份定义。

顺序是硬要求：**先改库（工作线程里、一个 worker 都还没有），再 `start()`**。反过来 worker 会与被扫描的行并发读写，`item.status` 的读-改-写互相覆盖。

按目标准入的分支（判据全部落在库里可见的事实上 —— 内存集合随进程一起消失，靠不住）：

| 条目所在处 | 结果 |
| --- | --- |
| 任务 `paused` | 条目折回 `paused`，**任何策略下都不恢复**：用户按的暂停优先。 |
| 任务 `cancelled`，或该任务**已有** `cancelled` 条目 | 条目折成 `cancelled`。`cancel()` 对运行中的任务只在内存里记一笔，进程一退就没了，所以「正在被取消」只能由已取消的条目反推。 |
| 所属任务不存在 | 折成 `cancelled` 并写明原因：队列永远不会给它派活。 |
| 其余 | 按 `YTDL_ORPHAN_RECOVERY` 处理：`resume`（默认）重新排队并清空进度字段；`fail` 落成 `failed` 并写明「上次退出时正在下载 / 还在排队」。 |

`resume` 之所以是默认：下载**幂等且可续传**（`build_download_options` 里 `continuedl=True`，`.part` 接着用），重排队最坏只是多下一遍；而 `fail` 要用户逐条点「重启」才能恢复，正是这个缺陷的痛点。B站 上传选「折成失败」的理由（重投会产生重复稿件）在下载侧不成立。恢复过的任务会给 `started_at` 重新计时 —— `elapsed_seconds` 量的是「下载花了多久」，不是「从下单到现在」。

每次恢复都写一条 `jobs_recovered` 事件（`JobEvent` + SSE），「这个任务为什么自己动起来了」在事件表里有答案。见 [015](../ai/bug-fix/015-orphan-items-never-resume.md)。

## yt-dlp 封装

[YtDlpService](../backend/app/download/ytdlp_service.py#L189) 是 yt-dlp 的边界层。它负责：

- 解析元数据：[extract_metadata](../backend/app/download/ytdlp_service.py#L357)。
- 下载前选择计划格式：[prepare_download](../backend/app/download/ytdlp_service.py#L395)。
- 构建下载参数：[build_download_options](../backend/app/download/ytdlp_service.py#L496)。
- 同清晰度 profile 重试：[download](../backend/app/download/ytdlp_service.py#L576)。
- 依赖诊断：[get_dependency_status](../backend/app/download/ytdlp_service.py#L296)。
- 错误分类：cookies、403、连接重置、JS challenge 和格式不可用。

`YtDlpService` 不把任意 yt-dlp 参数暴露给 API，只接受项目定义的 `DownloadOptions`。

### 代理

代理**解析**在 [proxy.py](../backend/app/host/proxy.py#L169)（纯函数、优先级：显式 > Windows 系统代理 > 环境变量），**验证**在 [connectivity.py](../backend/app/host/connectivity.py#L73)（一次朴素 HTTPS 探针）。`YtDlpService` 在元数据提取与下载两条路径上写入 `ydl_opts['proxy']`，**只有**来源是 `setting` / `system` / `direct` 时才写（来源是环境变量时留给 yt-dlp 自己解析，以保留 `NO_PROXY` 语义）。职责划分与取值语义见 [技术文档](technical.md#代理与网络出口)。

### JS 运行时

[_detect_js_runtime](../backend/app/download/ytdlp_service.py#L976) 的探测结果是记忆化的，且**真的用与 yt-dlp 相同的权限模型跑一次**候选运行时，而不是只看文件是否存在；失败原因通过 `js_runtime_error` / `js_runtime_candidates_rejected` 暴露给诊断。[reset_js_runtime_cache](../backend/app/download/ytdlp_service.py#L266) 供「重新自检」清缓存，见 `POST /api/diagnostics/runtime`。

启动时 [runtime_env.sanitize_environment](../backend/app/host/runtime_env.py#L58) 会摘掉会打坏 JS 运行时的宿主环境变量（`NODE_OPTIONS` 命中强加载开关），并返回记录用于日志与诊断。理由见 [技术文档](technical.md#js-运行时与-n-challenge)。

### 错误翻译

[error_advice.advise](../backend/app/download/error_advice.py#L116) 把异常链上的文本翻成「code + 结论 + 下一步」，判定顺序是 JS challenge → cookies → 代理 → 媒体流。异常链本身由 `error_advice.exception_chain()` 展开（BFS 遍历 `__cause__` / `__context__`，去重防环）；`ytdlp_service` 里的分类函数**直接调用它**，不再经过本类的转发方法（见 [ai/refactor/009](../ai/refactor/009-reclaim-compat-proxies.md)）。

调用点有两处，**都被 `try/except` 保护**（诊断本身出错绝不能改变重试与失败行为，测试里的 fake service 也没有这个方法）：profile 失败处与任务失败处，后者见 [_log_item_failure](../backend/app/download/job_manager.py#L1165)。

profile 顺序由 [_download_profiles](../backend/app/download/ytdlp_service.py#L841) 决定：`default`、`default_aria2c`（仅当 aria2c 启用且可执行文件存在）、`mweb_pot_chrome`、`safari_hls`、`chrome_default`。`default` profile 如果不是媒体流阻断错误会立即抛出（不换 profile），其余错误才继续下一个 profile。`DownloadCancelled` 与 `DownloadStalled` 都会跳出重试链直接上抛，见 [download](../backend/app/download/ytdlp_service.py#L576)。

## 停滞看门狗

[StallGuard](../backend/app/download/stall_guard.py#L32) 在 [_download_once](../backend/app/download/ytdlp_service.py#L637) 的 progress hook 里被调用：每次回调先检查取消标志，再 `observe()` 一次，最后才交给任务管理器的进度 hook。

判据是「历史最大已下载字节是否被刷新」（`best_bytes` + 达成时间），而不是「本轮是否增长」——这样才能区分节流振荡与正常断点续传。`status == "finished"` 会重置基线，因为合并格式在视频流与音频流之间会重新从 0 计数。超时后抛出 `DownloadStalled`，文案固定为「下载停滞：N 秒内没有新增字节」，不含 `timed out` / `reset` / `403` 等词，避免被 [is_media_stream_blocked_error](../backend/app/download/ytdlp_service.py#L732) 误分类。`YTDL_STALL_TIMEOUT_SECONDS=0` 表示关闭。

## 清晰度与降级

格式选择和分辨率工具位于 [ytdlp_formats.py](../backend/app/download/ytdlp_formats.py)，降级文案在 [fallback_policy.py](../backend/app/download/fallback_policy.py)，而**降级决策**在 [resolution_decisions.py](../backend/app/download/resolution_decisions.py#L87)。后者是纯模块：四个 `decide_*` 函数只吃「用户选的清晰度 + 可用格式列表」，吐「降级到哪 / 原因 / 报哪句话」，不联网也不认识 `JobManager` —— 所以 `backend/tests/test_resolution_decisions.py` 能在 0.1 秒内把每条规则单独验一遍。

下载前先调用 [_probe_preparation](../backend/app/download/job_manager.py#L948) 探测目标清晰度能否选出可下载组合。能选出就直接用这批 options 下载，不再额外调用 `extract_metadata`（上一轮性能修复的成果）。探针报「不可选」时 —— 无论是返回 `is_selectable=False`，还是抛 yt-dlp 的 `Requested format is not available` —— 才由 [_extract_formats_or_none](../backend/app/download/job_manager.py#L1137) 再解析一次元数据，交给 `decide_probe_fallback` 区分 `requested_resolution_missing`、`source_below_720_only` 和 `requested_resolution_unselectable`，然后用降级清晰度**再探一次**；通过才把降级后的 options 交给下载步骤。注意 `_extract_formats_or_none` 取不到元数据时返回 `None` 而不是空列表 —— 这是接口约定（"没取到"与"没有格式"是两件事），但 `resolution_decisions` 当前对两者的结论相同，该等价性由 `test_missing_metadata_and_empty_format_list_currently_agree` 钉住。

探针的两种失败表现必须都算否定结论，否则降级分支会变成死代码（症状：提示「已自动降级」却不下载），见 [010](../ai/bug-fix/010-unselectable-probe-raise-skips-the-fallback.md)。与之相对，网络 / JS challenge / cookies 失效等异常要原样抛出，不能被降级吞掉。

媒体流 403/连接重置只标注 `media_stream_blocked` 并给重启建议，不自动降清晰度重下：判定见 `decide_media_stream_fallback`（允许降到 720p 以下），调用点见 [_annotate_media_stream_fallback](../backend/app/download/job_manager.py#L1109)。

## 进度与平均速度

yt-dlp 对字幕、缩略图、HTTP chunk 和分离视频/音频流会多次发送 progress payload。某个小文件或当前 chunk 的 `downloaded_bytes == total_bytes` 并不代表整个任务完成。后端使用 [DownloadProgressAggregator](../backend/app/download/download_progress.py#L21) 聚合多子流，并用 `prepare_download()` 得到的计划大小作为进度分母下限；当新子流让总大小增加时，允许百分比回落到真实比例，避免运行中进度被锁在 `99.9%`。已下载字节按子流单调累加，因此分离音视频时 UI 不会看起来像重新开始。

进度聚合和平均速度在每个 yt-dlp callback 上都会更新；SQLite 提交和 SSE 由 [ProgressPersistGate](../backend/app/download/progress_persist.py#L5) 节流：首次快照、`finished`/`error`、进度跳变 ≥ 0.5%，或距上次写入 ≥ 250ms 才会落库。这样 5 路并行下载不会把每次 chunk 回调都写成数据库事务。

视频大小复用 `JobItem.total_bytes`：下载前由 `prepare_download()` 从所选格式的 `filesize/filesize_approx` 写入，下载中由 progress payload 校准，下载完成后继续保留，前端在任务行和 playlist 子视频行展示。

视频时长走的是另一条路，因为它**不需要等选出格式**：`POST /api/jobs` 建任务时就把解析结果里每个条目的 `duration` 写进 `JobItem.duration`（[routers/jobs.py](../backend/app/api/routers/jobs.py#L57)），任务一进队列就能显示。`duration` 这一列是后补的，补列之前建的旧任务重启时由 [_apply_download_preparation](../backend/app/download/job_manager.py#L1032) 从预检的同一份 `info` 里补上（预检反正要取一次元数据，不为时长多发一次请求）。前端格式化见 [formatMediaDuration](../frontend/src/shared/formatting.ts#L15)：固定 `时:分:秒`，与「已用/剩余」用的 `formatClock` 刻意不同 —— 后者不到一小时时省掉时位，而时长是视频的固有属性，三段位数固定更好扫读。任务行的时长由 [formatJobDuration](../frontend/src/task-center/JobQueue.tsx#L610) 按子视频求和，与「大小」同一套折法（有子视频未知时前缀「已知」）。

平均速度由 [TransferStats](../backend/app/download/transfer_stats.py#L5) 根据聚合下载字节和时间计算。运行中 `speed` 是 yt-dlp 当前瞬时速度，终态 `speed` 是平均速度，终态聚合见 [_terminal_job_speed](../backend/app/download/job_manager.py#L925)。

### B站 上传的进度

**下载有字节级进度，上传没有**：`biliup` 在管道模式（非 TTY）下不输出已传字节数，只在三个节点各打一行（`pre_upload` / `Upload completed` / `APP接口投稿成功`），[_PROGRESS_STAGES](../backend/app/bilibili/bili_bin.py#L196) 把它们折成 0.2 / 0.6 / 0.95。所以界面上给的是**四段轨迹**（排队 / 传输 / 提交 / 核对）与「已用时间」，不是百分比条 —— 一个停在 20% 不动的数字比没有数字更糟。阶段映射与聚合在 [biliProgress.ts](../frontend/src/bilibili/biliProgress.ts#L23)。

事件侧有一条与下载不同的约定：[_store_progress](../backend/app/bilibili/bili_manager.py#L1326) **只在进度值真的变大时才发 `bili_upload_progress`**。`on_progress` 是每读一行输出回调一次，不判「变没变」会往 SSE 里灌几百条同值事件；判了之后，一次投稿最多 3 条。前端沿用「收到任何一条事件就重拉」的既有口径，因此不需要为进度维护第二份状态。

## 读模型

API 返回不直接暴露 SQLModel，而由 [read_job](../backend/app/download/job_read_model.py#L12) 生成：

- 计算任务级实际分辨率和格式。
- 将多个子项不同结果聚合为 `混合分辨率` 或 `混合格式`。
- 为单视频失败透传具体错误。
- 计算 `elapsed_seconds`。
- 调用 `build_resolution_fallback()` 生成用户可读消息和重启建议。
- 投影字幕结论（`subtitle_language` / `subtitle_source`）。**不在这里拼展示文案**：投稿表单里那句
  `en · 人工` 由 [`subtitle_summary`](../backend/app/download/subtitle_plan.py#L92) 在服务端的预填阶段算好
  （`BiliPrefillItem.subtitle`），前端原样显示 —— 两处各拼一遍就会出现「界面写自动生成、后端写 ASR」。
- 把逐任务的「完成后通知」开关从 `options_json` 抬进读模型（[_notify_on_complete](../backend/app/download/job_read_model.py#L44)）。
  它只存在于 `options_json` 里，而 SSE 的每个事件只是让前端重拉 `/api/jobs` —— 不抬出来的话，
  「这次会话里提交的任务会提醒、重开页面后同一个任务就不提醒」。提醒本身在浏览器里做，
  后端只存偏好（`completion_notification`）与这个逐任务开关，见 [FR-48](requirements.md#功能需求)。

## B站 投稿

B站 上传是一条与下载完全解耦的链路，模块划分（详见 [设计文档的职责矩阵](design.md#模块职责矩阵)）：

| 模块 | 职责 |
| --- | --- |
| [bili_policy.py](../backend/app/bilibili/bili_policy.py#L38) | 文案与分类常量：平台状态码 → 人话、记录状态 → 人话、失败输出 → （结论 + 下一步）、徽标档位与配色。 |
| [bili_meta.py](../backend/app/bilibili/bili_meta.py#L202) | 元数据：模板占位符渲染（**显式替换，不用 `str.format`** —— 用户标题里的大括号不能被当成字段）、标签派生与收敛、分区小词表、硬限校验。 |
| [bili_http.py](../backend/app/bilibili/bili_http.py#L66) | 标准库 `urllib` 的 HTTP 接缝，**强制直连**（清空 `ProxyHandler`），且 4xx/5xx 也把正文与 `Set-Cookie` 带回来。 |
| [bili_auth.py](../backend/app/bilibili/bili_auth.py#L75) | 扫码登录（二维码 / 轮询）、**TV 端三步换投稿令牌**、登录文件读写（四形态兼容）、**凭据摘要（只回名字与计数）**、是否被 git 跟踪。 |
| [bili_bin.py](../backend/app/bilibili/bili_bin.py#L271) | 投稿工具边界：`biliup` 探测（含 WinGet 0 字节空壳识别/修复）、命令组装、子进程执行、进度解析、ffmpeg 抽封面。 |
| [bili_api.py](../backend/app/bilibili/bili_api.py#L124) | B站 会员态客户端：稿件对账、稿件详情（cid）、建合集、归集稿件、列合集、发并置顶评论、**CC 字幕保存**（`oid` 用 cid、两个布尔按小写字面量发）。全部经可注入 `transport`。 |
| [bili_subtitle.py](../backend/app/bilibili/bili_subtitle.py#L93) | B站 CC 字幕：SRT/VTT → BCC 转换（`from`/`to` 是**秒**）、`<c>` 内联标签剥离、**滚动字幕的 10 毫秒过渡条丢弃**、相邻重复字幕合并、语言标识映射（**只输出平台白名单内的值**；界面另用 `language_display` 说人话）、一次上传的结论（`posted` / `skipped` / `failed`）。**失败一律落成结论，不抛异常。** |
| [bili_upload.py](../backend/app/bilibili/bili_upload.py#L299) | **单次上传的领域逻辑**：编排上面几个模块，产出「已发布 / 审核中 / 待核对 / 失败」的结论与下一步；含模板批量渲染与逐条校验。纯函数式，可整链离线单测。 |
| [bili_read_model.py](../backend/app/bilibili/bili_read_model.py#L60) | 把两张表投影成 API 读模型；**「多条记录 → 一档徽标」的聚合规则**只在这里定义。 |
| [bili_manager.py](../backend/app/bilibili/bili_manager.py#L328) | 队列与编排：单 worker 串行、投稿间隔、状态写库与收敛、重启收敛、环境体检（含投稿令牌档）、事件发布。 |

几个必须知道的实现细节：

- **`run_command` 的读取放在后台线程**。早先是「主线程 `for line in process.stdout` 读完再 `wait(timeout)`」—— 阻塞在管道上时那段 `wait` 根本执行不到，于是一个**不打印任何输出就挂住**的投稿会让超时形同虚设（记录永远停在「上传中」），而「挂死」恰恰是这个超时唯一要防的东西。现在读取是 daemon 线程 + 有界 `join`，超时会 kill 并写入 `local-upload-deadline-reached` 标记，该标记会被 [classify_failure](../backend/app/bilibili/bili_policy.py#L351) 翻成「这次上传等得太久」。
- **`--submit` 照传，但它在 0.2.4 里不起作用**：上游 `uploader.rs` 的 `match submit` 两个分支都调 `submit_by_app`，实际提交永远走 `/x/vu/app/add`。真正会拦住投稿的是**登录文件里缺 `token_info.access_token`** —— `login_by_cookies` 会先拿它去 `oauth2/info` 校验，空串换来 `-400 请求错误` 之后 biliup 直接退出。[exchange_tv_token](../backend/app/bilibili/bili_auth.py#L142) 补上 TV 端三步交换，并把 `platform` 写成 `BiliTV`（biliup 靠它挑续期用的 appkey），见 [011](../ai/bug-fix/011-biliup-refuses-a-token-less-cookie-file.md)。
- **失败时 biliup 的原始输出会逐行进日志**（前缀 `biliup 输出 | `，`WARNING` 级）。此前 `UploadContext.on_log` 没有任何调用方，用户照着失败建议去 `app.log` 搜只会扑空；现在这条通道由 [bili_manager._log_biliup_line](../backend/app/bilibili/bili_manager.py#L184) 提供，落盘前过 `sanitize_log_message`。
- **传输层断线也是一类结论**。`biliup` 被对端断开时输出里**没有业务码**（`error sending request` → `connection error` → `peer closed connection … close_notify`），旧分类表一条也命中不了，界面只剩「没能识别出具体原因」。现在 `TRANSPORT_FAILURE_NEEDLES` 收这一类，并按「失败那一行的 URL 是不是上传 CDN」决定敢不敢承诺「重试不会投重」——承诺的成立只依赖「字节没传完 ⇒ 提交接口没被调用过」（见 [021](../ai/bug-fix/021-a-dropped-upload-link-looks-like-nothing-at-all.md)）。
- **对账按标题前缀匹配，且两侧都做 `strip()`**。平台会对标题做首尾空白处理；只归一不比较的写法会让用户多打一个尾随空格就永远查不到，于是一次成功投稿被标成「待核对」。
- **请求参数风格在两个端点上不一致**：`season/add` 的 `csrf` 走 form 且返回 `data` 为 `season_id`；`season/section/episodes/add` 的 `csrf` 走 **query**，body 用 **camelCase**（`sectionId` / `episodes`）。两处都由 `bili_api` 封装，调用方不用记。
- **评论幂等**：`/x/v2/reply/send` 已下线，用 `POST /x/v2/reply/add`，置顶用 `POST /x/v2/reply/top`。先查自己的评论（`mid` 匹配 + marker），有就只补置顶；重复评论码 `12051` 时换 `mode=2`（时间序）重找再决定。
- **二维码 SVG 的尺寸单位是毫米**（B站 返回 `width="29.6mm"`），前端必须显式覆盖宽高，否则渲染尺寸会跟着宿主字号变。
- **CC 字幕挂在投稿的最末尾**，因为字幕要挂在**分P**上：必须先拿到 `cid`（`oid` 取的就是它），
  而 `cid` 只有对账之后才有。拿不到就 `skipped`，绝不因为字幕把已发布的稿件翻成失败
  （[upload_subtitle](../backend/app/bilibili/bili_subtitle.py#L277) 内部也不抛异常，理由同上）。
- **BCC 的时间是秒不是毫秒**。`00:01:02,500` 解析成 `62.5`；解析不出来返回 `None` 而不是猜 0 ——
  时间错了整条字幕就废了，猜出来的 0 会让所有字幕堆在片头。
- **语言标识不是需求里写的那两个值**：B站 不接受 `zh-CN` / `en-US`（`code=79011 不合法的语言`），
  实测白名单只有 `zh` / `en` / `zh-Hant` / `zh-TW` / `zh-HK` / `ja` / `ko` / `yue` / `fr`。
  所以 `zh-Hans` 压成 `zh`、繁体三档原样保留、认不出来退 `en`。
  这条实测靠 `submit=false`（只存草稿、不发布）逐个枚举候选值得到 —— 它是唯一能在
  **不动任何已发布稿件**的前提下验证接口形态的方式。
- **字幕路径只在 `JobItem` 上存一份**（[`_subtitle_of`](../backend/app/bilibili/bili_manager.py#L1268) 从那里读），
  投稿记录里不复制；读的时候还要**再看一次文件在不在**，不在就当没有。
- **字幕失败有专门的补救动作**：已发布/审核中且 `subtitle_state=failed` 的稿件，重试走
  `_reverify_work` 并带上字幕，回执「已重新上传字幕」。**绝不**为了字幕重投一次视频。

## 前端实现

前端 API 调用集中在 [api.ts](../frontend/src/shared/api.ts#L40)。共享类型集中在 [types.ts](../frontend/src/contracts/types.ts)。[App.tsx](../frontend/src/App.tsx#L68) 只做状态编排与布局，展示拆成解析面板 [UrlAnalyzer](../frontend/src/download-request/UrlAnalyzer.tsx#L29)、解析结果 [AnalysisPanel](../frontend/src/download-request/AnalysisPanel.tsx#L12)、下载选项 [DownloadOptionsPanel](../frontend/src/download-request/DownloadOptionsPanel.tsx#L26)、任务中心 [JobQueue](../frontend/src/task-center/JobQueue.tsx#L24)、设置 [SettingsPanel](../frontend/src/settings/SettingsPanel.tsx#L70)，基础件是 [SearchableLanguageSelect](../frontend/src/download-request/SearchableLanguageSelect.tsx#L16) / [Toggle](../frontend/src/shared/ui/Toggle.tsx#L9) / [StatusPill](../frontend/src/shared/ui/StatusPill.tsx#L4)；cookies 区域与代理区域分别是 [CookieSection](../frontend/src/cookies/CookieSection.tsx#L121) 和 [ProxySection](../frontend/src/settings/ProxySection.tsx#L140)。字幕文案与来源归一是纯模块 [subtitles.ts](../frontend/src/shared/subtitles.ts#L12)，cookies 锁库错误到界面状态的映射是 [cookieLock.ts](../frontend/src/cookies/cookieLock.ts#L20)。

B站 上传的界面由四个组件加一个图标组成：[BiliIcon](../frontend/src/bilibili/BiliIcon.tsx#L12)（手绘 SVG，品牌色只落在图标本身）、[BiliUploadButton](../frontend/src/bilibili/BiliUploadButton.tsx#L42)（图标 + 一句人话，`idle` 档显示动作词，其余档原样显示后端徽标文案；**点下去做什么按档位分流**：已上传打开稿件页、待核对只核对、部分失败只补没成功的、上传中不给点，见 [FR-39](requirements.md#功能需求) / [FR-40](requirements.md#功能需求)）、[BiliUploadDialog](../frontend/src/bilibili/BiliUploadDialog.tsx#L65)（单视频与合集**共用**一个模态表单，合集显示逐条预览；表单里的值是服务端预填的，**投过就复用上一次提交的那一份**并明说，见 [FR-45](requirements.md#功能需求)）、[BiliJobStatus](../frontend/src/bilibili/BiliJobStatus.tsx#L41)（任务卡片里的明细与重试入口，**只在有话说时才渲染**）、[BiliLoginSection](../frontend/src/bilibili/BiliLoginSection.tsx#L35)（设置面板里的体检 / 扫码 / 导入 / 修复工具）。硬限数字与状态文案全部来自服务端，前端只负责显示成 `12/80` 与选颜色 —— 自己写一份常量迟早出现「界面说没超、提交回来 400」。

辅助说明统一走 [HelpPopover](../frontend/src/shared/ui/HelpPopover.tsx#L42)：它是**非模态浮层**，默认收起；桌面端悬停即看、点击钉住，窄屏（`≤640px`）渲染为贴底抽屉。三条实现约定值得单独记住：

- **钉住会记忆、关闭会清掉**：`rememberOpen` 把 `cascade.help.open.v1.<id>` 写进 `localStorage`，关闭时 `removeItem`；因此刷新后最多只有一块说明保持展开，见 [rememberOpen](../frontend/src/shared/ui/HelpPopover.tsx#L29)。
- **不抢输入框焦点**：触发按钮在 `mousedown` 上阻止默认行为，面板 `aria-modal=false` 且没有遮罩。点开「常用端口」时，代理输入框里没保存的地址不会因失焦被提前写进设置。
- **定位在打开时算一次**：面板用 `fixed` 定位并夹进视口（下方空间不足则上翻），因此它不占版面、展开前后主功能区位置不变；窗口尺寸变化会重新计算。

辅助函数职责：

- [formatting.ts](../frontend/src/shared/formatting.ts)：时长、百分比、日期、文件大小、分辨率和速度格式化。
- [quality.ts](../frontend/src/shared/quality.ts)：清晰度选项、可用高度、降级按钮标签和选中清晰度显示。

**报错按「它在说谁的事」分三层，页面级那层必须跟着视口走。**
页面级（一次动作失败了的回执）走 [ErrorBanner](../frontend/src/shared/ui/ErrorBanner.tsx)：
`.error-banner` 是 `position: sticky; top: 12px; z-index: 30` —— 不滚动时仍排在页头下方，
一旦滚过就贴住视口顶边，**任何滚动位置都在视野里**。区域级（解析面板的锁库提示、
任务行上的本地文件操作错误）与弹窗级（上传表单的错误）**刻意不上升**到页面级：
它们解释的是「这个输入框 / 这一行 / 这个弹窗」，离得越近越好读。判据与逐场景清单见
[ai/ui/009](../ai/ui/009-page-level-errors-scroll-out-of-view.md)。

四条行为约定（`App.clearError()` 是唯一出口）：

- **不自动消失**：错误里常带着要照着做的信息（路径、建议、该重试还是先改设置），
  定时缩回等于「还没读完就走了」。消失只有三条路：点「关闭」、焦点在条内按 `Esc`、
  以及**下一次动作开始时被收回** —— 旧报错描述的那次操作已经不成立，留着它用户会拿一条
  过期的失败去解释刚做成的另一件事（[api.ts](../frontend/src/shared/api.ts#L59) 的注释里记着这类误读）。
- **同刻只有一条**：新错误替换旧错误，不排队、不堆叠。
- **不替用户滚动、不抢焦点**：出现时不调 `scrollTo` 也不 `focus()`；条子插在内容之上，
  浏览器用**滚动锚定**把用户正在读的那一行钉在原地（实测参照卡片 `Δ=0px`，`scrollY` 由 1073 调到 1138）。
- **层级低于弹窗与完成提醒**（`z-index: 30` < 遮罩 50 < 右下角提醒 60）：弹窗开着时错误在弹窗里就近显示，
  页面级回执不该浮到遮罩上面。

这层容器的必要性有实测依据：它原来是页头下面一段普通内容，滚到任务中心（`scrollY=1073`）
时旧位置 `top=-1019px`，整条在视口上方一屏之外 —— 用户看到的只是「点了没反应」。

**每个请求都有截止时间**：[api.ts](../frontend/src/shared/api.ts#L51) 的 `request()` 用 `AbortController` 挂一个定时器，
默认 60 秒（[REQUEST_TIMEOUT_MS](../frontend/src/shared/api.ts#L51)），超时后把 `AbortError` 翻译成一句
**能照做的话**（[timeoutError](../frontend/src/shared/api.ts#L59)：区分「没响应」与「失败」，并指向日志文件与重启后端）。
唯一放宽的是**解析链接**（180 秒，[ANALYZE_TIMEOUT_MS](../frontend/src/shared/api.ts#L57)）—— 它要真的连 YouTube，
实测能到半分钟以上。两个值都取自「服务端最长的一次合法阻塞」的实测（打开方式窗口 6 s、cookies 库约 10 s、B站 请求 20 s），
所以看门狗不会误伤正常路径。**超时与用户主动取消必须分得开**：前者要报错，后者应该安静地结束，
因此 `catch` 里用 `controller.signal.aborted` 判断而不是看异常类型。

任务完成提醒同样分成**判定**与**呈现**两层：[notifications.ts](../frontend/src/notifications/notifications.ts) 是纯逻辑（哪些状态算终态、一条提醒写成什么话、浮层队列的 `useCompletionToasts` hook），[CompletionToasts.tsx](../frontend/src/notifications/CompletionToasts.tsx#L21) 只渲染队列，并用 Web Audio **现场合成**提示音（C6→G6 两个正弦音，第二个轻一点晚一点；仓库里没有任何音频文件）。三条实现约束值得单独记住：

- **触发条件是状态跃迁，不是「当前状态」**：必须比对上一次状态，否则一进来就已跑完的历史任务会被反复播报；`cancelled` 不算终态（取消是用户自己点的）。
- **标签页在后台时浮层不倒计时**：只记「还剩多久」，等 `visibilitychange` 说可见了再接着走 —— 否则「右下角弹窗」这种方式在后台等于没选。
- **判定抽成纯函数才测得到**：jsdom 里没有 `AudioContext`、也不做布局，所以「谁该被提醒、提醒什么」在 `notifications.ts` 里可注入、可单测（`completion-notification.test.tsx`，19 例）。

前端不负责构造 yt-dlp selector，只提交 `DownloadOptions`，由后端决定实际格式。下载选项面板会根据解析结果展示字幕来源和字幕格式；当用户指定的字幕来源在当前视频中缺失但另一种来源存在时，前端提交前会 fallback 到可用来源。

任务中心删除入口分为任务级和 playlist 子视频级：任务级调用 `DELETE /api/jobs/{job_id}`，子视频级调用 `POST /api/jobs/{job_id}/items/delete`。删除文件入口统一在发请求前用确认弹窗保护。删除时如果 `JobItem.output_path` 为空，后端会在任务下载目录中按 YouTube id 发现输出视频、字幕、metadata 和 `.part` 文件。

任务中心播放和打开文件夹入口调用后端受控本机打开 API：单视频任务调用 `POST /api/jobs/{job_id}/play` 与 `POST /api/jobs/{job_id}/open-folder`，playlist 子视频调用对应的 item endpoint，playlist 任务行用同一个 open-folder endpoint 打开 `Job.download_dir`。打开字幕另有 `POST .../open-subtitle`（任务级与条目级各一条）：界面调用**不带参数**，缺省 `mode=chooser`（弹系统的「打开方式」）；`mode=default` 保留给接口调用方。后端只根据数据库中的 `output_path`、`subtitle_path`、`download_dir` 或按 YouTube id 在下载目录中发现的本地文件打开目标，不接受前端传入任意路径。

`output_path` 可能来自 yt-dlp 分离音视频流的中间文件名，例如 `title [id].f137.mp4` 或 `title [id].f140.m4a`。下载完成时和本地打开前，后端会通过 [output_paths.py](../backend/app/download/output_paths.py) 解析到合并后的最终文件，例如 `title [id].mp4`，避免中间文件被合并删除后误报“视频文件不存在”。读模型同样会在 `output_path` 为空时尝试发现 `... [id].mp4/.webm/.mkv`，让历史任务恢复播放和文件夹入口。

本机打开器位于 [system_open.py](../backend/app/host/system_open.py)。打开目录时显式调用 `explorer.exe /n, <folder>` 并尽量把窗口置前，减少已有窗口只在任务栏闪烁但没有前置的情况。播放视频不再盲目使用默认文件关联，而是优先选择 VLC、mpv、PotPlayer、MPC、IINA 等可确认解码能力更强的播放器；常见 MP4 可回退到 Windows Media Player。找不到合适播放器时返回 `409`，错误内容包含当前格式和建议安装的播放器。

**打开字幕不挑程序**：`POST /api/jobs/{job_id}/open-subtitle` 与条目级同名接口把
`JobItem.subtitle_path` 交给系统 —— 缺省（界面走的就是这条）弹**应用自带的「打开方式」对话框**，候选读系统注册表为该后缀登记的处理器（见 [open_path_with_chooser](../backend/app/host/system_open.py#L108)）；
`?mode=default` 才走文件关联，Windows 上该后缀还没有关联程序时**自动改弹对话框**而不是把 `WinError 1155` 抛给用户。
三级链按顺序处理：**系统两条入口先试**（`SHOpenWithDialog` → `OpenAs_RunDLL`），**判据是窗口高度不是窗口存在**
（降级提示框 680×118、选择器约 700×500），都不成才用**自带对话框**（候选 = 系统登记的处理程序 + 本机真实存在的常见编辑器 + 「浏览…」）；
**弹不出来必须报错**（等 3s 仍没有 `TkTopLevel` 就 400，绝不 204 空转；用户点取消则静默返回）；
**前台只能等、不能抢**（提前 `SetForegroundWindow` 会被前台锁拒绝，而且被拒后对话框不再自行激活）。
实测与细节见 [technical.md](technical.md#打开字幕文件) 第二~四节。
字幕位置由 [existing_subtitle_file](../backend/app/download/output_paths.py#L184) 算：库里的 `subtitle_path` 优先（只验存在），
否则看视频旁边的同名 `.srt`/`.vtt`（补这一列之前下载的历史任务就是这么找到的）。没有字幕时返回 `409`；
`mode` 取其它值一律 `422`。为什么没有编辑器清单、也没有应用侧「上次用的查看器」，见
[technical.md](technical.md#打开字幕文件)。

播放、打开文件夹、打开字幕这类本地文件操作失败时，前端在 [JobQueue](../frontend/src/task-center/JobQueue.tsx) 的对应任务行或子视频行附近显示错误，而不是只放在页面顶部的全局提示区。
「打开字幕」按钮本身是**一颗**图标按钮 [SubtitleOpenButton](../frontend/src/task-center/SubtitleOpenButton.tsx#L19)：
`title="打开字幕文件；可选择打开方式"`，点它就是「弹选择器」那一条请求（不带 `mode`）；能不能点**只看**读模型给的
`subtitle_path`（`null` 就灰着，悬停说「字幕文件尚不可用」），所以界面上不存在「点得下去但必然 409」的按钮。

任务中心复制链接入口是纯前端行为，使用 `navigator.clipboard.writeText()` 复制 `Job.url` 或 `JobItem.source_url`，成功后按钮短暂显示“已复制”。

任务中心跳转 YouTube 页面同样是纯前端行为，使用 `window.open(sourceUrl, "_blank", "noopener,noreferrer")` 打开 `Job.url` 或 `JobItem.source_url`，不经过后端本地文件打开接口。

## 日志安全

下载失败日志会记录 job id、item id、标题、清晰度、错误分类和清洗后的错误文本，见 [_log_item_failure](../backend/app/download/job_manager.py#L1165)。日志清洗工具位于 [log_safety.py](../backend/app/common/log_safety.py#L20)，用于避免敏感 query、cookies 或 token 进入日志。

## 日志落盘

[configure_logging](../backend/app/infra/logging_setup.py#L48) 在 `create_app()` 一开始就被调用，把 root logger 配成「控制台 + 文件」：`<data_dir>/logs/app.log`，UTF-8，`时间 级别 模块 | 消息`，2 MiB × 3 轮转。

三个容易踩的点：

- **以前根本没有配置过 logging**：`logger.info("proxy resolved: ...")` 只会冒泡到 root，而 root 没有 handler，于是 `logging.lastResort`（WARNING、无格式）接手 —— INFO 被静默丢弃、WARNING 只剩裸消息。「代理明明没生效，日志里却什么都看不到」就是这么来的。
- **uvicorn 的 logger 默认 `propagate=False`**，所以同一个文件 handler 也显式挂到 `uvicorn` / `uvicorn.error` / `uvicorn.access` 上，否则启动与访问日志会漏掉。
- **`create_app()` 会被反复调用**（测试里尤其多），模块级 `_configured` 保证幂等；目录不可写时降级成「仅控制台」并 `root.warning`，不让应用起不来。

日志级别可用 `YTDL_LOG_LEVEL` 覆盖（默认 INFO）。路径通过 `/api/diagnostics.log_file` 暴露给界面。
