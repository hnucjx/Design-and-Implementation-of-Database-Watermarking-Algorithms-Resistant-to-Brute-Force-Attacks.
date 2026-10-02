# 技术文档

适用读者：需要理解代理与网络出口、下载策略、清晰度、cookies、JS 运行时、PO token 和稳定性排障的开发者与高级用户。

> 本文解释「为什么这么设计」；只想解决眼前问题请看 [排障手册](troubleshooting.md)。

## 清晰度与格式选择

用户只选择清晰度；后端自动选择格式。选择器实现位于 [ytdlp_formats.py](../backend/app/ytdlp_formats.py#L9)。

数字清晰度如 `1080p` 的优先级：

1. 同高度 `mp4`/H.264 视频 + `m4a`/AAC 音频。
2. 同高度 `mp4` 视频 + `m4a` 音频。
3. 同高度任意 video + audio。
4. 同高度 HLS 单文件。
5. 同高度单文件。

`safari_hls` profile 会把同高度 HLS 单文件放到最前，见 [format_selector](../backend/app/ytdlp_formats.py#L9)。如果 ffmpeg 可用，后端允许 video+audio 合并并设置 `merge_output_format=mp4`；ffmpeg 不可用时会退化为单文件选择器，而需要合并的清晰度直接报错，见 [build_download_options](../backend/app/ytdlp_service.py#L447)。

## 下载前预检测

在实际下载前，`JobManager` 先调用 [prepare_download](../backend/app/ytdlp_service.py#L393) 让 yt-dlp 按当前 selector 选择计划下载格式。源视频清晰度匹配时不再额外 `extract_metadata`——这是上一轮性能修复的成果，见 [PLAN.md](../ai/perf/PLAN.md)。只有计划格式不可选时，才会再解析元数据并按降级原因分类。结果通过 [_apply_download_preparation](../backend/app/job_manager.py#L968) 写入：

- `actual_width`
- `actual_height`
- `actual_format`
- `total_bytes`，当 yt-dlp 能从所选格式得到 `filesize` 或 `filesize_approx` 时写入

因此任务中心可以在下载开始后尽早显示计划分辨率、格式和视频大小。下载完成后仍会根据 progress payload 或输出文件进行校准，避免预检测与最终文件不一致。

**「不可选」有两种表现形式，两者都必须被当成否定结论。** yt-dlp 的格式选择发生在 `extract_info` **内部**，匹配不到时它**抛 `DownloadError: Requested format is not available`**，而不是返回空结果；只有少数路径才走得到「返回空列表」。`YtDlpService.prepare_download` 负责把该异常归一成 `is_selectable=False`，`JobManager._probe_preparation` 再兜一层，保证降级逻辑不会因为「探针换了一种方式说不行」而整段失效 —— 那种失效的症状是界面提示「已自动降级到 1080p」，而下载根本没有开始，见 [010](../ai/bug-fix/010-unselectable-probe-raise-skips-the-fallback.md)。

反向的约束同样重要：**只把「格式选不出来」当作否定结论，其余异常必须继续向上抛**。网络失败、JS challenge、cookies 失效都是真故障，被降级逻辑吞掉只会让真实病因更难查。

## 字幕来源与格式

默认 `DownloadOptions` 使用 `resolution="1440p"`、`subtitle_source="both"`、`subtitle_format="best"`。当用户选择“两者都要”时，yt-dlp 会同时启用人工字幕和自动字幕；若其中一种不存在，只会写出另一种可用字幕。前端在已解析元数据中发现用户指定的人工/自动字幕来源不可用时，会把提交给后端的 `subtitle_source` fallback 到另一种可用来源，并在下载选项面板显示“来源”和“格式”；若两类字幕都没有，则显示“无字幕”。

## 分辨率降级原因

降级原因常量在 [fallback_policy.py](../backend/app/fallback_policy.py#L4)，**决策**（降到哪、为什么、降不了时报哪句话）在 [resolution_decisions.py](../backend/app/resolution_decisions.py#L87) —— 后者是纯函数，不联网、不读库，所以每条规则都能在 `backend/tests/test_resolution_decisions.py` 里单独验。降级策略分为下载前自动处理和媒体流失败提示两类。

| 原因 | 含义 | 是否自动降级 |
| --- | --- | --- |
| `requested_resolution_missing` | 源视频本来没有用户选择的清晰度。 | 是，降到低于目标且不低于 720p 的最高可用清晰度。 |
| `source_below_720_only` | 源视频没有任何 720p 或更高清晰度。 | 是，允许降到源视频最高可用低清晰度。 |
| `requested_resolution_unselectable` | 元数据显示目标清晰度存在，但当前 selector 选不出可下载组合。 | 是，只降到 720p 或更高的安全清晰度。 |
| `media_stream_blocked` | 目标清晰度媒体流被 403、连接重置、超时或 TLS 错误阻断。 | 否，只提供较低清晰度重启建议。 |

720p 底线由 [DEFAULT_MIN_AUTO_FALLBACK_HEIGHT](../backend/app/ytdlp_formats.py#L6) 定义，`resolution_decisions` 以 `MIN_AUTO_FALLBACK_HEIGHT` 暴露同一常量。候选计算见 [suggest_lower_resolution](../backend/app/ytdlp_formats.py#L148)；「要不要放弃自动降级、以及为什么」的判定不在那里，在 `resolution_decisions` 的四个 `decide_*` 函数里。

## 稳定下载策略

默认策略是稳定优先，而不是并发优先。核心参数在 [ytdlp_service.py](../backend/app/ytdlp_service.py#L42) 和 [build_download_options](../backend/app/ytdlp_service.py#L447)：

- `continuedl=True`，保留 `.part` 断点续传。
- `fragment_retries=20`、`file_access_retries=5`、`extractor_retries=5`。
- `socket_timeout=30`。
- 重试等待单次上限 10s（`retries=10` 时单 profile 最长静默约 80s，而不是约 110s），由停滞看门狗兜底。
- `concurrent_fragment_downloads=1`。
- `http_chunk_size=16 MiB` 默认值。进度百分比不能用单个 chunk 的 `total_bytes` 当成分母；聚合器会结合计划文件大小计算，见 [实现文档](implementation.md#进度与平均速度)。
- `throttledratelimit` **默认关闭**（`throttled_rate_kbps=0`）。它只按**单条流**的速度判定，低于阈值时 yt-dlp 会抛 `ThrottledDownload`（`ReExtractInfo` 子类），被其无计数重提取循环接住，表现为每约 5 秒中断并重新 extract 一次；并发越高，单流速度越低，越容易误触发。仅在并发 = 1 且确实需要「慢就换 fresh URL」时，用 `YTDL_THROTTLED_RATE_KBPS=32` 开启（不建议回到 64）。
- 停滞看门狗：默认 90 秒内没有任何新增字节就抛出 `DownloadStalled`，让任务**可见地失败**而不是永久 `running`，见 [stall_guard.py](../backend/app/stall_guard.py)。判据是「历史最大字节是否被刷新」而不是「本轮是否增长」，因此能区分节流振荡（峰值从不刷新）与正常断点续传（恢复后会超过旧峰值）。`finished` 会重置基线，避免合并格式的视频流→音频流切换被误判。`DownloadStalled` 不会进入下一个 profile 重试，直接冒泡为失败。用 `YTDL_STALL_TIMEOUT_SECONDS=0` 可关闭。注意它依赖 yt-dlp 的 progress 回调：若完全阻塞在 socket 读而无回调，兜底仍是 `socket_timeout=30`。
- aria2c（可选，默认关闭）：yt-dlp 内建 http 下载器是**单连接**，单条 URL 无法并行，因此 aria2c 是单视频任务唯一真实的提速手段。安装后仍需 `YTDL_ARIA2C_ENABLED=true` 才会启用，连接数由设置面板或 `YTDL_ARIA2C_CONNECTIONS` 控制（默认 2，上限 4）。多连接是 YouTube 侧最敏感的触发条件，403/限速概率显著上升，出问题时应先把连接数降到 1 或关闭 aria2c。
- 默认 worker 并发为 5，按**同时下载的视频数**计算（跨单视频任务和合集子项）；若追求稳定，可在设置面板或 `YTDL_YOUTUBE_MAX_PARALLEL_DOWNLOADS=1` 中降为 1。并发是视频级而不是流级：单视频任务只有 1 个 `JobItem`，因此**并发设置对单视频任务无效**。
- 单个视频内部的视频流、音频流、字幕和缩略图之间不再插入 2–5 秒 `sleep_interval`；视频级节流由 worker 并发承担。解析和下载阶段的 player API 请求仍使用 `sleep_interval_requests=1.0`。

并发、限速和重试次数属于运行时设置。并发修改会直接调整后台 worker 数，每个 worker 一次处理一个 `JobItem`；限速和重试次数修改会更新 queued/running/paused 任务的 `DownloadOptions`，前端会显示保存中、保存成功或保存失败状态。如果某个视频正在 yt-dlp 内下载，任务管理器会请求当前项退出并重新入队，依靠 `continuedl=True` 和保留的 `.part` 文件断点续传，从而让新的 `ratelimit` 或 `retries` 尽快生效。

核心参数和默认值的完整清单、以及每个参数的风险与回退方式见 [PLAN.md](../ai/perf/PLAN.md)。性能相关的离线复现脚本：

```powershell
python scripts\bench_concurrency.py <临时目录>          # item 级并发是否线性生效
python scripts\bench_throttle_guard.py <临时目录> 64    # 节流守卫开启时的中断-重提取循环
python scripts\bench_throttle_guard.py <临时目录> 0     # 关闭后的连续下载
```

YouTube 媒体流 403 或连接中断时，`YtDlpService.download()` 会在同一清晰度下依次尝试 profile，见 [download](../backend/app/ytdlp_service.py#L527)：

1. `default`
2. `default_aria2c`，仅当显式启用 aria2c 且可执行文件存在
3. `mweb_pot_chrome`
4. `safari_hls`
5. `chrome_default`

媒体流阻断判断见 [is_media_stream_blocked_error](../backend/app/ytdlp_service.py#L692)。这类失败不会在下载中途自动降清晰度重下，任务中心会给出中文原因和可重启建议。

## 代理与网络出口

代理的**解析**与**验证**是两个独立模块，这个划分是刻意的：解析是纯函数、可离线单测；验证必须联网、结果必须是原始证据。

优先级（`app/proxy.py`）：

```
显式设置  >  Windows 系统代理（WinINet 注册表 ProxyEnable/ProxyServer）  >  环境变量
```

系统代理排在环境变量之前：桌面应用应当和浏览器一致，而环境变量极易被宿主 shell / IDE 无意注入（实测过一次 `HTTPS_PROXY` 指向一个不存在的端口，把系统代理顶掉并导致 `502 Bad Gateway`，详见 [006](../ai/bug-fix/006-proxy-is-not-configurable.md)）。

取值语义：

| 填什么 | `source` | 是否写进 `ydl_opts['proxy']` |
| --- | --- | --- |
| 留空 / `auto` | `system` / `environment` / `none` | 取决于来源：`system` 写、`environment` **不写** |
| `direct` / `none` / `off` / `no` / `-` | `direct` | 写（空串，等价 yt-dlp 的 `--proxy ""`） |
| 代理地址（缺 scheme 补 `http://`） | `setting` | 写 |

**为什么「来源=环境变量」时不写：** yt-dlp 的 `proxy` 参数是单个 URL、不带绕过列表 —— `utils/networking.select_proxy()` 只在 proxy map 里存在 `no` 键时才做 `NO_PROXY` 绕过判断，而 `{'all': url}` 没有这个键。不写才能保留用户环境里的 `NO_PROXY` 语义。

注意「空值 ≠ 直连」：表单清空、`YTDL_PROXY=` 都表示**自动**（仍会用到系统代理与环境变量），强制直连必须显式写 `direct`。

验证走 [connectivity.py](../backend/app/connectivity.py#L73)：向固定的 `https://www.youtube.com/robots.txt` 发一次普通 HTTPS 请求，返回状态码、耗时、字节数与原始异常。刻意不复用 yt-dlp —— 它自带重试、cookies、profile 逻辑，失败时分不清是代理坏了还是提取器坏了。一个容易写错的实现细节：**关闭代理必须用 `ProxyHandler({})`**，不传 handler 会让 urllib 自己去读环境变量，那样探测结果就不再等于产品实际使用的设置。

## JS 运行时与 n challenge

YouTube 的 `n` 参数（nsig）需要跑 JS 才能解出来，这是**登录态取数路径的必需项**。相关实现在 [ytdlp_service.py](../backend/app/ytdlp_service.py#L187) 的运行时探测，以及 [runtime_env.py](../backend/app/runtime_env.py#L58) 的环境净化。

探测顺序是「显式 `js_runtime_path` → Deno → Node」，其中 `js_runtime_path` 来自设置面板或环境变量 **`YTDL_JS_RUNTIME_PATH`**（历史上文档把它写成 `YTDL_JS_RUNTIME`，实测那个名字设了不生效，见 [006](../ai/bug-fix/006-proxy-is-not-configurable.md) 的遗留项）。探测**不只判断文件存在，而是真的用与 yt-dlp 相同的权限模型跑一次**：

```
node --experimental-permission --no-warnings=ExperimentalWarning -e <probe>
```

失败时原始报错会留在 `dependencies.js_runtime_error`，逐个候选被拒的原因留在 `js_runtime_candidates_rejected`。这两项是「检测到 node 却解不出 n challenge」唯一能查清楚的地方。

**宿主环境变量会静默打坏这条链路**：`NODE_OPTIONS` 是每个 node 进程都会读的，宿主若塞进 `--require=<补丁>`，node 会在权限模型下拒绝加载它并以 `ERR_ACCESS_DENIED` 退出；n challenge 求解失败后，yt-dlp 抛出的却是完全指不到病因的 `ERROR: The page needs to be reloaded.`。因此 [sanitize_environment](../backend/app/runtime_env.py#L58) 在启动时摘掉命中 `--require` / `--import` / `--loader` / `--experimental-loader` 的 `NODE_OPTIONS`，并把动作记进日志与 `/api/diagnostics.sanitized_environment`。

失败分类见 [error_advice.py](../backend/app/error_advice.py#L116)：它把异常链上的文本翻译成 `code + 结论 + 下一步` 四类之一（JS challenge / cookies / 代理 / 媒体流被挡），无法归类时返回 `None` 而不硬凑。`JobManager` 的失败日志会同时打一行结构化事实与一行 `诊断 / 原因 / 建议N`。

## Cookies 与登录态

Cookies 用于合法账号态、年龄确认或 bot 校验场景。解析阶段逻辑见 [_extract_metadata_with_cookies](../backend/app/api_support.py#L87)，下载阶段刷新逻辑见 [_download_with_cookie_refresh](../backend/app/job_manager.py#L724)。

浏览器导入器只保存 YouTube/Google 相关 cookies，过滤规则见 [YOUTUBE_COOKIE_DOMAIN_SUFFIXES](../backend/app/browser_cookies.py#L13)。Edge 锁库和 DPAPI fallback 处理见 [browser_cookies.py](../backend/app/browser_cookies.py#L117)。

未配置 cookies 时，YouTube 媒体流 403 概率显著上升。任务中心的媒体流失败文案会前置「当前 cookies 状态：已配置 / 未配置」，见 [_media_stream_failure_message](../backend/app/job_manager.py#L1055)，便于先排除这个最常见的前置条件。

已配置的 cookies 是否真的可用，由 [cookie_health.py](../backend/app/cookie_health.py#L234) 判定（`POST /api/cookies/verify`）。它把两件事合成一个结论：

- **离线体检**（始终执行）：Netscape 格式是否可解析、`.youtube.com` / `.google.com` 域上各有多少条、命中的鉴权项**名字**、最近到期时间。不联网，因此也适用于「只想确认文件格式对不对」。
- **联网确认**（`deep=true` 时）：用这份 cookies 请求一次 `https://www.youtube.com/`，读页面里的 `"LOGGED_IN"`。

结论只有三档 —— **未配置 / 未登录 / 已登录**，且每档都带上面那几行证据。响应**不返回 cookie 内容**，只返回条数与名字集合。界面上「结论解读」说明就挂在这三条证据旁边，见 [用户手册](user-manual.md#cookies)。

## PO token 与浏览器 impersonation

依赖 `yt-dlp[default,curl-cffi]` 提供浏览器/TLS impersonation，依赖 `yt-dlp-getpot-wpc` 提供 YouTube PO-token provider，配置在 [backend/pyproject.toml](../backend/pyproject.toml)。

相关环境变量：

- `YTDL_YOUTUBE_PO_TOKEN`
- `YTDL_YOUTUBE_VISITOR_DATA`
- `YTDL_YOUTUBE_PO_BROWSER_PATH`

这些值只传给 yt-dlp extractor 或 provider，不在诊断接口中回显原文。诊断只返回是否已配置，以及可用的 impersonation client 列表，见 [get_dependency_status](../backend/app/ytdlp_service.py#L287)。

## aria2c fallback

`aria2c` 不是默认下载器。只有同时满足以下条件才会插入 `default_aria2c` profile：

- `YTDL_ARIA2C_ENABLED=true`
- 系统 PATH 或 `YTDL_ARIA2C_PATH` 能找到 aria2c

连接数参数来自 `aria2c_connections`（默认 2，上限 4，可在设置面板修改），参数拼装见 [_aria2c_args](../backend/app/ytdlp_service.py#L828)。该能力给单视频提供多连接下载，但多连接会显著提高 YouTube 风控面，因此默认关闭；诊断接口返回 `aria2c_available`、`aria2c_enabled`、`aria2c_path`、`aria2c_connections` 便于判断当前是否真的生效。

## 失败排查顺序

0. 先看 `data/logs/app.log`（路径见 `/api/diagnostics.log_file`）。启动三行快照 `proxy resolved` / `js runtime ...` / `dependencies ready` 能立刻排除环境问题；失败处有 `category=` 与紧随其后的 `诊断 / 原因 / 建议N`。
1. 查看任务中心具体错误；单视频失败时 `Job.error` 会透传唯一失败 `JobItem.error`，见 [job_read_model.py](../backend/app/job_read_model.py#L122)。
2. 查看 `/api/diagnostics`，确认 ffmpeg、JS runtime（含 `js_runtime_error`）、impersonation、PO-token provider、cookies、代理、aria2c 状态。代理通不通用 `POST /api/proxy/test`；cookies 是否真能登录用 `POST /api/cookies/verify`。
3. 重新从浏览器导入 cookies，并用 `POST /api/cookies/verify` 确认结论（域名与鉴权项都可能是「文件存在但全是匿名」的假象）。
4. 若浏览器可正常播放但应用仍遇到媒体流 403，配置 PO token、visitor data 或浏览器路径。
5. 若看到「下载停滞：N 秒内没有新增字节」，说明该连接已不再产出数据；重试或换 profile 后仍失败时，把并发降为 1 并检查代理/网络。
6. 若是连接不稳定，把并发设为 1，确认代理或网络能稳定访问 YouTube 媒体域名。
7. 若报 `The page needs to be reloaded.`，不要再查网络与 cookies：这是 JS 运行时的症状，见 [JS 运行时与 n challenge](#js-运行时与-n-challenge)。

## 文件删除语义

任务中心的“仅删除任务”只删除数据库中的任务记录；“删除任务并删除已下载文件”会删除数据库记录，并按 `JobItem.output_path` 删除输出视频及同名字幕、metadata、缩略图、description、info.json 等 sidecar。若历史任务或运行中任务尚未写入 `output_path`，后端会按 YouTube id 在任务下载目录中发现 `... [id].mp4/.webm/.mkv`、字幕、metadata 和 `.part` 等部分下载文件并纳入删除候选。删除逻辑仍限制在下载根目录或该任务下载目录内，避免误删任意路径。
