# API 文档

适用读者：前端开发者、后端维护者和需要调试接口的测试者。所有 schema 定义以 [backend/app/schemas.py](../backend/app/schemas.py) 为准，前端类型以 [frontend/src/types.ts](../frontend/src/types.ts) 为准。

## 机器可读规范

[openapi.yaml](openapi.yaml) 是本 API 的 OpenAPI 3.1.0 描述，覆盖全部 25 个操作、24 个 schema、复用的路径参数/错误响应组件与逐接口请求/响应示例。它由人工从 [main.py](../backend/app/main.py)（路由与状态码）与 [schemas.py](../backend/app/schemas.py)（字段与必填性）编写；**这两个文件仍是唯一事实来源**，规范只做投影。

可直接用于：

- Swagger Editor / Redoc / Postman 导入，生成交互式文档或客户端。
- 代码生成前的一致性比对——把生成的模型与 `schemas.py` 做字段级 diff。

校验方式（`openapi-spec-validator` 是文档工具，不进 `requirements.txt`）：

```bash
.venv/Scripts/python.exe -m pip install openapi-spec-validator
.venv/Scripts/python.exe -c "from openapi_spec_validator import validate; from openapi_spec_validator.readers import read_from_filename; validate(read_from_filename('docs/openapi.yaml')[0])"
```

