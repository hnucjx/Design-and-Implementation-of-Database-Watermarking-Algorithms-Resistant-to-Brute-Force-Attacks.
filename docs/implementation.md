# 实现文档

适用读者：需要修改代码或排查行为的后端、前端维护者。架构层面的说明见 [架构设计](architecture.md)。

## 后端入口

FastAPI 应用由 [create_app](../backend/app/main.py#L37) 创建，启动时：

- 创建配置和目录。
- 初始化 SQLite engine 和表结构。
- 创建 `EventBroker`。
- 创建 `YtDlpService` 并注入 PO token、chunk、throttled rate、aria2c 等配置。
- 创建 `JobManager` 并在 lifespan 中启动/停止 worker。

路由函数保留 HTTP 入口、依赖注入和错误转换；任务序列化逻辑委托给 [job_read_model.py](../backend/app/job_read_model.py#L10)。

## 数据模型

核心持久化模型见 [models.py](../backend/app/models.py#L27)。

| 表 | 作用 |
| --- | --- |
| `Setting` | 保存下载目录、并发、默认清晰度和字幕语言等用户设置。 |
| `Job` | 保存任务级 URL、标题、状态、进度、错误、下载目录和聚合计数。 |
| `JobItem` | 保存 playlist 子视频或单视频的实际进度、输出文件、分辨率、格式、错误和降级原因。 |
| `JobEvent` | 保存任务事件，配合 SSE 推送。 |

数据库初始化调用 [init_db](../backend/app/db.py#L16)。为了兼容旧数据库，[_ensure_columns](../backend/app/db.py#L21) 会补齐新增列。

## 任务调度

[JobManager](../backend/app/job_manager.py#L32) 负责队列、worker、暂停、重启、删除、任务状态和事件发布。

关键流程：

1. `POST /api/jobs` 写入 `Job` 和 `JobItem`，然后调用 [enqueue](../backend/app/job_manager.py#L75)。
2. worker 从队列取出 job id，调用 [_run_job_sync](../backend/app/job_manager.py#L278)。
3. `_run_job_sync` 按 `JobItem.index` 顺序执行待处理子项。
4. 单项由 [_run_item](../backend/app/job_manager.py#L324) 处理，负责预检测、下载、进度 hook、错误分类和终态写入。
5. 全部子项处理后调用 [_finish_job](../backend/app/job_manager.py#L521) 聚合任务终态。

暂停和重启会重置运行中字段，但保留可重新执行的任务记录，见 [restart](../backend/app/job_manager.py#L116) 和 [restart_item](../backend/app/job_manager.py#L166)。Playlist 子视频删除由 `delete_items()` 处理：删除指定 `JobItem` 后刷新父任务聚合状态；如果删除最后一个子视频，父任务也会被删除。

## yt-dlp 封装

[YtDlpService](../backend/app/ytdlp_service.py#L84) 是 yt-dlp 的边界层。它负责：

- 解析元数据：[extract_metadata](../backend/app/ytdlp_service.py#L161)。
- 下载前选择计划格式：[prepare_download](../backend/app/ytdlp_service.py#L198)。
- 构建下载参数：[build_download_options](../backend/app/ytdlp_service.py#L231)。
- 同清晰度 profile 重试：[download](../backend/app/ytdlp_service.py#L312)。
- 依赖诊断：[get_dependency_status](../backend/app/ytdlp_service.py#L110)。
- 错误分类：cookies、403、连接重置和格式不可用。

`YtDlpService` 不把任意 yt-dlp 参数暴露给 API，只接受项目定义的 `DownloadOptions`。

## 清晰度与降级

格式选择和分辨率工具位于 [ytdlp_formats.py](../backend/app/ytdlp_formats.py)。降级消息集中在 [fallback_policy.py](../backend/app/fallback_policy.py)。

下载前的两类自动处理：

- [_options_for_available_resolution](../backend/app/job_manager.py#L608)：源视频没有目标高度时，选择合规的较低清晰度。
- [_prepare_download](../backend/app/job_manager.py#L649)：目标高度存在但 selector 选不出可下载组合时，尝试 720p 或更高降级。

媒体流 403/连接重置只标注 `media_stream_blocked` 并给重启建议，不自动降清晰度重下，相关逻辑见 [_annotate_media_stream_fallback](../backend/app/job_manager.py#L705)。

## 进度与平均速度

yt-dlp 对分离视频/音频流会多次发送 progress payload。为避免 UI 在视频流 100% 后音频流从 0% 开始造成进度回退，后端使用 [DownloadProgressAggregator](../backend/app/download_progress.py#L21) 聚合多子流。

视频大小复用 `JobItem.total_bytes`：下载前由 `prepare_download()` 从所选格式的 `filesize/filesize_approx` 写入，下载中由 progress payload 校准，下载完成后继续保留，前端在任务行和 playlist 子视频行展示。

平均速度由 [TransferStats](../backend/app/transfer_stats.py#L5) 根据聚合下载字节和时间计算。运行中 `speed` 是 yt-dlp 当前瞬时速度，终态 `speed` 是平均速度，终态聚合见 [_terminal_job_speed](../backend/app/job_manager.py#L585)。

## 读模型

API 返回不直接暴露 SQLModel，而由 [read_job](../backend/app/job_read_model.py#L10) 生成：

- 计算任务级实际分辨率和格式。
- 将多个子项不同结果聚合为 `混合分辨率` 或 `混合格式`。
- 为单视频失败透传具体错误。
- 计算 `elapsed_seconds`。
- 调用 `build_resolution_fallback()` 生成用户可读消息和重启建议。

## 前端实现

前端 API 调用集中在 [api.ts](../frontend/src/api.ts#L42)。共享类型集中在 [types.ts](../frontend/src/types.ts)。任务中心展示组件是 [JobQueue](../frontend/src/components/JobQueue.tsx#L13)。

辅助函数职责：

- [formatting.ts](../frontend/src/formatting.ts)：时长、百分比、日期、文件大小、分辨率和速度格式化。
- [quality.ts](../frontend/src/quality.ts)：清晰度选项、可用高度、降级按钮标签和选中清晰度显示。

前端不负责构造 yt-dlp selector，只提交 `DownloadOptions`，由后端决定实际格式。下载选项面板会根据解析结果展示字幕来源和字幕格式；当用户指定的字幕来源在当前视频中缺失但另一种来源存在时，前端提交前会 fallback 到可用来源。

任务中心删除入口分为任务级和 playlist 子视频级：任务级调用 `DELETE /api/jobs/{job_id}`，子视频级调用 `POST /api/jobs/{job_id}/items/delete`。删除文件入口统一在发请求前用确认弹窗保护。删除时如果 `JobItem.output_path` 为空，后端会在任务下载目录中按 YouTube id 发现输出视频、字幕、metadata 和 `.part` 文件。

任务中心播放和打开文件夹入口调用后端受控本机打开 API：单视频任务调用 `POST /api/jobs/{job_id}/play` 与 `POST /api/jobs/{job_id}/open-folder`，playlist 子视频调用对应的 item endpoint，playlist 任务行用同一个 open-folder endpoint 打开 `Job.download_dir`。后端只根据数据库中的 `output_path`、`download_dir` 或按 YouTube id 在下载目录中发现的本地文件打开目标，不接受前端传入任意路径。

`output_path` 可能来自 yt-dlp 分离音视频流的中间文件名，例如 `title [id].f137.mp4` 或 `title [id].f140.m4a`。下载完成时和本地打开前，后端会通过 [output_paths.py](../backend/app/output_paths.py) 解析到合并后的最终文件，例如 `title [id].mp4`，避免中间文件被合并删除后误报“视频文件不存在”。读模型同样会在 `output_path` 为空时尝试发现 `... [id].mp4/.webm/.mkv`，让历史任务恢复播放和文件夹入口。

本机打开器位于 [system_open.py](../backend/app/system_open.py)。打开目录时显式调用 `explorer.exe /n, <folder>` 并尽量把窗口置前，减少已有窗口只在任务栏闪烁但没有前置的情况。播放视频不再盲目使用默认文件关联，而是优先选择 VLC、mpv、PotPlayer、MPC、IINA 等可确认解码能力更强的播放器；常见 MP4 可回退到 Windows Media Player。找不到合适播放器时返回 `409`，错误内容包含当前格式和建议安装的播放器。

播放和打开文件夹这类本地文件操作失败时，前端在 [JobQueue](../frontend/src/components/JobQueue.tsx) 的对应任务行或子视频行附近显示错误，而不是只放在页面顶部的全局提示区。

任务中心复制链接入口是纯前端行为，使用 `navigator.clipboard.writeText()` 复制 `Job.url` 或 `JobItem.source_url`，成功后按钮短暂显示“已复制”。

任务中心跳转 YouTube 页面同样是纯前端行为，使用 `window.open(sourceUrl, "_blank", "noopener,noreferrer")` 打开 `Job.url` 或 `JobItem.source_url`，不经过后端本地文件打开接口。

## 日志安全

下载失败日志会记录 job id、item id、标题、清晰度、错误分类和清洗后的错误文本，见 [_log_item_failure](../backend/app/job_manager.py#L776)。日志清洗工具位于 [log_safety.py](../backend/app/log_safety.py#L11)，用于避免敏感 query、cookies 或 token 进入日志。
