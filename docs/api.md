# API 文档

适用读者：前端开发者、后端维护者和需要调试接口的测试者。所有 schema 定义以 [backend/app/contracts/schemas.py](../backend/app/contracts/schemas.py) 为准，前端类型以 [frontend/src/contracts/types.ts](../frontend/src/contracts/types.ts) 为准。

## 机器可读规范

[openapi.yaml](openapi.yaml) 是本 API 的 OpenAPI 3.1.0 描述，覆盖全部 41 个操作、48 个 schema、复用的路径参数/错误响应组件与逐接口请求/响应示例。它由人工从 [routers/](../backend/app/api/routers/__init__.py)（路由与状态码）与 [schemas.py](../backend/app/contracts/schemas.py)（字段与必填性）编写；**这两个文件仍是唯一事实来源**，规范只做投影。

可直接用于：

- Swagger Editor / Redoc / Postman 导入，生成交互式文档或客户端。
- 代码生成前的一致性比对——把生成的模型与 `schemas.py` 做字段级 diff。

校验方式（`openapi-spec-validator` 是文档工具，不进 `requirements.txt`）：

```bash
.venv/Scripts/python.exe -m pip install openapi-spec-validator
.venv/Scripts/python.exe -c "from openapi_spec_validator import validate; from openapi_spec_validator.readers import read_from_filename; validate(read_from_filename('docs/openapi.yaml')[0])"
```

