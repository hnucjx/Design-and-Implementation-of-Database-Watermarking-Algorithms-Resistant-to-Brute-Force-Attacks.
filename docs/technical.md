# 技术文档

适用读者：需要理解下载策略、清晰度、cookies、PO token 和稳定性排障的开发者与高级用户。

## 清晰度与格式选择

用户只选择清晰度；后端自动选择格式。选择器实现位于 [ytdlp_formats.py](../backend/app/ytdlp_formats.py#L9)。

数字清晰度如 `1080p` 的优先级：

1. 同高度 `mp4`/H.264 视频 + `m4a`/AAC 音频。
2. 同高度 `mp4` 视频 + `m4a` 音频。
3. 同高度任意 video + audio。
4. 同高度 HLS 单文件。
5. 同高度单文件。

`safari_hls` profile 会把同高度 HLS 单文件放到最前，见 [format_selector](../backend/app/ytdlp_formats.py#L25)。如果 ffmpeg 可用，后端允许 video+audio 合并并设置 `merge_output_format=mp4`，见 [build_download_options](../backend/app/ytdlp_service.py#L291)。

## 下载前预检测

在实际下载前，`JobManager` 会调用 [prepare_download](../backend/app/ytdlp_service.py#L198) 让 yt-dlp 按当前 selector 选择计划下载格式。结果通过 [_apply_download_preparation](../backend/app/job_manager.py#L682) 写入：

- `actual_width`
- `actual_height`
- `actual_format`
- `total_bytes`，当 yt-dlp 能从所选格式得到 `filesize` 或 `filesize_approx` 时写入

因此任务中心可以在下载开始后尽早显示计划分辨率、格式和视频大小。下载完成后仍会根据 progress payload 或输出文件进行校准，避免预检测与最终文件不一致。

## 字幕来源与格式

默认 `DownloadOptions` 使用 `resolution="1440p"`、`subtitle_source="both"`、`subtitle_format="best"`。当用户选择“两者都要”时，yt-dlp 会同时启用人工字幕和自动字幕；若其中一种不存在，只会写出另一种可用字幕。前端在已解析元数据中发现用户指定的人工/自动字幕来源不可用时，会把提交给后端的 `subtitle_source` fallback 到另一种可用来源，并在下载选项面板显示“来源”和“格式”；若两类字幕都没有，则显示“无字幕”。

## 分辨率降级原因

降级原因常量在 [fallback_policy.py](../backend/app/fallback_policy.py#L4)。降级策略分为下载前自动处理和媒体流失败提示两类。

| 原因 | 含义 | 是否自动降级 |
| --- | --- | --- |
| `requested_resolution_missing` | 源视频本来没有用户选择的清晰度。 | 是，降到低于目标且不低于 720p 的最高可用清晰度。 |
| `source_below_720_only` | 源视频没有任何 720p 或更高清晰度。 | 是，允许降到源视频最高可用低清晰度。 |
| `requested_resolution_unselectable` | 元数据显示目标清晰度存在，但当前 selector 选不出可下载组合。 | 是，只降到 720p 或更高的安全清晰度。 |
| `media_stream_blocked` | 目标清晰度媒体流被 403、连接重置、超时或 TLS 错误阻断。 | 否，只提供较低清晰度重启建议。 |

720p 底线由 [DEFAULT_MIN_AUTO_FALLBACK_HEIGHT](../backend/app/ytdlp_formats.py#L6) 定义。自动降级计算见 [suggest_lower_resolution](../backend/app/ytdlp_formats.py#L134)。

## 稳定下载策略

默认策略是稳定优先，而不是并发优先。核心参数在 [ytdlp_service.py](../backend/app/ytdlp_service.py#L40) 和 [build_download_options](../backend/app/ytdlp_service.py#L240)：

- `continuedl=True`，保留 `.part` 断点续传。
- `fragment_retries=20`、`file_access_retries=5`、`extractor_retries=5`。
- `socket_timeout=30`。
- `concurrent_fragment_downloads=1`。
- `http_chunk_size=16 MiB` 默认值。
- `throttledratelimit=64 KiB/s` 默认值，用于低速重取 fresh media URL，不是限速。
- 默认 worker 并发为 5；若追求稳定，可在设置面板或 `YTDL_YOUTUBE_MAX_PARALLEL_DOWNLOADS=1` 中降为 1。配置见 [default_download_concurrency](../backend/app/config.py#L12)。

YouTube 媒体流 403 或连接中断时，`YtDlpService.download()` 会在同一清晰度下依次尝试 profile，见 [download](../backend/app/ytdlp_service.py#L312)：

1. `default`
2. `default_aria2c`，仅当显式启用 aria2c 且可执行文件存在
3. `mweb_pot_chrome`
4. `safari_hls`
5. `chrome_default`

媒体流阻断判断见 [is_media_stream_blocked_error](../backend/app/ytdlp_service.py#L455)。这类失败不会在下载中途自动降清晰度重下，任务中心会给出中文原因和可重启建议。

## Cookies 与登录态

Cookies 用于合法账号态、年龄确认或 bot 校验场景。解析阶段逻辑见 [_extract_metadata_with_cookies](../backend/app/main.py#L83)，下载阶段刷新逻辑见 [_download_with_cookie_refresh](../backend/app/job_manager.py#L464)。

浏览器导入器只保存 YouTube/Google 相关 cookies，过滤规则见 [YOUTUBE_COOKIE_DOMAIN_SUFFIXES](../backend/app/browser_cookies.py#L20)。Edge 锁库和 DPAPI fallback 处理见 [browser_cookies.py](../backend/app/browser_cookies.py#L117)。

## PO token 与浏览器 impersonation

依赖 `yt-dlp[default,curl-cffi]` 提供浏览器/TLS impersonation，依赖 `yt-dlp-getpot-wpc` 提供 YouTube PO-token provider，配置在 [backend/pyproject.toml](../backend/pyproject.toml)。

相关环境变量：

- `YTDL_YOUTUBE_PO_TOKEN`
- `YTDL_YOUTUBE_VISITOR_DATA`
- `YTDL_YOUTUBE_PO_BROWSER_PATH`

这些值只传给 yt-dlp extractor 或 provider，不在诊断接口中回显原文。诊断只返回是否已配置，见 [get_dependency_status](../backend/app/ytdlp_service.py#L110)。

## aria2c fallback

`aria2c` 不是默认下载器。只有同时满足以下条件才会插入 `default_aria2c` profile：

- `YTDL_ARIA2C_ENABLED=true`
- 系统 PATH 或 `YTDL_ARIA2C_PATH` 能找到 aria2c

参数保持保守单连接，见 [_aria2c_args](../backend/app/ytdlp_service.py#L537)。多连接可能增加 YouTube 风控面，因此默认不启用。

## 失败排查顺序

1. 查看任务中心具体错误；单视频失败时 `Job.error` 会透传唯一失败 `JobItem.error`，见 [job_read_model.py](../backend/app/job_read_model.py#L111)。
2. 查看 `/api/diagnostics`，确认 ffmpeg、JS runtime、impersonation、PO-token provider、cookies、aria2c 状态。
3. 重新从浏览器导入 cookies。
4. 若浏览器可正常播放但应用仍遇到媒体流 403，配置 PO token、visitor data 或浏览器路径。
5. 若是连接不稳定，把并发设为 1，确认代理或网络能稳定访问 YouTube 媒体域名。

## 文件删除语义

任务中心的“仅删除任务”只删除数据库中的任务记录；“删除任务并删除已下载文件”会删除数据库记录，并按 `JobItem.output_path` 删除输出视频及同名字幕、metadata、缩略图、description、info.json 等 sidecar。若历史任务或运行中任务尚未写入 `output_path`，后端会按 YouTube id 在任务下载目录中发现 `... [id].mp4/.webm/.mkv`、字幕、metadata 和 `.part` 等部分下载文件并纳入删除候选。删除逻辑仍限制在下载根目录或该任务下载目录内，避免误删任意路径。