改动路由或 schema 后必须同步 `openapi.yaml`；这一步已写进 [维护文档的变更 checklist](maintenance.md#变更-checklist)。

## 基本约定

- 后端应用由 [create_app](../backend/app/main.py#L41) 创建；API 默认绑定 `127.0.0.1:8000`，单端口模式下同时托管 `frontend/dist`。
- API 返回 JSON；`DELETE /api/jobs/{id}` 与四个本地打开接口成功时返回 `204`。
- 前端统一请求封装在 [request](../frontend/src/api.ts#L24)，非 2xx 响应会抛出 `ApiError`，并保留结构化 `detail`。
- Cookies 导入锁库错误使用结构化 `detail`，字段见 [BrowserCookieImportError.to_detail](../backend/app/browser_cookies.py#L46)。

## Endpoint

| 方法 | 路径 | 用途 | 主要模型 |
| --- | --- | --- | --- |
| `GET` | `/health` | 健康检查。 | `dict[str, bool]` |
| `GET` | `/api/diagnostics` | 依赖和运行状态诊断。 | [`DiagnosticsRead`](../backend/app/schemas.py#L199) |
| `POST` | `/api/analyze` | 解析单视频或 playlist。 | [`AnalyzeRequest`](../backend/app/schemas.py#L14)、[`AnalyzeResponse`](../backend/app/schemas.py#L43) |
| `POST` | `/api/jobs` | 创建下载任务并入队。 | [`CreateJobRequest`](../backend/app/schemas.py#L72)、[`JobRead`](../backend/app/schemas.py#L128) |
| `GET` | `/api/jobs` | 获取任务列表。 | `list[JobRead]` |
| `GET` | `/api/jobs/{job_id}` | 获取单个任务详情。 | `JobRead` |
| `POST` | `/api/jobs/batch` | 批量暂停、重启或删除。 | [`JobBatchActionRequest`](../backend/app/schemas.py#L81) |
| `POST` | `/api/jobs/{job_id}/cancel` | 取消任务。 | `JobRead` |
| `POST` | `/api/jobs/{job_id}/pause` | 暂停任务。 | `JobRead` |
| `POST` | `/api/jobs/{job_id}/restart` | 重启任务，可覆盖清晰度。 | [`RestartJobRequest`](../backend/app/schemas.py#L77) |
| `POST` | `/api/jobs/{job_id}/play` | 播放单视频任务已下载的视频文件。 | `204 No Content` |
| `POST` | `/api/jobs/{job_id}/open-folder` | 打开单视频任务已下载视频所在文件夹，或打开 playlist 任务文件夹。 | `204 No Content` |
| `POST` | `/api/jobs/{job_id}/items/{item_id}/restart` | 重启 playlist 中单个子视频，可覆盖清晰度。 | `RestartJobRequest` |
| `POST` | `/api/jobs/{job_id}/items/{item_id}/play` | 播放 playlist 子视频已下载的视频文件。 | `204 No Content` |
| `POST` | `/api/jobs/{job_id}/items/{item_id}/open-folder` | 打开 playlist 子视频已下载视频所在文件夹。 | `204 No Content` |
| `POST` | `/api/jobs/{job_id}/items/delete` | 删除 playlist 中一个或多个子视频任务，可选删除输出和 sidecar 文件。 | `DeleteJobItemsRequest`、`DeleteJobItemsResponse` |
| `DELETE` | `/api/jobs/{job_id}` | 删除任务，可选删除输出视频及字幕、metadata、缩略图、description 等相关文件。 | 查询参数 `delete_files` |
| `GET` | `/api/events` | SSE 任务事件流。 | `text/event-stream` |
| `GET` | `/api/settings` | 获取设置。 | [`SettingsRead`](../backend/app/schemas.py#L164) |
| `PUT` | `/api/settings` | 更新设置。 | [`SettingsUpdate`](../backend/app/schemas.py#L175) |
| `POST` | `/api/settings/download-dir/select` | 打开本机目录选择对话框。 | `SettingsRead` |
| `POST` | `/api/cookies` | 上传 cookies 文件。 | [`CookieStatus`](../backend/app/schemas.py#L186) |
| `POST` | `/api/cookies/from-browser` | 从浏览器导入 cookies。 | [`BrowserCookieImportRequest`](../backend/app/schemas.py#L194) |
| `DELETE` | `/api/cookies` | 清除本地 cookies。 | `CookieStatus` |

路由实现集中在 [main.py](../backend/app/main.py#L111)。`POST /api/jobs` 成功时返回 `201`，其余返回模型或 `204`。

## 关键请求模型

### AnalyzeRequest

`url` 是待解析链接；`cookies_enabled` 控制解析时是否使用本地 cookies。前端默认传 `true`，见 [analyzeUrl](../frontend/src/api.ts#L57)。

### DownloadOptions

字段定义见 [DownloadOptions](../backend/app/schemas.py#L56)。

| 字段 | 说明 |
| --- | --- |
| `mode` | `video_subtitles`、`video_only`、`subtitles_only`。 |
| `resolution` | 目标清晰度，例如 `1440p`、`1080p` 或 `best`；默认 `1440p`。 |
| `format_id` | 兼容旧请求保留，新 UI 不提供具体格式选择入口；有值时后端会强制该格式并要求 ffmpeg。 |
| `subtitle_languages` | 字幕语言列表；为空时按 `all` 处理。 |
| `subtitle_source` | `human`、`auto`、`both`；默认 `both`。前端会在已解析元数据缺少某类字幕时提交可用来源作为 fallback。 |
| `subtitle_format` | `best`、`srt`、`vtt`；默认 `best`；`best` 不写入 `subtitlesformat`。 |
| `playlist_items` | playlist 中选择的条目索引；单视频为 `null`。 |
| `write_metadata` | 是否写出 description 与 info.json。 |
| `write_thumbnail` | 是否写出缩略图。 |
| `skip_existing` | 默认 `true`，对应 yt-dlp `overwrites=False`；关闭后允许覆盖同名文件。 |
| `speed_limit_kbps` | 空值表示不限速；有值时启用 yt-dlp `ratelimit`（`kbps × 1024` 字节/秒）。 |
| `retries` | 下载重试次数，默认 10，范围 `0..20`。 |
| `notify_on_complete` | 请求模型接受该字段，但当前下载链路不消费它；不要据此期望系统通知。 |

### SettingsRead / SettingsUpdate

`GET /api/settings` 返回全局运行时设置。除下载目录、并发、默认清晰度和字幕语言外，还包含：

| 字段 | 说明 |
| --- | --- |
| `default_speed_limit_kbps` | 全局默认限速；`null` 表示不限速。 |
| `default_retries` | 全局默认下载重试次数，范围 `0..20`。 |
| `aria2c_connections` | aria2c 每文件的连接数，范围 `1..4`，默认 `2`。仅当 `YTDL_ARIA2C_ENABLED=true` 且 aria2c 可用时才会真正用于下载。 |

`PUT /api/settings` 采用局部更新语义：只有显式提交的字段才会被改写。`default_speed_limit_kbps` 通过 `model_fields_set` 判断，因此显式提交 `null` 表示取消限速；`aria2c_connections` 只更新设置与 `YtDlpService`，不会打断正在下载的任务。

更新并发会即时调整后台 worker 数量。更新限速或重试次数后，后端会同步 queued/running/paused 任务的 `DownloadOptions`；如果当前有视频正在下载，会取消当前 yt-dlp 实例、保留 `.part` 文件，并重新入队以断点续传方式应用新设置。保存后的值写入 `Setting` 表并在下次启动时恢复，见 [_apply_stored_settings](../backend/app/main.py#L576)。

`POST /api/settings/download-dir/select` 会在服务端弹出本机目录选择对话框；无图形环境时返回 `400`（`Folder dialog is unavailable in this environment.`），此时应改用 `PUT /api/settings` 直接提交 `download_dir`。

### CookieStatus

`POST /api/cookies` 与 `POST /api/cookies/from-browser` 返回 [`CookieStatus`](../backend/app/schemas.py#L186)：`enabled`、`filename`、`source`（`none` / `file` / `browser`）、`browser`、`imported_count`。诊断与设置接口只返回 `cookies_enabled` 布尔值，不返回 cookies 内容。

### DeleteJobItemsRequest

`item_ids` 是同一 job 下要删除的 `JobItem.id` 列表；`delete_files=false` 只删除任务记录，`delete_files=true` 同时删除每个子视频的输出文件和相关 sidecar。若删除的是父 playlist 的最后一个子视频，响应中的 `job_deleted=true` 且 `job=null`。

任务级批量删除继续使用 `POST /api/jobs/batch`。当 `action="delete"` 且 `delete_files=true` 时，后端会删除每个任务的输出文件和相关 sidecar；前端任务中心提供独立按钮，不依赖全局开关。

### Local Open Actions

播放视频和打开文件夹接口不接收路径参数，只使用数据库中的 `JobItem.output_path`、`Job.download_dir` 和任务下载目录内可按 YouTube id 关联到的文件。若 `output_path` 缺失或指向 yt-dlp 分离流中间文件名，后端会先尝试解析到合并后的最终文件；打开文件夹时若还没有最终视频，也可回退到任务下载目录。缺少输出路径、文件或目录不存在时返回 `409`；任务或子视频不存在时返回 `404`；系统打开器失败时返回 `400`。Windows 下打开文件夹会新开 Explorer 窗口，避免只在任务栏闪烁。

## 关键响应模型

### AnalyzeResponse

包含标题、是否 playlist、条目、格式列表、字幕列表、自动字幕列表和 ffmpeg 状态。格式和字幕映射逻辑见 [extract_metadata](../backend/app/ytdlp_service.py#L167)。

`entries` 只在 playlist 场景非空；`formats` 已过滤掉纯 storyboard/图片格式，见 [_map_formats](../backend/app/ytdlp_service.py#L741)。

### JobRead 与 JobItemRead

任务读模型由 [read_job](../backend/app/job_read_model.py#L12) 生成。任务级 `actual_resolution` 和 `actual_format` 是子视频聚合结果；单一值时显示具体值，playlist 不一致时显示 `混合分辨率` 或 `混合格式`，实现见 [job_read_model.py](../backend/app/job_read_model.py#L87)。`elapsed_seconds` 由 `started_at` 与 `finished_at`（未结束时取当前时间）计算，见 [_elapsed_seconds](../backend/app/job_read_model.py#L128)。

子视频级字段包括：

- `actual_width`、`actual_height`、`actual_format`：下载前预检测后写入，完成后校准。
- `downloaded_bytes`、`total_bytes`：下载前若 yt-dlp 能预估计划大小，会先写入 `total_bytes`；下载中持续校准；下载完成后保留最终大小。
- `output_path`：优先使用数据库记录；若为空但任务下载目录中存在 `... [youtube_id].mp4/.mkv/.webm` 等最终视频，读模型会投影该路径，便于任务中心恢复播放和打开文件夹能力。
- `requested_resolution`、`fallback_resolution`、`fallback_reason`、`resolution_fallback`：清晰度降级和重启建议。
- `speed`：运行中是瞬时速度；终态是平均速度。

本地文件操作 endpoint 不接收前端传入的任意路径，只使用数据库中的 `output_path` 或 `download_dir`。播放视频时后端会选择可用播放器；找不到可确认能解码当前格式的播放器时返回 `409`，错误内容包含当前格式和建议安装的播放器。打开文件夹时 Windows 会新开 Explorer 窗口并尽量置前。

### ResolutionFallback

字段定义见 [ResolutionFallback](../backend/app/schemas.py#L92)，消息构建逻辑见 [fallback_policy.py](../backend/app/fallback_policy.py#L10)。固定原因包括：

- `requested_resolution_missing`
- `source_below_720_only`
- `requested_resolution_unselectable`
- `media_stream_blocked`

## 错误语义

错误响应对齐 FastAPI 默认结构：`{"detail": ...}`。`detail` 通常是字符串，仅在浏览器 cookies 导入失败时是结构化对象（`code` / `browser` / `message` / `raw_detail`）。

| 状态码 | 触发条件 | 典型 `detail` |
| --- | --- | --- |
| `400` | URL 解析失败、yt-dlp 抛错、目录选择对话框不可用、系统打开器调用失败。 | yt-dlp 原始错误或本地化说明。 |
| `404` | 任务或子视频不存在；批量操作没有匹配任务；删除子视频时未命中。 | `Job not found.`、`Job item not found.`、`No matching jobs found.` |
| `409` | 浏览器 cookies 数据库被占用；单视频专用接口被用于合集；输出文件/目录尚不可用或缺失；找不到可解码播放器。 | `Edge 正在运行，cookies 数据库被锁定。…`、`合集任务请打开具体视频。`、`视频文件尚不可用。`、`找不到可确认能解码当前视频的播放器。…` |
| `422` | 请求体未通过 Pydantic 校验（缺字段、越界、未知枚举值）。 | FastAPI 校验错误数组。 |

对应实现见 [main.py](../backend/app/main.py#L428)（锁库错误映射为 `409`）与各路由内的 `HTTPException`。

## 任务状态

任务和子视频状态来自 [JobStatus](../backend/app/models.py#L12)。

| 状态 | 含义 |
| --- | --- |
| `queued` | 等待 worker 处理。 |
| `running` | 正在处理当前任务或子视频。 |
| `paused` | 用户暂停。 |
| `succeeded` | 全部下载或当前子视频完成。 |
| `failed` | 任务或子视频失败。 |
| `cancelled` | 取消。 |

## 诊断字段

`GET /api/diagnostics` 返回 `{"cookies_enabled": bool, "dependencies": {...}}`，其中 `dependencies` 是 `YtDlpService.get_dependency_status()` 与配置值的合并结果，见 [main.py](../backend/app/main.py#L115)。常见字段：

- `ffmpeg`、`ffprobe`
- `yt_dlp_version`
- `js_runtime`、`js_runtime_name`、`js_runtime_version`
- `impersonation_available`、`impersonation_targets`
- `po_token_provider_available`、`po_token_provider`、`po_token_provider_version`
- `youtube_po_token_configured`、`youtube_visitor_data_configured`、`youtube_po_browser_path_configured`
- `youtube_max_parallel_downloads`
- `anti403_http_chunk_size_mb`
- `throttled_rate_kbps`
- `aria2c_available`、`aria2c_enabled`、`aria2c_path`、`aria2c_connections`

诊断响应不返回 token 原文，只返回是否已配置的布尔值。