改动路由或 schema 后必须同步 `openapi.yaml`；这一步已写进 [维护文档的变更 checklist](maintenance.md#变更-checklist)。

### 与运行时契约的漂移校验

上面那一步靠自觉。而**运行时契约**（`create_app().openapi()`，由 [schemas.py](../backend/app/contracts/schemas.py)
与各路由的响应模型生成）才是唯一「改代码就跟着变」的那一份 —— `openapi.yaml` 与
[types.ts](../frontend/src/contracts/types.ts) 都只是它的**手写副本**。两者有没有分叉，可以直接跑：

```bash
.venv/Scripts/python.exe scripts/check_api_contract.py
```

以运行时契约为单一来源，校验两件事：

- `frontend/src/contracts/types.ts`：每个接口的**字段名集合**与对应运行时 schema 完全相同（当前 33 个接口）；
- `docs/openapi.yaml`：**路径 × 方法**集合与 **paths 引用的 schema** 集合（当前 72 个条目）。

有意不跟随运行时命名的两类（文档用领域化的 `Error` / `CookieImportError`，运行时用 `HTTPValidationError`；
文件上传的请求体在文档里写成显式 `requestBody`，不暴露 `Body_upload_*` / `Body_bili_import_cookies_*`）
在脚本里登记为已知差异，只有**未登记**的差异才会失败。
注意它**只比字段名、不比类型**，覆盖边界与已知缺口见 [006](../ai/refactor/006-api-contract-drift.md)。

## 基本约定

- 后端应用由 [create_app](../backend/app/main.py#L40) 创建；API 默认绑定 `127.0.0.1:8000`（可用仓库根 `.env` 的 `YTDL_API_PORT` 或 `python -m app --port` 覆盖，见 [开发文档](development.md#端口被占用时)），单端口模式下同时托管 `frontend/dist`。
- API 返回 JSON；`DELETE /api/jobs/{id}` 与四个本地打开接口成功时返回 `204`。
- 前端统一请求封装在 [request](../frontend/src/shared/api.ts#L74)，非 2xx 响应会抛出 `ApiError`，并保留结构化 `detail`。
- Cookies 导入锁库错误使用结构化 `detail`，字段见 [BrowserCookieImportError.to_detail](../backend/app/host/browser_cookies.py#L58)。
- **所有时间字段是 UTC，且以 `Z` 结尾**（`started_at` / `finished_at` / `created_at` / `updated_at`）。
  SQLite 不存时区，读回来是 naive 的墙上时间，读模型补成 aware 之后才发出 —— 少了这个 `Z`，消费方 `Date.parse` 会把同一串数字按**本地**时间解析（ECMAScript 规定），于是每个时长都凭空多出一个时区偏移（本机 UTC+8 上实测 52 秒显示成 8 小时，见 [020](../ai/bug-fix/020-the-progress-clock-is-eight-hours-off.md)）。
  前端解析统一走 [parseServerTime](../frontend/src/shared/formatting.ts#L48)，不要直接 `Date.parse`。
- `/api/bilibili/*` 用的是**另一套**凭据（`data/bilibili/cookies.json`），与 `data/cookies.txt` 互不影响；接口只回传凭据的**名字与计数**，不回传值，也不回传掩码前缀。详见下面「B站 上传」。

## Endpoint

| 方法 | 路径 | 用途 | 主要模型 |
| --- | --- | --- | --- |
| `GET` | `/health` | 健康检查。 | `dict[str, bool]` |
| `GET` | `/api/diagnostics` | 依赖和运行状态诊断。 | [`DiagnosticsRead`](../backend/app/contracts/schemas.py#L329) |
| `POST` | `/api/diagnostics/runtime` | 清掉 JS 运行时探测缓存后重新自检，返回同一份诊断。 | [`DiagnosticsRead`](../backend/app/contracts/schemas.py#L329) |
| `POST` | `/api/proxy/test` | 检测代理连通性，可临时试一个**未保存**的地址。 | [`ProxyTestRequest`](../backend/app/contracts/schemas.py#L285)、[`ProxyTestRead`](../backend/app/contracts/schemas.py#L290) |
| `POST` | `/api/cookies/verify` | cookies 体检（离线格式/域名/鉴权项，可联网确认登录态）。 | [`CookieVerifyRequest`](../backend/app/contracts/schemas.py#L303)、[`CookieHealthRead`](../backend/app/contracts/schemas.py#L308) |
| `POST` | `/api/analyze` | 解析单视频或 playlist。 | [`AnalyzeRequest`](../backend/app/contracts/schemas.py#L71)、[`AnalyzeResponse`](../backend/app/contracts/schemas.py#L100) |
| `POST` | `/api/jobs` | 创建下载任务并入队。 | [`CreateJobRequest`](../backend/app/contracts/schemas.py#L132)、[`JobRead`](../backend/app/contracts/schemas.py#L199) |
| `GET` | `/api/jobs` | 获取任务列表。 | `list[JobRead]` |
| `GET` | `/api/jobs/{job_id}` | 获取单个任务详情。 | `JobRead` |
| `POST` | `/api/jobs/batch` | 批量暂停、重启或删除。 | [`JobBatchActionRequest`](../backend/app/contracts/schemas.py#L141) |
| `POST` | `/api/jobs/{job_id}/cancel` | 取消任务。 | `JobRead` |
| `POST` | `/api/jobs/{job_id}/pause` | 暂停任务。 | `JobRead` |
| `POST` | `/api/jobs/{job_id}/restart` | 重启任务，可覆盖清晰度。 | [`RestartJobRequest`](../backend/app/contracts/schemas.py#L137) |
| `POST` | `/api/jobs/{job_id}/play` | 播放单视频任务已下载的视频文件。 | `204 No Content` |
| `POST` | `/api/jobs/{job_id}/open-folder` | 打开单视频任务已下载视频所在文件夹，或打开 playlist 任务文件夹。 | `204 No Content` |
| `POST` | `/api/jobs/{job_id}/open-subtitle` | 用本机程序打开单视频任务下载到的字幕文件；缺省先请系统的「打开方式」（不可用时自动退回应用自带的对话框），`?mode=default` 才走文件关联。 | 查询参数 `mode`（`chooser` 缺省 / `default`） |
| `POST` | `/api/jobs/{job_id}/items/{item_id}/restart` | 重启 playlist 中单个子视频，可覆盖清晰度。 | `RestartJobRequest` |
| `POST` | `/api/jobs/{job_id}/items/{item_id}/play` | 播放 playlist 子视频已下载的视频文件。 | `204 No Content` |
| `POST` | `/api/jobs/{job_id}/items/{item_id}/open-folder` | 打开 playlist 子视频已下载视频所在文件夹。 | `204 No Content` |
| `POST` | `/api/jobs/{job_id}/items/{item_id}/open-subtitle` | 用本机程序打开 playlist 子视频下载到的字幕文件；缺省先请系统的「打开方式」（不可用时自动退回应用自带的对话框），`?mode=default` 才走文件关联。 | 查询参数 `mode` |
| `POST` | `/api/jobs/{job_id}/items/delete` | 删除 playlist 中一个或多个子视频任务，可选删除输出和 sidecar 文件。 | `DeleteJobItemsRequest`、`DeleteJobItemsResponse` |
| `DELETE` | `/api/jobs/{job_id}` | 删除任务，可选删除输出视频及字幕、metadata、缩略图、description 等相关文件。 | 查询参数 `delete_files` |
| `GET` | `/api/events` | SSE 任务事件流。 | `text/event-stream` |
| `GET` | `/api/settings` | 获取设置。 | [`SettingsRead`](../backend/app/contracts/schemas.py#L239) |
| `PUT` | `/api/settings` | 更新设置。 | [`SettingsUpdate`](../backend/app/contracts/schemas.py#L260) |
| `POST` | `/api/settings/download-dir/select` | 打开本机目录选择对话框。 | `SettingsRead` |
| `POST` | `/api/cookies` | 上传 cookies 文件。 | [`CookieStatus`](../backend/app/contracts/schemas.py#L272) |
| `POST` | `/api/cookies/from-browser` | 从浏览器导入 cookies。 | [`BrowserCookieImportRequest`](../backend/app/contracts/schemas.py#L280) |
| `DELETE` | `/api/cookies` | 清除本地 cookies。 | `CookieStatus` |
| `GET` | `/api/bilibili/status` | B站 投稿环境体检（工具 / ffmpeg / 登录态 / 缺项清单 / 分区表 / 硬限）。 | [`BiliStatusRead`](../backend/app/contracts/schemas.py#L380) |
| `POST` | `/api/bilibili/tools/repair` | 修复 WinGet `Links` 目录下 0 字节的 `biliup.exe` 空壳，然后重新体检。 | `BiliStatusRead` |
| `POST` | `/api/bilibili/login/qrcode` | 生成扫码登录二维码（返回 **SVG 文本**）。 | [`BiliQrcodeRead`](../backend/app/contracts/schemas.py#L394) |
| `GET` | `/api/bilibili/login/poll` | 轮询扫码状态；**只有 `confirmed` 才落盘登录态**。 | [`BiliLoginPollRead`](../backend/app/contracts/schemas.py#L403) |
| `POST` | `/api/bilibili/cookies` | 导入一份已有的 B站 登录文件（`multipart/form-data`，不校验格式）。 | `BiliStatusRead` |
| `DELETE` | `/api/bilibili/cookies` | 清除 B站 登录态；幂等，本来没有也返回体检结论。 | `BiliStatusRead` |
| `GET` | `/api/bilibili/prefill` | 预填上传表单：单视频给一组值，合集给「模板 + 逐条预览 + 超限定位」。 | [`BiliPrefillRead`](../backend/app/contracts/schemas.py#L464) |
| `GET` | `/api/bilibili/seasons` | 账号下**已存在**的合集，供投稿界面下拉选择。**未登录也返回 200**（空列表 + `error` 说明原因）。 | [`BiliSeasonListRead`](../backend/app/contracts/schemas.py#L530) |
| `GET` | `/api/bilibili/uploads` | 按任务读回上传状态（任务级徽标 + 条目级明细 + 合集记录）。 | [`BiliJobUploadsRead`](../backend/app/contracts/schemas.py#L612) |
| `POST` | `/api/bilibili/uploads` | 启动单视频投稿（可选归入某个合集）；返回**整个任务**的上传状态。 | [`BiliUploadCreate`](../backend/app/contracts/schemas.py#L498)、`BiliJobUploadsRead` |
| `GET` | `/api/bilibili/uploads/summary` | **所有有 B站 记录的任务**的徽标汇总（任务中心每行一个图标，逐个问就是 N 个请求）。 | [`BiliJobUploadsSummaryRead`](../backend/app/contracts/schemas.py#L627) |
| `POST` | `/api/bilibili/uploads/{upload_id}/retry` | 重试一条稿件；动作按终态三选一。 | [`BiliRetryRead`](../backend/app/contracts/schemas.py#L638) |
| `POST` | `/api/bilibili/collections` | 启动合集投稿：按模板逐条投稿，全部确认发布后自动归集。 | [`BiliCollectionCreate`](../backend/app/contracts/schemas.py#L539)、`BiliJobUploadsRead` |
| `POST` | `/api/bilibili/collections/{season_record_id}/group` | 重试合集归集；已有 `season_id` 时只补加视频，不重复建合集。 | [`BiliSeasonRetryRead`](../backend/app/contracts/schemas.py#L645) |

路由实现集中在 [routers/](../backend/app/api/routers/__init__.py)。`POST /api/jobs` 成功时返回 `201`，其余返回模型或 `204`。B站 那一组统一返回 JSON 读模型（没有 `204`）。

## 关键请求模型

### AnalyzeRequest

`url` 是待解析链接；`cookies_enabled` 控制解析时是否使用本地 cookies。前端默认传 `true`，见 [analyzeUrl](../frontend/src/shared/api.ts#L127)。

### DownloadOptions

字段定义见 [DownloadOptions](../backend/app/contracts/schemas.py#L113)。

| 字段 | 说明 |
| --- | --- |
| `mode` | `video_subtitles`、`video_only`、`subtitles_only`。 |
| `resolution` | 目标清晰度，只接受 `best` 或 `<高度>p`（例如 `1440p`、`1080p`），默认 `1440p`。取值会先**归一化**（去空白、转小写，`1080P` → `1080p`），其余写法一律 422 —— 它们没有唯一解释，猜一个就等于静默换画质（见 [026](../ai/bug-fix/026-a-resolution-that-silently-means-best.md)）。同一个校验也用在 `POST /api/jobs/{job_id}/restart` 的 `resolution` 与设置里的 `default_resolution` 上。 |
| `format_id` | 兼容旧请求保留，新 UI 不提供具体格式选择入口；有值时后端会强制该格式并要求 ffmpeg。 |
| `subtitle_languages` | 字幕语言列表。**只处理中文与英文**，其余语种在后端被忽略（粤语 `yue` 不算中文）。留空时由后端按字幕计划收敛成一条（按主语言挑、人工优先、无则降级 ASR、都没有则跳过），**不会**退化成 `["all"]`；用户显式给了值时以用户值为准，计划不改它。 |
| `subtitle_source` | `human`、`auto`、`both`；默认 `both`。前端会在已解析元数据缺少某类字幕时提交可用来源作为 fallback。 |
| `subtitle_format` | `best`、`srt`、`vtt`；默认 `best`；`best` 不写入 `subtitlesformat`。 |
| `playlist_items` | playlist 中选择的条目索引；单视频为 `null`。 |
| `write_metadata` | 是否写出 description 与 info.json。 |
| `write_thumbnail` | 是否写出缩略图。 |
| `skip_existing` | 默认 `true`，对应 yt-dlp `overwrites=False`；关闭后允许覆盖同名文件。 |
| `speed_limit_kbps` | 空值表示不限速；有值时启用 yt-dlp `ratelimit`（`kbps × 1024` 字节/秒）。 |
| `retries` | 下载重试次数，默认 10，范围 `0..20`。 |
| `notify_on_complete` | 这个任务跑完时**要不要**提醒，默认 `true`。提醒**用什么方式**（提示音 / 右下角浮层 / 两者 / 不提醒）由 `GET /api/settings` 的 `completion_notification` 决定；提醒在浏览器里发生，后端不参与发声。 |

### SettingsRead / SettingsUpdate

`GET /api/settings` 返回全局运行时设置。除下载目录、并发、默认清晰度和字幕语言外，还包含：

| 字段 | 说明 |
| --- | --- |
| `default_speed_limit_kbps` | 全局默认限速；`null` 表示不限速。 |
| `default_retries` | 全局默认下载重试次数，范围 `0..20`。 |
| `aria2c_connections` | aria2c 每文件的连接数，范围 `1..4`，默认 `2`（界面上叫「单视频并发下载数」）。仅当 `YTDL_ARIA2C_ENABLED=true` 且 aria2c 可用时才会真正用于下载，且它只是 profile 链里的后备——`default` 失败后才会轮到，不是每次下载都走。 |
| `completion_notification` | 任务跑到终态时怎么提醒：`off` / `sound` / `toast` / `both`，默认 `both`。后端只存这个偏好，提醒本身由前端在浏览器里做（提示音由 Web Audio 现场合成，浮层是 DOM）。非法取值（环境变量写错、落库值被改坏）一律收敛回 `both`，不会原样发给前端 —— 前端拿它比字符串，不匹配的表现就是「选了却不响」。 |

`PUT /api/settings` 采用局部更新语义：只有显式提交的字段才会被改写。`default_speed_limit_kbps` 通过 `model_fields_set` 判断，因此显式提交 `null` 表示取消限速；`aria2c_connections` 只更新设置与 `YtDlpService`，不会打断正在下载的任务。

更新并发会即时调整后台 worker 数量。更新限速或重试次数后，后端会同步 queued/running/paused 任务的 `DownloadOptions`；如果当前有视频正在下载，会取消当前 yt-dlp 实例、保留 `.part` 文件，并重新入队以断点续传方式应用新设置。保存后的值写入 `Setting` 表并在下次启动时恢复，见 [_apply_stored_settings](../backend/app/api/api_support.py#L149)。

`POST /api/settings/download-dir/select` 会在服务端弹出本机目录选择对话框；无图形环境时返回 `400`（`Folder dialog is unavailable in this environment.`），此时应改用 `PUT /api/settings` 直接提交 `download_dir`。

### ProxyTestRequest / CookieVerifyRequest

`POST /api/proxy/test` 的 `proxy` 语义与 `PUT /api/settings` 完全一致，但**不写库**：

| 值 | 行为 |
| --- | --- |
| 不传 / `null` | 用当前生效的解析结果（显式设置 → 系统代理 → 环境变量）去探测 |
| 代理地址字符串 | 只试这个地址 |
| `direct` / `none` / `off` / `-` | 这次探测强制直连 |

它不修改设置，也不影响正在下载的任务；界面上的「先试输入框里的地址」用的就是它。

`POST /api/cookies/verify` 的 `deep` 默认为 `true`：为 `true` 时会拿这份 cookies 真的请求一次
`https://www.youtube.com/`，读返回页面里的 `"LOGGED_IN"`；为 `false` 时只做离线体检。两者都不修改 cookies 文件。

### CookieStatus

`POST /api/cookies` 与 `POST /api/cookies/from-browser` 返回 [`CookieStatus`](../backend/app/contracts/schemas.py#L272)：`enabled`、`filename`、`source`（`none` / `file` / `browser`）、`browser`、`imported_count`。诊断与设置接口只返回 `cookies_enabled` 布尔值，不返回 cookies 内容。

### DeleteJobItemsRequest

`item_ids` 是同一 job 下要删除的 `JobItem.id` 列表；`delete_files=false` 只删除任务记录，`delete_files=true` 同时删除每个子视频的输出文件和相关 sidecar。若删除的是父 playlist 的最后一个子视频，响应中的 `job_deleted=true` 且 `job=null`。

任务级批量删除继续使用 `POST /api/jobs/batch`。当 `action="delete"` 且 `delete_files=true` 时，后端会删除每个任务的输出文件和相关 sidecar；前端任务中心提供独立按钮，不依赖全局开关。

### Local Open Actions

播放视频和打开文件夹接口不接收路径参数，只使用数据库中的 `JobItem.output_path`、`Job.download_dir` 和任务下载目录内可按 YouTube id 关联到的文件。若 `output_path` 缺失或指向 yt-dlp 分离流中间文件名，后端会先尝试解析到合并后的最终文件；打开文件夹时若还没有最终视频，也可回退到任务下载目录。缺少输出路径、文件或目录不存在时返回 `409`；任务或子视频不存在时返回 `404`；系统打开器失败时返回 `400`。Windows 下打开文件夹会新开 Explorer 窗口，避免只在任务栏闪烁。

打开字幕（`open-subtitle`）同样不收路径：字幕位置由 [existing_subtitle_file](../backend/app/download/output_paths.py#L184) 算——库里的 `subtitle_path` 优先（只验存在），否则看视频旁边的同名 `.srt` / `.vtt`。`mode` 只有两个取值，对应系统那边的两种 verb：

| `mode` | 实际动作 | 谁决定程序 |
| --- | --- | --- |
| `chooser`（**默认**，任务中心按钮唯一动作） | 先弹系统的「打开方式」（`SHOpenWithDialog` → `OpenAs_RunDLL`）；系统那套界面降级成一个提示框时，自动改用**应用自带**的对话框（列出系统登记的处理器 + 本机常见编辑器 + 「浏览…」） | 用户当场挑；想固定默认请到系统「默认应用」里设 |
| `default`（仅接口调用方，界面不再提供） | 走该后缀的**文件关联**；Windows 上若还没有关联程序，自动改弹上面那个对话框（不是报错） | 系统记录的那个程序 |

界面调用这两个接口时**不带 `mode`**：缺省就是「弹选择器」，那个缺省只写在服务端一处，前端不跟它各写一遍（曾经前端写 `?mode=…`、两处各记一份，改一处就会与另一处不一致）。后端**不接收、也不解析「用哪个程序」**：没有编辑器清单，也不存「上次用的查看器」。理由见 [technical.md](technical.md#打开字幕文件)。没有字幕文件时 `409`；`mode` 取别的值一律 `422`（多一个取值就是一条没人实现过的分支）。

## 关键响应模型

### AnalyzeResponse

包含标题、是否 playlist、条目、格式列表、字幕列表、自动字幕列表和 ffmpeg 状态。格式和字幕映射逻辑见 [extract_metadata](../backend/app/download/ytdlp_service.py#L357)。

`entries` 只在 playlist 场景非空；`formats` 已过滤掉纯 storyboard/图片格式，见 [_map_formats](../backend/app/download/ytdlp_service.py#L1155)。

### JobRead 与 JobItemRead

任务读模型由 [read_job](../backend/app/download/job_read_model.py#L12) 生成。任务级 `actual_resolution` 和 `actual_format` 是子视频聚合结果；单一值时显示具体值，playlist 不一致时显示 `混合分辨率` 或 `混合格式`，实现见 [job_read_model.py](../backend/app/download/job_read_model.py#L125)。`elapsed_seconds` 由 `started_at` 与 `finished_at`（未结束时取当前时间）计算，见 [elapsed_seconds](../backend/app/download/job_read_model.py#L166)。`notify_on_complete` 是「这个任务勾没勾完成后通知」的派生字段，从 `options_json` 读出，见 [_notify_on_complete](../backend/app/download/job_read_model.py#L44)：它必须走读模型，否则前端刷新一次页面就拿不到它（SSE 每条事件只是重拉 `/api/jobs`）。

子视频级字段包括：

- `actual_width`、`actual_height`、`actual_format`：下载前预检测后写入，完成后校准。
- `downloaded_bytes`、`total_bytes`：下载前若 yt-dlp 能预估计划大小，会先写入 `total_bytes`；下载中持续校准；下载完成后保留最终大小。
- `duration`：视频时长（秒）。**建任务时**就从解析结果的条目写入（`POST /api/jobs` 的条目本身带时长），所以任务一进队列就能显示，不必等预检；平台没给时长时为 `null`。`duration` 这一列是后补的，补列之前建的旧任务**重启一次**会在预检时补上（见 [_apply_download_preparation](../backend/app/download/job_manager.py#L1032)）。
- `output_path`：优先使用数据库记录；若为空但任务下载目录中存在 `... [youtube_id].mp4/.mkv/.webm` 等最终视频，读模型会投影该路径，便于任务中心恢复播放和打开文件夹能力。
- `requested_resolution`、`fallback_resolution`、`fallback_reason`、`resolution_fallback`：清晰度降级和重启建议。
- `speed`：运行中是瞬时速度；终态是平均速度。
- `subtitle_language`、`subtitle_source`：这一条下载到的字幕结论。**语言是族名**（`en` / `zh`，不是
  `zh-Hans` 这种轨代码）；`subtitle_source` 取 `human` / `auto` / `none` / `null`，其中
  **`null` 与 `none` 不是一回事** —— `null` 是这一轮还没有结论（`video_only` 或不需要降级的早退路径），
  `none` 是有结论（该视频没有可用的中英字幕）。落库判据是**磁盘上真的有字幕文件**，不是下载前的计划。
- `subtitle_path`：字幕文件的**实际路径**，没有就是 `null`。任务中心「打开字幕」按钮能不能点**只看它**
  —— 回显一条已经被删掉的旧记录，界面上就是一个点了必然 409 的按钮。同样由读模型算（记录值 → 视频旁边
  的同名字幕），前端不拼路径。

本地文件操作 endpoint 不接收前端传入的任意路径，只使用数据库中的 `output_path`、`subtitle_path` 或 `download_dir`。播放视频时后端会选择可用播放器；找不到可确认能解码当前格式的播放器时返回 `409`，错误内容包含当前格式和建议安装的播放器。打开文件夹时 Windows 会新开 Explorer 窗口并尽量置前。**打开字幕不选程序**：把文件交给系统，由文件关联或系统的「打开方式」决定。

### ProxyTestRead

一次探针的**原始证据**，不做二次加工：`ok`、`source`（`setting` / `system` / `environment` / `direct`）、
`proxy`（脱敏后；直连时为 `null`）、`probe_url`（默认 `https://www.youtube.com/robots.txt`）、
`http_status`、`elapsed_ms`、`bytes_read`、`error`（原始异常字符串）、
`summary`（一句人话结论）、`next_steps`（可执行建议数组）。

判定成功要求 `http_status == 200` 且读到非空响应体；失败一律带上 `next_steps`，不给「光秃秃的失败」。

注意「强制直连失败」是**可能的正常结果**：`source=direct` 时 summary 会明确写「如果本机直连本来就被拦，这是预期结果」。

### CookieHealthRead

cookies 体检结果。字段分三层：

- **文件层**：`present`、`path`、`filename`、`size_bytes`、`format_ok`、`format_note`（是否为 Tab 分隔 7 列的 Netscape 格式）。
- **内容层**：`cookie_count`、`domains`（域名 → 条数）、`youtube_domain_count`、`auth_cookie_names`、
  `missing_auth_cookie_names`、`anonymous_only`、`expired_count`。
- **结论层**：`verdict`（一句中文结论）、`next_steps`、`logged_in`（`true` / `false` / `null`）、
  `logged_in_detail`（原始证据，例如页面里出现了什么）、`checked_at`。

`youtube_domain_count == 0` 是最值得警惕的一档：文件里可能有 `SID`，但 `.google.com` 域的 cookie
**永远不会**发给 `www.youtube.com`，请求实则匿名。这也是必须联网校验 `logged_in` 的原因 ——
只看文件内容区分不出「登录态有效」与「cookie 还在但已失效」。

### ResolutionFallback

字段定义见 [ResolutionFallback](../backend/app/contracts/schemas.py#L152)，消息构建逻辑见 [fallback_policy.py](../backend/app/download/fallback_policy.py#L10)。固定原因包括：

- `requested_resolution_missing`
- `source_below_720_only`
- `requested_resolution_unselectable`
- `media_stream_blocked`

## B站 上传

这一组接口把**已经下载完成**的视频投稿到 B站。它与下载链路完全解耦：独立账号、独立凭据文件（`data/bilibili/cookies.json`，与 `/api/cookies` 的 YouTube cookies 无关）、独立队列（`BiliManager` 的单 worker）与独立状态。

三条不写在 schema 里、但会直接影响调用方判断的语义：

1. **投稿是异步的**。`POST /api/bilibili/uploads` 与 `/collections` 只负责入队并立刻返回当前读模型，进度要靠 `GET /api/bilibili/uploads`、`/summary` 或 SSE 事件流回读。
2. **命令返回码 0 不等于已发布**。后端在命令退出后回读账号稿件列表对账（按标题前缀匹配，两侧空白归一），对不上就是 `unverified`（待核对）——它既不是成功也不是失败，界面文案与推荐动作都单独一套（`UPLOAD_UNVERIFIED`）。
3. **界面状态由后端聚合**。任务级那一档徽标（`none` / `uploading` / `uploaded` / `unverified` / `partial` / `failed`）由 [derive_badge](../backend/app/bilibili/bili_read_model.py#L60) 现场算出，前端只渲染，不自行推导。

投放限制在服务端统一校验，超限**只报错不截断**（静默截断会让用户以为发出去的是完整内容），合集会把问题定位到第几条。

| 字段 | 上限 |
| --- | --- |
| `title` | 80 字符 |
| `desc` | 250 字符 |
| `tags` | 逗号分隔总长 < 200，最多 10 个，**单个 ≤ 20** |
| `comment` | 1000 字符 |
| `copyright` | 只能取 `1`（自制）或 `2`（转载）。**默认 `2`** |
| `source` | `copyright=2` 时**必填**；`copyright=1` 时**不提交** |

`copyright` 与 `source` 不在上面这张「长度上限」表里，但同样是提交前的拦截项，判据在 [validate](../backend/app/bilibili/bili_meta.py#L354)。三点值得调用方注意：

- **读不出合法 `copyright` 时回退 `2`（转载）而不是 `1`**。标成自制是替用户声明一件他没说过的事；标成转载最多让他改一下。
- **自制稿即便 `source` 带值也不会出现在命令行里**。判据只有一个函数 [effective_source](../backend/app/bilibili/bili_meta.py#L205) —— 若在别处再写一遍「要不要带来源」，落库快照与实发参数迟早对不上。
- **合集模板上 `source` 恒为空**，逐条来源由服务端渲染时各取该条自己的来源链接。模板里回填第一条的值会让人误以为「所有条目都用它」，所以刻意不回填。

历史上已入库的稿件，`db._ensure_columns` 补的 `copyright` 是 `1`（那是改动之前的实际行为），与产品默认值 `2` 不同 —— 见 [BiliUpload](../backend/app/contracts/models.py#L132) 的注释。

### 归集方式（`group_mode` / `season_id` / `season_title`）

**单视频与合集两条投稿路径共用同一组字段与同一份校验**（[validate_group_choice](../backend/app/bilibili/bili_meta.py#L493)），
区别只在默认值：

| 字段 | 说明 |
| --- | --- |
| `group_mode` | `none` 不归集 / `existing` 加进 `season_id` 指定的已有合集 / `new` 用 `season_title` 新建一个。 |
| `season_id` | `group_mode=existing` 时必填。**不校验它是否真存在** —— 那是归集阶段的网络动作，提交前查一次会多一次往返，且账号可能在这之后被登出。 |
| `season_title` | 只在 `new` 时用得上；留空由路由用任务名兜底（`season_title or job.title`）。 |

**默认值按路径不同，而且方向是反的**，这是改动前后行为都必须保持的结果：

- `POST /api/bilibili/uploads`（单视频）默认 `none`。改动前单视频**从不进任何合集**，
  默认为 `new` 会让此后每条单视频投稿都在账号里多出一个只含它自己的合集。
- `POST /api/bilibili/collections`（合集）默认 `new`。改动前就是每次投完自动新建，
  改成别的会让已有的合集投稿突然失去归集。

两个常量分别在 [DEFAULT_GROUP_MODE](../backend/app/bilibili/bili_meta.py#L97) 与
[DEFAULT_SINGLE_GROUP_MODE](../backend/app/bilibili/bili_meta.py#L98)，
[normalize_group_mode](../backend/app/bilibili/bili_meta.py#L102) 的回退值是**参数**而不是写死的 ——
传错的表现是「升级后一批投稿悄悄多出/失去合集」，不会有任何报错。

三条调用方容易踩到的语义：

- **`group_mode=none` 在两条路径上的落库行为不同**。合集那边有一条归集记录，会落成终态
  `skipped`（界面上要有一句「未归集」可显示）；单视频那边**一条记录都不建**，
  因为单视频的默认恰好就是 `none`，建记录等于让每个单视频任务都多出一行噪音。
  两条路径都**会把上一轮留下的 `pending` 记录停掉**，否则 `_maybe_group` 照样自动归集。
- **`new` 时交付的 `season_id` 必须为空**。预置任何值（哪怕是对的 0）都会让归集阶段跳过
  建合集，然后对着一个不存在的编号加视频。`season_id` 只在 `existing` 时才有意义。
- **校验看的是原始输入**。路由先校验再归一：顺序反过来的话，非法值已被修成合法值，
  校验永远看不到它，一个手写坏的 `group_mode` 会被静默当成某个合法值执行。

`GET /api/bilibili/prefill` 会返回 `default_group_mode`，界面直接用它做初始选中项 ——
默认值由服务端算，前端不再按 `kind` 推一遍（推两份迟早会漂移）。

### BiliUploadRead 与 BiliJobUploadsRead

前端真正消费的是 [BiliJobUploadsRead](../backend/app/contracts/schemas.py#L612)（任务级读模型）与 [BiliJobUploadsSummaryRead](../backend/app/contracts/schemas.py#L627)（全部任务的徽标汇总）。`uploads` 按条目在任务中的位置排序，对不上任何条目的记录排在最后。

条目级 `status` 是后端自己的生命周期，`badge` 是界面用的粗档位：

| `status` | 含义 | 对应 `badge` |
| --- | --- | --- |
| `queued` | 已入队，等 worker。 | `uploading` |
| `uploading` | 投稿命令在跑。 | `uploading` |
| `submitted` | 命令成功，还在对账。 | `uploading` |
| `published` | 平台稿件列表已确认存在。 | `uploaded` |
| `reviewing` | 平台侧「等待审核 / 排队审核 / 转码中」。 | `uploading` |
| `rejected` | 平台侧审核不通过或已被锁定。 | `failed` |
| `unverified` | 命令成功，但列表里暂时查不到。 | `unverified` |
| `failed` | 命令失败或前置条件不满足。 | `failed` |

`verified_state` / `verified_note` 记录**对账结论本身**（平台状态码与它的人话），`error` / `advice` 放**给用户看的那两句**。注意 `unverified` 也会填 `error` / `advice`（「已提交，但还没在 B 站看到它」+「列表有延迟，稍后重新核对」）—— 措辞与 `failed` 刻意不同：前者是「我们还不知道」，后者是「这一步没成」。把两者都说成失败，用户会去重投一条**已经投上去**的稿件。

### 字幕（CC）

投稿成功后，下载阶段落下的字幕会作为**可关闭的 CC 字幕**一并提交（字幕轨，不是硬字幕；观众可在播放器里关掉）。
三个字段挂在 `BiliUploadRead` 上，**与 `status` 完全分离**：

| 字段 | 取值 | 说明 |
| --- | --- | --- |
| `subtitle_state` | `posted` / `skipped` / `failed`，默认 `skipped` | 字幕这一步自己的结论。**不参与徽标聚合**，也绝不把 `published` 翻成 `failed`。`skipped` 只表示「确实没有可传的字幕」；「有字幕但挂不上」（例如稿件还在审核、拿不到分P编号）记 `failed` |
| `subtitle_lan` | 平台侧实际用的标识（`zh` / `en` / `zh-Hant`…），未传时 `""` | **不是** `zh-CN` / `en-US` —— 平台不接受这两个值（实测 `code=79011 不合法的语言`），也不接受 `zh-Hans` / `en-GB`。白名单只有 `zh` / `en` / `zh-Hant` / `zh-TW` / `zh-HK` / `ja` / `ko` / `yue` / `fr` |
| `subtitle_message` | 恒非空（成功也写一句） | 给用户看的那句：「没有已下载的字幕，跳过字幕上传」/「CC 字幕已上传（英文），观众可在播放器里开关」/「稿件已提交（reviewing），但平台还没给出分P编号…」/ 失败原因 |

提交前，`GET /api/bilibili/prefill` 的每条 `BiliPrefillItem.subtitle` 会告诉界面**这一条会带什么字幕**
（`en · 人工` / `zh · 自动生成` / `无字幕` / 空串）。这句文案由服务端
[`subtitle_summary`](../backend/app/download/subtitle_plan.py#L92) 拼好 —— 前端自己拼一遍就会出现
「界面写自动生成、后端写 ASR」两套说法。

已发布/审核中的稿件，只要字幕**失败过**、或者**有字幕文件却还没 `posted`**，重试就走
**只重传字幕**（`action=reverify`），**不会**为了字幕重投一次视频。回执按实际结论给：
补上了是「已补传字幕」，仍失败是「字幕仍然没传上去：<原因>」（稿件还在审核时补传本来就可能再失败）。
补传对同一语言轨是**覆盖**，不是新增。

## 错误语义

错误响应对齐 FastAPI 默认结构：`{"detail": ...}`。`detail` 通常是字符串，仅在浏览器 cookies 导入失败时是结构化对象（`code` / `browser` / `message` / `raw_detail`）。

| 状态码 | 触发条件 | 典型 `detail` |
| --- | --- | --- |
| `400` | URL 解析失败、yt-dlp 抛错、目录选择对话框不可用、系统打开器调用失败；B站 投稿的元数据/评论超过平台硬限，或合集可投稿的视频不足 2 条。 | yt-dlp 原始错误或本地化说明；`标题太长（87 > 80）`、`第 3 条：标题太长（92 > 80）`。 |
| `404` | 任务或子视频不存在；批量操作没有匹配任务；删除子视频时未命中；B站 的上传/合集记录不存在。 | `Job not found.`、`Job item not found.`、`No matching jobs found.` |
| `409` | 浏览器 cookies 数据库被占用；单视频专用接口被用于合集；输出文件/目录尚不可用或缺失；找不到可解码播放器；**B站 投稿前置条件不满足**（视频还没下载好 / 投稿工具不可用 / 未登录）。 | `Edge 正在运行，cookies 数据库被锁定。…`、`合集任务请打开具体视频。`、`视频文件尚不可用。`、`找不到可确认能解码当前视频的播放器。…`、`这个视频还没有下载好，没有可以上传的文件`、`还有 2 个视频没有下载好：第一集、第二集`。 |
| `422` | 请求体未通过 Pydantic 校验（缺字段、越界、未知枚举值）。 | FastAPI 校验错误数组。 |

`409` 用在这里是刻意的：B站 投稿的前置条件全部是「现在还不满足、但可以补救」的状态（等下载完、装工具、登录），因此做成**请求被拒**而不是「建了个任务然后失败」—— 后者会把一次可解释的拒绝变成一条要用户去清理的失败记录。

对应实现见 [cookie_import_status_code](../backend/app/api/api_support.py#L137)（锁库错误映射为 `409`）与各路由内的 `HTTPException`。

## 任务状态

任务和子视频状态来自 [JobStatus](../backend/app/contracts/models.py#L12)。

| 状态 | 含义 |
| --- | --- |
| `queued` | 等待 worker 处理。 |
| `running` | 正在处理当前任务或子视频。 |
| `paused` | 用户暂停。 |
| `succeeded` | 全部下载或当前子视频完成。 |
| `failed` | 任务或子视频失败。 |
| `cancelled` | 取消。 |

## 诊断字段

`GET /api/diagnostics` 返回 `{"cookies_enabled": bool, "dependencies": {...}, "log_file": str|null, "sanitized_environment": [...]}`，其中 `dependencies` 是 `YtDlpService.get_dependency_status()` 与配置值的合并结果，见 [main.py](../backend/app/api/routers/diagnostics.py#L30)。常见字段：

- `ffmpeg`、`ffprobe`
- `yt_dlp_version`
- `js_runtime`、`js_runtime_name`、`js_runtime_version`
- `js_runtime_error`、`js_runtime_candidates_rejected`：**「检测到 node 却解不出 n challenge」唯一能查清楚的地方**。
  前者是候选运行时的原始报错（例如 `ERR_ACCESS_DENIED`），后者是逐个候选被拒的原因。
- `proxy`、`proxy_source`、`proxy_writes_ydl_option`、`system_proxy`、`environment_proxy`（`user:pass@` 已脱敏）
- `impersonation_available`、`impersonation_targets`
- `po_token_provider_available`、`po_token_provider`、`po_token_provider_version`
- `youtube_po_token_configured`、`youtube_visitor_data_configured`、`youtube_po_browser_path_configured`
- `youtube_max_parallel_downloads`
- `anti403_http_chunk_size_mb`
- `throttled_rate_kbps`
- `aria2c_available`、`aria2c_enabled`、`aria2c_path`、`aria2c_connections`

顶层另外两个字段：

| 字段 | 说明 |
| --- | --- |
| `log_file` | 应用正在写入的日志文件绝对路径；目录不可写时为 `null`（此时只有控制台日志）。界面把它做成可一键复制的「日志文件」。 |
| `sanitized_environment` | 启动时被摘掉的、会打坏 JS 运行时的宿主环境变量，每项为 `{name, value, reason}`。**它是一份动作记录**：应用不会静默修改环境。 |

`POST /api/diagnostics/runtime` 返回同一结构，但会先清掉 JS 运行时的探测缓存重新跑一遍，
用于「刚装完 Node/Deno，不想重启应用」的场景。

诊断响应不返回 token 原文，不返回 cookies 内容，只返回是否已配置的布尔值；日志文件内容也不经 API 暴露。
