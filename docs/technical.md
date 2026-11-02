# 技术文档

适用读者：需要理解代理与网络出口、下载策略、清晰度、cookies、JS 运行时、PO token 和稳定性排障的开发者与高级用户。

> 本文解释「为什么这么设计」；只想解决眼前问题请看 [排障手册](troubleshooting.md)。

## 清晰度与格式选择

用户只选择清晰度；后端自动选择格式。选择器实现位于 [ytdlp_formats.py](../backend/app/download/ytdlp_formats.py#L9)。

清晰度的取值在**入口**就收敛成 `best` 或 `<高度>p`（`schemas._normalize_resolution`：去空白、转小写，`1080P` → `1080p`），其余写法直接 422。理由：选择器认不出 `1080`/`hd`/`1440p60` 时会回落成 `bv*+ba/b`（最佳可用），而 `resolution_decisions` 也认不出、连降级都不介入 —— **不按用户要求做，也不告诉任何人**，见 [026](../ai/bug-fix/026-a-resolution-that-silently-means-best.md)。

数字清晰度如 `1080p` 的优先级：

1. 同高度 `mp4`/H.264 视频 + `m4a`/AAC 音频。
2. 同高度 `mp4` 视频 + `m4a` 音频。
3. 同高度任意 video + audio。
4. 同高度 HLS 单文件。
5. 同高度单文件。

`safari_hls` profile 会把同高度 HLS 单文件放到最前，见 [format_selector](../backend/app/download/ytdlp_formats.py#L9)。如果 ffmpeg 可用，后端允许 video+audio 合并并设置 `merge_output_format=mp4`；ffmpeg 不可用时会退化为单文件选择器，而需要合并的清晰度直接报错，见 [build_download_options](../backend/app/download/ytdlp_service.py#L496)。

## 下载前预检测

在实际下载前，`JobManager` 先调用 [prepare_download](../backend/app/download/ytdlp_service.py#L395) 让 yt-dlp 按当前 selector 选择计划下载格式。源视频清晰度匹配时不再额外 `extract_metadata`——这是上一轮性能修复的成果，见 [PLAN.md](../ai/perf/PLAN.md)。只有计划格式不可选时，才会再解析元数据并按降级原因分类。结果通过 [_apply_download_preparation](../backend/app/download/job_manager.py#L1032) 写入：

- `actual_width`
- `actual_height`
- `actual_format`
- `total_bytes`，当 yt-dlp 能从所选格式得到 `filesize` 或 `filesize_approx` 时写入

因此任务中心可以在下载开始后尽早显示计划分辨率、格式和视频大小。下载完成后仍会根据 progress payload 或输出文件进行校准，避免预检测与最终文件不一致。

**「不可选」有两种表现形式，两者都必须被当成否定结论。** yt-dlp 的格式选择发生在 `extract_info` **内部**，匹配不到时它**抛 `DownloadError: Requested format is not available`**，而不是返回空结果；只有少数路径才走得到「返回空列表」。`YtDlpService.prepare_download` 负责把该异常归一成 `is_selectable=False`，`JobManager._probe_preparation` 再兜一层，保证降级逻辑不会因为「探针换了一种方式说不行」而整段失效 —— 那种失效的症状是界面提示「已自动降级到 1080p」，而下载根本没有开始，见 [010](../ai/bug-fix/010-unselectable-probe-raise-skips-the-fallback.md)。

反向的约束同样重要：**只把「格式选不出来」当作否定结论，其余异常必须继续向上抛**。网络失败、JS challenge、cookies 失效都是真故障，被降级逻辑吞掉只会让真实病因更难查。

## 字幕来源与格式

默认 `DownloadOptions` 使用 `resolution="1440p"`、`subtitle_source="both"`、`subtitle_format="best"`。当用户选择“两者都要”时，yt-dlp 会同时启用人工字幕和自动字幕；若其中一种不存在，只会写出另一种可用字幕。前端在已解析元数据中发现用户指定的人工/自动字幕来源不可用时，会把提交给后端的 `subtitle_source` fallback 到另一种可用来源，并在下载选项面板显示“来源”和“格式”；若两类字幕都没有，则显示“无字幕”。

### 只处理中英，并按主语言挑一条

上面那条是**界面**上的「来源偏好」。真正决定「这次下哪一条」的是后端算出的
[`SubtitlePlan`](../backend/app/download/subtitle_plan.py#L59)（不联网、不读库，纯逻辑）：

1. **只认中文与英文两族**。[`language_family`](../backend/app/download/subtitle_plan.py#L109) 是唯一入口，
   其余语种返回 `None`（= 忽略）。粤语 `yue` **不算**中文 —— yt-dlp 也单列它，混进来会让「中文视频」
   的判定漂到粤语内容上。界面列出字幕与下载选取共用同一份过滤，避免「界面能选 20 种、下载只认两种」。
2. **主语言是平台给的，不是猜的**。yt-dlp 给**原始语音语言**那条 ASR 轨加 `-orig` 后缀
   （源码注释：`Add an "-orig" label to the original language so that it can be distinguished`），
   所以 `zh-Hans-orig` 存在 == 这个视频说的是中文。判不出来时退化为「人工字幕里的中/英」，
   再退化为英文 —— **有界默认，不是随机猜**。
3. **人工优先 → 降级 ASR → 跳过不中断**，这条优先级是**免费的**：yt-dlp 的 `process_subtitles`
   本来就是「先把人工字幕放进候选，再用自动字幕补没被占掉的语言」。我们只要把 `subtitleslangs`
   收敛成**一条**语言即可；同一族有多条轨时由 [`pick_track`](../backend/app/download/subtitle_plan.py#L188)
   只挑一条（简体优先繁体、基础码优先地区变体、`-orig` 排后），否则会落下 `en` + `en-orig` 两份。

**「跳过」不能用空语言列表表达。** yt-dlp 用 `fullmatch` 匹配语言代码，而真实轨是 `zh-Hans` /
`en-orig` 这种带后缀的，所以请求族名 `zh` / `en` 匹配不到任何轨 ⇒ 一条都不下、**也不报错** —— 这
正是「跳过下载、不中断流程」。反过来，**空列表会被当成「没给」**，yt-dlp 按自己的默认去挑英文，
把「跳过」悄悄变成「下载英文」；退化成 `["all"]` 更糟（一次拉 20+ 条轨撞 HTTP 429，连视频本体一起
失败）。所以 `SubtitlePlan.languages` 被设计成**永远 0 或 1 条**。

### 落库信磁盘，不信计划

[`_record_subtitle_result`](../backend/app/download/job_manager.py#L1062) 用**字幕文件是否真的在磁盘上**判定：
yt-dlp 对「请求的语言不存在」是静默跳过（不报错），所以「计划说有」与「写出来了」是两件事。
存一个不存在的文件路径，等于让一次上传在最后一刻才发现没有字幕。

`subtitle_source` 的 `NULL` 与 `none` 也必须分开：`NULL` = 这一轮**没有结论**（`video_only`、
或不需要降级的早退路径），`none` = 有结论（这个视频确实没有可用中英字幕）。混成一句会让用户
刚提交任务就看到「无字幕」。

## 分辨率降级原因

降级原因常量在 [fallback_policy.py](../backend/app/download/fallback_policy.py#L4)，**决策**（降到哪、为什么、降不了时报哪句话）在 [resolution_decisions.py](../backend/app/download/resolution_decisions.py#L87) —— 后者是纯函数，不联网、不读库，所以每条规则都能在 `backend/tests/test_resolution_decisions.py` 里单独验。降级策略分为下载前自动处理和媒体流失败提示两类。

| 原因 | 含义 | 是否自动降级 |
| --- | --- | --- |
| `requested_resolution_missing` | 源视频本来没有用户选择的清晰度。 | 是，降到低于目标且不低于 720p 的最高可用清晰度。 |
| `source_below_720_only` | 源视频没有任何 720p 或更高清晰度。 | 是，允许降到源视频最高可用低清晰度。 |
| `requested_resolution_unselectable` | 元数据显示目标清晰度存在，但当前 selector 选不出可下载组合。 | 是，只降到 720p 或更高的安全清晰度。 |
| `media_stream_blocked` | 目标清晰度媒体流被 403、连接重置、超时或 TLS 错误阻断。 | 否，只提供较低清晰度重启建议。 |

720p 底线由 [DEFAULT_MIN_AUTO_FALLBACK_HEIGHT](../backend/app/download/ytdlp_formats.py#L6) 定义，`resolution_decisions` 以 `MIN_AUTO_FALLBACK_HEIGHT` 暴露同一常量。候选计算见 [suggest_lower_resolution](../backend/app/download/ytdlp_formats.py#L175)；「要不要放弃自动降级、以及为什么」的判定不在那里，在 `resolution_decisions` 的四个 `decide_*` 函数里。

## 稳定下载策略

默认策略是稳定优先，而不是并发优先。核心参数在 [ytdlp_service.py](../backend/app/download/ytdlp_service.py#L42) 和 [build_download_options](../backend/app/download/ytdlp_service.py#L496)：

- `continuedl=True`，保留 `.part` 断点续传。
- `fragment_retries=20`、`file_access_retries=5`、`extractor_retries=5`。
- `socket_timeout=30`。
- 重试等待单次上限 10s（`retries=10` 时单 profile 最长静默约 80s，而不是约 110s），由停滞看门狗兜底。
- `concurrent_fragment_downloads=1`。
- `http_chunk_size=16 MiB` 默认值。进度百分比不能用单个 chunk 的 `total_bytes` 当成分母；聚合器会结合计划文件大小计算，见 [实现文档](implementation.md#进度与平均速度)。
- `throttledratelimit` **默认关闭**（`throttled_rate_kbps=0`）。它只按**单条流**的速度判定，低于阈值时 yt-dlp 会抛 `ThrottledDownload`（`ReExtractInfo` 子类），被其无计数重提取循环接住，表现为每约 5 秒中断并重新 extract 一次；并发越高，单流速度越低，越容易误触发。仅在并发 = 1 且确实需要「慢就换 fresh URL」时，用 `YTDL_THROTTLED_RATE_KBPS=32` 开启（不建议回到 64）。
- 停滞看门狗：默认 90 秒内没有任何新增字节就抛出 `DownloadStalled`，让任务**可见地失败**而不是永久 `running`，见 [stall_guard.py](../backend/app/download/stall_guard.py)。判据是「历史最大字节是否被刷新」而不是「本轮是否增长」，因此能区分节流振荡（峰值从不刷新）与正常断点续传（恢复后会超过旧峰值）。`finished` 会重置基线，避免合并格式的视频流→音频流切换被误判。`DownloadStalled` 不会进入下一个 profile 重试，直接冒泡为失败。用 `YTDL_STALL_TIMEOUT_SECONDS=0` 可关闭。注意它依赖 yt-dlp 的 progress 回调：若完全阻塞在 socket 读而无回调，兜底仍是 `socket_timeout=30`。
- aria2c（可选，默认关闭）：yt-dlp 内建 http 下载器是**单连接**，单条 URL 无法并行，因此 aria2c 是单视频任务唯一真实的提速手段。安装后仍需 `YTDL_ARIA2C_ENABLED=true` 才会启用，连接数由设置面板（界面上叫「单视频并发下载数（1–4）」）或 `YTDL_ARIA2C_CONNECTIONS` 控制（默认 2，上限 4）。**「启用」不等于「每次下载都走」**：`default_aria2c` 是 profile 链里的**第二个**，只有 `default` 因媒体流被挡（403 / 连接重置）、要求登录校验、或 JS 挑战解不出来而被放弃时才会轮到它（见 [_download_profiles](../backend/app/download/ytdlp_service.py#L841) 与 `should_try_next_profile`）；因此调大段数对本来就能成功的下载没有影响。另外 `--min-split-size` 是 16 MiB，**小于这个体积的单流不会被真正拆分**，此时调大段数同样没有效果。多连接是 YouTube 侧最敏感的触发条件，403/限速概率显著上升，出问题时应先把连接数降到 1 或关闭 aria2c。
- 默认 worker 并发为 5，按**同时下载的视频数**计算（跨单视频任务和合集子项）；若追求稳定，可在设置面板或 `YTDL_YOUTUBE_MAX_PARALLEL_DOWNLOADS=1` 中降为 1。并发是视频级而不是流级：单视频任务只有 1 个 `JobItem`，因此**并发设置对单视频任务无效**。
- 单个视频内部的视频流、音频流、字幕和缩略图之间不再插入 2–5 秒 `sleep_interval`；视频级节流由 worker 并发承担。解析和下载阶段的 player API 请求仍使用 `sleep_interval_requests=1.0`。

并发、限速和重试次数属于运行时设置。并发修改会直接调整后台 worker 数，每个 worker 一次处理一个 `JobItem`；限速和重试次数修改会更新 queued/running/paused 任务的 `DownloadOptions`，前端会显示保存中、保存成功或保存失败状态。如果某个视频正在 yt-dlp 内下载，任务管理器会请求当前项退出并重新入队，依靠 `continuedl=True` 和保留的 `.part` 文件断点续传，从而让新的 `ratelimit` 或 `retries` 尽快生效。

核心参数和默认值的完整清单、以及每个参数的风险与回退方式见 [PLAN.md](../ai/perf/PLAN.md)。性能相关的离线复现脚本：

```powershell
python scripts\bench_concurrency.py <临时目录>          # item 级并发是否线性生效
python scripts\bench_throttle_guard.py <临时目录> 64    # 节流守卫开启时的中断-重提取循环
python scripts\bench_throttle_guard.py <临时目录> 0     # 关闭后的连续下载
```

YouTube 媒体流 403 或连接中断时，`YtDlpService.download()` 会在同一清晰度下依次尝试 profile，见 [download](../backend/app/download/ytdlp_service.py#L576)：

1. `default`
2. `default_aria2c`，仅当显式启用 aria2c 且可执行文件存在
3. `mweb_pot_chrome`
4. `safari_hls`
5. `chrome_default`

媒体流阻断判断见 [is_media_stream_blocked_error](../backend/app/download/ytdlp_service.py#L732)。这类失败不会在下载中途自动降清晰度重下，任务中心会给出中文原因和可重启建议。

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

验证走 [connectivity.py](../backend/app/host/connectivity.py#L73)：向固定的 `https://www.youtube.com/robots.txt` 发一次普通 HTTPS 请求，返回状态码、耗时、字节数与原始异常。刻意不复用 yt-dlp —— 它自带重试、cookies、profile 逻辑，失败时分不清是代理坏了还是提取器坏了。一个容易写错的实现细节：**关闭代理必须用 `ProxyHandler({})`**，不传 handler 会让 urllib 自己去读环境变量，那样探测结果就不再等于产品实际使用的设置。

## JS 运行时与 n challenge

YouTube 的 `n` 参数（nsig）需要跑 JS 才能解出来，这是**登录态取数路径的必需项**。相关实现在 [ytdlp_service.py](../backend/app/download/ytdlp_service.py#L187) 的运行时探测，以及 [runtime_env.py](../backend/app/host/runtime_env.py#L58) 的环境净化。

探测顺序是「显式 `js_runtime_path` → Deno → Node」，其中 `js_runtime_path` 来自设置面板或环境变量 **`YTDL_JS_RUNTIME_PATH`**（历史上文档把它写成 `YTDL_JS_RUNTIME`，实测那个名字设了不生效，见 [006](../ai/bug-fix/006-proxy-is-not-configurable.md) 的遗留项）。探测**不只判断文件存在，而是真的用与 yt-dlp 相同的权限模型跑一次**：

```
node --experimental-permission --no-warnings=ExperimentalWarning -e <probe>
```

失败时原始报错会留在 `dependencies.js_runtime_error`，逐个候选被拒的原因留在 `js_runtime_candidates_rejected`。这两项是「检测到 node 却解不出 n challenge」唯一能查清楚的地方。

**宿主环境变量会静默打坏这条链路**：`NODE_OPTIONS` 是每个 node 进程都会读的，宿主若塞进 `--require=<补丁>`，node 会在权限模型下拒绝加载它并以 `ERR_ACCESS_DENIED` 退出；n challenge 求解失败后，yt-dlp 抛出的却是完全指不到病因的 `ERROR: The page needs to be reloaded.`。因此 [sanitize_environment](../backend/app/host/runtime_env.py#L58) 在启动时摘掉命中 `--require` / `--import` / `--loader` / `--experimental-loader` 的 `NODE_OPTIONS`，并把动作记进日志与 `/api/diagnostics.sanitized_environment`。

失败分类见 [error_advice.py](../backend/app/download/error_advice.py#L116)：它把异常链上的文本翻译成 `code + 结论 + 下一步` 四类之一（JS challenge / cookies / 代理 / 媒体流被挡），无法归类时返回 `None` 而不硬凑。`JobManager` 的失败日志会同时打一行结构化事实与一行 `诊断 / 原因 / 建议N`。

## Cookies 与登录态

Cookies 用于合法账号态、年龄确认或 bot 校验场景。解析阶段逻辑见 [_extract_metadata_with_cookies](../backend/app/api/api_support.py#L87)，下载阶段刷新逻辑见 [_download_with_cookie_refresh](../backend/app/download/job_manager.py#L765)。

浏览器导入器只保存 YouTube/Google 相关 cookies，过滤规则见 [YOUTUBE_COOKIE_DOMAIN_SUFFIXES](../backend/app/host/browser_cookies.py#L13)。Edge 锁库和 DPAPI fallback 处理见 [browser_cookies.py](../backend/app/host/browser_cookies.py#L117)。

未配置 cookies 时，YouTube 媒体流 403 概率显著上升。任务中心的媒体流失败文案会前置「当前 cookies 状态：已配置 / 未配置」，见 [_media_stream_failure_message](../backend/app/download/job_manager.py#L1161)，便于先排除这个最常见的前置条件。

已配置的 cookies 是否真的可用，由 [cookie_health.py](../backend/app/host/cookie_health.py#L234) 判定（`POST /api/cookies/verify`）。它把两件事合成一个结论：

- **离线体检**（始终执行）：Netscape 格式是否可解析、`.youtube.com` / `.google.com` 域上各有多少条、命中的鉴权项**名字**、最近到期时间。不联网，因此也适用于「只想确认文件格式对不对」。
- **联网确认**（`deep=true` 时）：用这份 cookies 请求一次 `https://www.youtube.com/`，读页面里的 `"LOGGED_IN"`。

结论只有三档 —— **未配置 / 未登录 / 已登录**，且每档都带上面那几行证据。响应**不返回 cookie 内容**，只返回条数与名字集合。界面上「结论解读」说明就挂在这三条证据旁边，见 [用户手册](user-manual.md#cookies)。

## PO token 与浏览器 impersonation

依赖 `yt-dlp[default,curl-cffi]` 提供浏览器/TLS impersonation，依赖 `yt-dlp-getpot-wpc` 提供 YouTube PO-token provider，配置在 [backend/pyproject.toml](../backend/pyproject.toml)。

相关环境变量：

- `YTDL_YOUTUBE_PO_TOKEN`
- `YTDL_YOUTUBE_VISITOR_DATA`
- `YTDL_YOUTUBE_PO_BROWSER_PATH`

这些值只传给 yt-dlp extractor 或 provider，不在诊断接口中回显原文。诊断只返回是否已配置，以及可用的 impersonation client 列表，见 [get_dependency_status](../backend/app/download/ytdlp_service.py#L296)。

## aria2c fallback

`aria2c` 不是默认下载器。只有同时满足以下条件才会插入 `default_aria2c` profile：

- `YTDL_ARIA2C_ENABLED=true`
- 系统 PATH 或 `YTDL_ARIA2C_PATH` 能找到 aria2c

连接数参数来自 `aria2c_connections`（默认 2，上限 4，可在设置面板修改），参数拼装见 [_aria2c_args](../backend/app/download/ytdlp_service.py#L854)。该能力给单视频提供多连接下载，但多连接会显著提高 YouTube 风控面，因此默认关闭；诊断接口返回 `aria2c_available`、`aria2c_enabled`、`aria2c_path`、`aria2c_connections` 便于判断当前是否真的生效。

## 失败排查顺序

0. 先看 `data/logs/app.log`（路径见 `/api/diagnostics.log_file`）。启动三行快照 `proxy resolved` / `js runtime ...` / `dependencies ready` 能立刻排除环境问题；失败处有 `category=` 与紧随其后的 `诊断 / 原因 / 建议N`。
1. 查看任务中心具体错误；单视频失败时 `Job.error` 会透传唯一失败 `JobItem.error`，见 [job_read_model.py](../backend/app/download/job_read_model.py#L160)。
2. 查看 `/api/diagnostics`，确认 ffmpeg、JS runtime（含 `js_runtime_error`）、impersonation、PO-token provider、cookies、代理、aria2c 状态。代理通不通用 `POST /api/proxy/test`；cookies 是否真能登录用 `POST /api/cookies/verify`。
3. 重新从浏览器导入 cookies，并用 `POST /api/cookies/verify` 确认结论（域名与鉴权项都可能是「文件存在但全是匿名」的假象）。
4. 若浏览器可正常播放但应用仍遇到媒体流 403，配置 PO token、visitor data 或浏览器路径。
5. 若看到「下载停滞：N 秒内没有新增字节」，说明该连接已不再产出数据；重试或换 profile 后仍失败时，把并发降为 1 并检查代理/网络。
6. 若是连接不稳定，把并发设为 1，确认代理或网络能稳定访问 YouTube 媒体域名。
7. 若报 `The page needs to be reloaded.`，不要再查网络与 cookies：这是 JS 运行时的症状，见 [JS 运行时与 n challenge](#js-运行时与-n-challenge)。
8. 若是 B站 上传失败，先看设置面板里「上传到 B站」的体检结论（`biliup` / 登录 / 封面依赖，以及它下面的红字缺项清单 —— **缺「投稿令牌」是最常见的一档**），再去 `data/logs/app.log` 搜 **`biliup 输出`** 看原始报错。注意这个搜索词：失败时 biliup 的原文是逐行按 `biliup 输出 | ` 打进去的；早期版本只落库里不落日志，而失败建议却写着「搜 BILI」，照做的人一定扑空（见 [011](../ai/bug-fix/011-biliup-refuses-a-token-less-cookie-file.md)）。界面上那句结论已经是从原始输出里归类出来的，见 [B站 投稿技术要点](#b站-投稿技术要点)。

## B站 投稿技术要点

B站 投稿与 YouTube 下载是**两套凭据、两条队列**，互不共用：下载用 `data/cookies.txt`，投稿用 `data/bili_cookies.json`（两者都不入库，见 [架构文档](architecture.md#数据模型)）。界面上「上传到 B站」的入口挂在任务行上，但它调用的是 `BiliManager` 的单 worker，不是 `JobManager` 的并发 worker —— 平台限的是**账号**而不是本地并发，共用一个队列会逼出一条不属于下载的限速规则。

### 命令返回 0 不等于已发布

投稿走 `biliup-rs 0.2.4`（`biliup.exe`，安装方式 `winget install --id ForgQi.biliup-rs --exact`），命令组装在 [build_upload_argv](../backend/app/bilibili/bili_bin.py#L143)。三个必须知道的事实：

- **登录必须一路做到拿到投稿令牌。** `biliup` 的 `login_by_cookies` 在**发出任何投稿请求之前**会拿 `token_info.access_token` 去 `passport.bilibili.com/x/passport-login/oauth2/info` 校验一次；`access_token` 是空串时 B站 返回 `-400 请求错误`，`biliup` 随即以 `Err` 退出 —— 全程约 1 秒、一个字节都不会上传，而错误码本身读不出「缺令牌」这层意思。所以 [exchange_tv_token](../backend/app/bilibili/bili_auth.py#L142) 会补上 TV 端三步交换（申请二维码 → 用用户自己的 cookies 替他确认 → 轮询换回 `token_info`），「登录」的完成判据是**拿到令牌**而不是「文件里有 SESSDATA」。详见 bug-fix 记录 [011](../ai/bug-fix/011-biliup-refuses-a-token-less-cookie-file.md)。
- **返回码 0 只说明命令跑完了，不说明平台上多了稿**。所以 [run_single](../backend/app/bilibili/bili_upload.py#L483) 在命令返回后一定进 [_verify](../backend/app/bilibili/bili_upload.py#L300)：用 `GET https://member.bilibili.com/x/web/archives` 回读账号近期稿件，按标题前缀在列表里找（[find_archive](../backend/app/bilibili/bili_api.py#L127)，带退避重试，因为平台侧列表有延迟）。**查不到就停在 `unverified`** —— 这是本功能唯一不肯妥协的一条，宁可让用户去创作中心确认，也不拿「命令没报错」冒充「已发布」。
- **`--submit` 这个开关照传，但它不起作用。** 上游 0.2.4 的 `match submit` 两个分支都调 `submit_by_app`（web 分支被注释掉了），实际提交永远走 `/x/vu/app/add`。早先文档里「`--submit web` 是必需的」是一条**写错的结论**：真正拦住投稿的是上面那次启动时的令牌校验，不是提交接口。


对账还有一处容易写错：**两侧都要 `strip()`**。B站 会对标题做首尾空白处理，用户在手填表单里多打一个尾随空格时，只归一不比较的写法会让对账永远查不到，把一次成功投稿标成「待核对」。改了标题前缀（前 20 字）之后对账也会失效，这是**已知限制**，不是 bug。

回读到的 `state` 由 [platform_state_text](../backend/app/bilibili/bili_policy.py#L38) 映射成中文，再经 `status_for_platform_state` 折成上传状态；封面的最终值取平台侧（本地抽帧的封面被 B站 转码/裁切过，平台上那个才生效）。

### 频控是账号级的

`code 601 您上传视频过快` 是**账号侧**的临时频控，不是本机参数不对。正确处理是等几分钟、原样重投，不要靠改标题/加标签去硬刷 —— 那只会让频控更久。[classify_failure](../backend/app/bilibili/bili_policy.py#L351) 把它归成一条明确的建议，合集批量投稿之间也留了间隔（`DEFAULT_COLLECTION_GAP_SECONDS`）。这条**无法在本机消解**，只能等。

### 上传断了 ≠ 投稿被拒

`biliup` 走 reqwest + rustls。网络层被断开时，它打印的是一条**没有任何业务码**的错误链：
`error sending request for url (https://upos-….bilivideo.com/…)` → `connection error` →
`peer closed connection without sending TLS close_notify`。2026-10-21 的 Ep 130 正是这个形状，
而当时的分类表只认「平台错误码」与「中文失败语」，一条都命中不了 ⇒ 界面上只剩「没能识别出具体原因」。

现在这类失败有 16 条 needle（`TRANSPORT_FAILURE_NEEDLES`，全部是失败语句本身，不会命中
「验证cookie / Upload completed」这些成功行），并且按**失败发生在哪一步**再细分一次：

- 错误那一行的 URL 落在上传 CDN（`bilivideo.com`）上 ⇒ 字节还没传完，提交接口根本没被调用过，
  这才可以说「重试不会多出一条重复稿件」；
- 落在 `api.bilibili.com` 上 ⇒ 提交的应答丢了、稿件**可能已经建好**，只能说「先去创作中心看一眼」。

判据要求「失败语句与 CDN 主机名在**同一行**」：`pre_upload` 那行输出里也带着 `ugcupos` 字样，
按整段输出判会把提交阶段的断线误判成传输阶段，给出一个不成立的承诺。详见 bug-fix 记录
[021](../ai/bug-fix/021-a-dropped-upload-link-looks-like-nothing-at-all.md)。

### 版权与来源：`--copyright` 的默认值是错的

`--copyright` 是**必填**语义（`1` 原创/自制、`2` 转载），而 **`biliup upload` 自己的默认值是 `1`（自制）** —— 早先的实现照抄了这个默认值，等于把每一单都填成「自制」。B站 对「把转载标成自制」是有处置的，所以现在是**默认转载**（`DEFAULT_COPYRIGHT`），由用户在表单里改。

关键判据在 [effective_source](../backend/app/bilibili/bili_meta.py#L205)：**只有转载稿才有来源**，自制稿即便在界面上带着值也不提交 `--source`。不这么收口的话，「界面上有值」与「命令里带了这个参数」会各写一遍，迟早出现落库的快照与实发参数不一致（`source` 是转载稿专有字段，自制稿带了反而可能被判成误标）。

**合集模板上没有共用来源**，这是有意的：合集里每条视频的出处都不同，逐条渲染时各取该条自己的 `source_url`。界面上相应地只在**单视频**场景显示来源输入框；模板的投影刻意留空而不是回填第一条 —— 回填一个看着像「所有条目都用它」的值，正是要避免的误导。

转载稿缺来源会被平台退回来（错误码 `21004 Missing source for reprint`），所以校验拦在提交之前：服务端 [validate](../backend/app/bilibili/bili_meta.py#L354) 是权威，界面另写一份同判据的检查，让用户在输入框旁边就看到，而不是点了提交再吃一个只有错误码的 400。

### 合集（season）的两个端点参数风格相反

「先逐个投稿为独立稿件，再把它们归进同一个合集」这后半段是自研接口，因为 biliup 不支持合集：

- **创建**：`POST /x2/creative/web/season/add` —— **form-urlencoded** 传 `title/desc/cover/season_price=0/csrf`，返回 `data` 即 `season_id`。
- **加稿**：`POST /x2/creative/web/season/section/episodes/add` —— **JSON body**，字段是 camelCase 的 `sectionId` + `episodes[{aid, cid, title, charging_pay}]`，而 **`csrf` 走 query 串**。

两者参数风格正好相反，照抄前者的写法会静默失败。`cid` 只能靠 `GET /x/web-interface/view?bvid=` 拿到，所以归集前对每个已发布稿件都多一次详情请求。「创建」有人工审核，通过前平台上可能看不到，[SEASON_SUBMITTED_NOTE](../backend/app/bilibili/bili_policy.py#L392) 会把这点说给用户。**重试不重复建合集**：归集失败时保留已拿到的 `season_id`，重试只补加缺口 —— 重复建合集在平台侧不可撤销，比「多等一次」贵得多。

**`sectionId` 装的是分区 id，不是合集 id。** 这是本节最容易踩的一处：「创建」返回的是**合集**编号，而
「加稿」要的是该合集下某个**分区（section）**的编号，两者在平台上是两套独立编号
（实测合集 `9343273` 对应「正片」分区 `10455347`）。传错时返回 `code=-404 message=啥都木有` ——
既不像网络问题也不像权限问题，而且**重试不会自愈**（同一个错值再发一遍）。所以归集前必先
[find_season_section](../backend/app/bilibili/bili_api.py#L322) 回读合集列表、从 `sections.sections[]` 里取分区：
优先 `type == 1`（平台自动建的「正片」），该合集被加过自定义分区、没有默认分区时按 `order` 取最靠前的。
读不到分区就**不发加稿请求**，落成「合集建好了，但没读到它下面的分区」并把 `season_id` 带回 ——
详见 [013](../ai/bug-fix/013-season-section-id-confusion.md)。

### 归集方式：`season_id` 预置就是全部的「加入已有」（单视频与合集共用）

界面上让用户选**新建合集 / 加入已有合集 / 不归集**，但后端的新逻辑只有一行赋值：
`submit_collection` / `submit_single` 把用户选中的编号写进 [BiliSeason.season_id](../backend/app/contracts/models.py#L199)，
而 [_group_work](../backend/app/bilibili/bili_manager.py#L1494) 本来就读它当 `existing_season_id`
并据此跳过 `create_season`。**复用既有的「重试不重复建合集」机制，而不是另开一条归集分支** ——
另开一条就要把「建集 + 加集 + 失败重试 + 状态收敛」再实现一遍。

**单视频也能归集，靠的就是把这一行接到 `submit_single` 上**。在此之前 `submit_single`
连归集记录都不建，`_maybe_group` 因此永远找不到它 —— 这个能力只有先下载一整个 playlist
才够得着。两条路径共用同一组字段、同一份校验（[validate_group_choice](../backend/app/bilibili/bili_meta.py#L493)）
与同一套落库，差异**只有默认值**：

| 路径 | 默认 | 为什么 |
| --- | --- | --- |
| 合集（playlist） | `new` | 改动前就是每次投完自动新建一个合集。 |
| 单视频 | `none` | 改动前单视频**从不进任何合集**。 |

用同一个默认值，两处必然有一处是错的，而且**都是「升级之后悄悄改了用户的东西」，没有报错可循**。
所以 [normalize_group_mode](../backend/app/bilibili/bili_meta.py#L102) 的回退值是参数而非写死的常量 ——
调用方必须按场景传。

四处容易写反的地方：

- **`new` 模式下 `season_id` 必须保持为空**。它一旦被预置成任何值（哪怕是对的 `0`），
  `_group_work` 就会跳过建集、对着不存在的编号加视频。所以这个字段不能「顺手填个默认值」。
- **用户显式选择必须覆盖上一轮遗留的编号**。[_upsert_season](../backend/app/bilibili/bili_manager.py#L874)
  既有的「`season_id` 不清空」是为了重试不重复建合集；照搬到「用户从加入 A 改成加入 B」上，
  就会静默地继续往 A 里加 —— 用户选了什么完全不起作用。
- **校验必须看原始输入，不能先归一**。`normalize_group_mode` 把非法值修成 `new` 之后再校验，
  等于把校验废掉了：一个手滑写坏的 `group_mode` 会被静默执行。归一只用于**通过校验之后**
  真正执行的那一步。（这条是被 `test_an_unknown_group_mode_is_400_not_silently_corrected`
  抓出来的，不是推理出来的。）
- **空合集名拦在投稿之前**。[_require_group_ready](../backend/app/bilibili/bili_manager.py#L992) 是路由那道
  400 之外的第二道闸，因为两个提交方法都是公开的：空标题会被 B站 打回来，而那时
  **视频已经投出去了** —— 那是一次不可回退的半成品状态。

**「读不到」与「真的没有」必须分开**。[list_seasons](../backend/app/bilibili/bili_api.py#L275) 自己吞掉
网络异常返回空表，所以未登录时由 manager 按登录态判断、把原因写进 `BiliSeasonListRead.error`。
不去伪造那个区别，是因为界面上一旦显示成「这个账号还没有合集」，用户就会跑去新建一个重复的。
同理，没有编号的合集整条跳过（归集要按编号提交），缺标题的保留并用编号兜名。

**「不归集」在两条路径上的落库行为相反，这是刻意的**：

- **合集**：落成终态 [skipped](../backend/app/contracts/models.py#L129) 而不是 `pending`。
  [_maybe_group](../backend/app/bilibili/bili_manager.py#L1399) 只自动归集 `pending`，留 `pending` 会让
  「不归集」根本不管用，且界面上挂一个永远不会结束的徽标。但记录**照建** —— 不落库的话界面上
  什么都不显示，用户分不清是「没归集」还是「还没开始归」。
- **单视频**：`_upsert_season(create_if_missing=False)` **一条记录都不建**。单视频的默认恰好
  就是「不归集」，照合集那样建记录等于让此后每个单视频任务都在列表上多出一行「合集：未归集」——
  **默认路径上多出来的东西必须是零**。
- 两条路径都**会把已有的 `pending` 记录停掉**：放着不管，用户明明选了「不归集」，
  视频投完还是会被 `_maybe_group` 归集，而界面上看不出任何异常。

**合集列表按需读，不放在开窗路径上**。`GET /api/bilibili/seasons` 只服务于「加入已有合集」
这一个选项，而另外两个默认选项（合集「新建」、单视频「不归集」）都不看它。把它挂在开窗上
曾是**一次真实回归**：一次请求实测 **40 秒不返回**，界面上是一个**没有终点**的「正在读取…」。

病因当时记成了「DNS 解析不受 `socket` 超时覆盖」——**那不是真因**（2026-11-02 实测：
`getaddrinfo` 只用 0.02 秒）。真因是**地址族**与**预算单位**：`getaddrinfo` 把 6 个 IPv6 排在前面，
而本机到 B站 的 IPv6 是黑洞；`socket.create_connection` 会把**每个**地址都试一遍、每次都拿满
`timeout`，于是 `timeout=20` 实测走了 **160 秒**。修法在 [bili_http.py](../backend/app/bilibili/bili_http.py)：
只解析 IPv4，并让整段地址共享一个截止时刻（见 [ai/bug-fix/031](../ai/bug-fix/031-bili-requests-fall-into-an-ipv6-black-hole.md)）。

现在的两道保险都在前端 [App.tsx](../frontend/src/App.tsx#L400)：
`seasonsRequested` 去重（弹窗每收到一条 SSE 事件就重渲染一次，没有去重会反复发请求），
以及 `SEASON_LIST_TIMEOUT_MS = BILI_CALL_TIMEOUT_MS`（25 s，与体检共用）到点写下一个**读完了、但读不到**的结论
（`SEASON_LIST_UNREADABLE`，带可操作的建议）。迟到的真结果会**覆盖**这条兜底 ——
先给一个结论，再被更好的结论替换。触发点在弹窗的副作用里：只有 `groupMode === "existing"`
且列表还是 `null` 时才调 `loadSeasons()`。

前端另有一条与它配套的规则：**「加入已有」却还没挑合集时，表单就地拦下**。这条判据
在两条路径上都成立（先前只对合集成立），否则单视频用户只会吃到一个服务端 400
（`missing_season_id`），而那句话明明可以写在下拉旁边。

### 评论幂等

评论走 `POST /x/v2/reply/add`（旧的 `/x/v2/reply/send` 已下线），置顶走 `/x/v2/reply/top`。重复评论返回 `12051`，所以策略是**先查自己的评论**（在 `/x/v2/reply/main` 里按 `mid` 匹配 + 找 marker 串），有就只补置顶；真撞上 `12051` 时换 `mode=2`（时间序）重找一次。marker 是 [COMMENT_MARKER](../backend/app/bilibili/bili_upload.py#L92)，写在评论正文里，用来区分「这条评论是本应用发的」和「用户自己发的」。

### CC 字幕上传：`oid` 是 cid，语言只用白名单里的值

视频投稿成功后，下载阶段落下的字幕会被转成 BCC 提交到
`POST https://api.bilibili.com/x/v2/dm/subtitle/draft/save`
（[save_subtitle](../backend/app/bilibili/bili_api.py#L366)）。它是**字幕轨**而不是烧进画面的硬字幕，
观众可以在播放器里关掉 —— 这是需求要的那一条。

四个参数上的坑：

| 坑 | 说明 |
| --- | --- |
| **`oid` 必须是 `cid`** | 不是 `aid`。字幕挂在**分P**上；混用的报错是那句没有信息量的 `-404 啥都木有` |
| **布尔要发小写字面量** | Python 的 `True` 经 `urlencode` 变成 `True`，平台按字符串解析就认不出来。必须显式写 `"true"` / `"false"`（`sign=false`、`submit=true`） |
| **BCC 的 `from` / `to` 是秒** | 按毫秒填会让整条字幕的时间轴放大 1000 倍 |
| **`lan` 只用白名单里的值** | 见下 |

**语言白名单（实测，不是从文档推的）**：接受 `zh` / `en` / `zh-Hant` / `zh-TW` / `zh-HK` / `ja` /
`ko` / `yue` / `fr`；**拒绝** `zh-CN` / `en-US` / `zh-Hans` / `en-GB`，报
`code=79011 不合法的语言`。所以 [`bilibili_language`](../backend/app/bilibili/bili_subtitle.py#L93) 把
`zh-Hans` / `zh-CN` 压成 `zh`，繁体三档**原样保留**（它们在白名单里，压平反而丢信息），英文一律
`en`，认不出来退 `en`。判据是用 `submit=false`（只存草稿、不发布）逐个枚举候选值得到的 ——
**真发一次投稿去试语言值，试错成本是一堆不可撤销的稿件。**

三条行为约束：

- **字幕失败不翻稿件状态**。`upload_subtitle` **不抛异常**，任何失败都落成 `failed` 结论。这一步挂在
  「视频已经传成功」之后，抛出去会让一条**已发布**的稿件被标成失败，而用户看到的错误还是「字幕没传上去」。
- **字幕的三档状态不进徽标**。徽标回答「传上去没有」；把「已发布 + 字幕失败」显示成一种新的失败档，
  用户能做的唯一正确动作却是「重传字幕」而不是「重投视频」。
- **补救只重传字幕**：已发布/审核中、且字幕「失败过」或「有字幕文件却没 `posted`」时，重试走
  `_reverify_work` 并带上字幕，**不跑投稿命令**。重投视频不可撤销，只重传字幕是幂等的
  （同一语言轨再传是覆盖）。补传的结果按实际结论回话：成功是「已补传字幕」，
  仍失败是「字幕仍然没传上去：<原因>」—— 稿件还在审核时补传本来就可能再次失败。

### 段数不是句数：滚动字幕的过渡条要丢掉

BCC 的 `body` 一段就是一条 cue。YouTube 的自动字幕是**滚动**的：换行时插一条约 **10 毫秒**的 cue，
内容是刚过去的那一行。Ep 130 的 `.en.vtt` 实测 241 条 cue 里有 **119 条**是这种过渡条（时长正好
0.010s，文字 100% 被相邻 cue 覆盖），而 `merge_repeated_cues` 只合并「整条同文」，合不掉它们 ——
于是传上去 239 段，对应只有 122 句台词。

[`drop_transition_cues`](../backend/app/bilibili/bili_subtitle.py#L211) 判据是**两条同时成立**：时长
≤ `TRANSITION_MAX_SECONDS`(0.05) **且**文字被**紧邻**的 cue 完整覆盖。只看时长会误删真的只有
几十毫秒的短字幕；只看「文字在别处出现过」会误删正常重复（同一句话隔几秒再说一次是新字幕）。
`load_bcc` 的三步定序固定为 **解析 → 丢过渡条 → 合并同文**（先合会把过渡条并进上一条，之后就认不出来）。

全语料 28 个字幕：20,385 → 10,973 条，被删的 100% ≤20ms，**0 文字丢失、0 个文件末条结束时间改变**。

**这条数字不进界面**：结论只说「CC 字幕已上传（英文），观众可在播放器里开关」。段数既不是字数
也不是句数，摆给用户只会引出一个答不上来的问题；它以 `cue_count` 与日志的形式留给排障
（见 [022](../ai/bug-fix/022-a-subtitle-count-nobody-can-read.md)）。语言同理 —— 界面说「英文」，
`en` 是发给接口的白名单取值。

### 「跳过」和「没做成」必须分开记
字幕有 `posted` / `skipped` / `failed` 三档，其中 **`skipped` 只该表示「这个视频确实没有可传的字幕」**
（没下载到、文件已被删、格式不是 SRT/VTT）。「有字幕但挂不上去」是另一回事，必须记 `failed`。

最典型的一种是**稿件还在审核/转码就投稿了字幕**：B站 的 `x/web-interface/view` 对这类稿件返回 `-404`，
`cid` 取不到，而字幕正是挂在分P（`cid`）上的。这时如果记成 `skipped`，下游
`retry_upload` 会因为「状态不是 failed」直接回「这一条已经上传成功」，**这条字幕就永久丢了**
（2026-10-19 本机库实测丢了两条，见 [ai/bug-fix/016](../ai/bug-fix/016-subtitle-lost-while-the-video-is-in-review.md)）。

判据一句话：**一个枚举值不能同时表达「不需要」和「没做成」** —— 下游一定是按键值分支的，
两种含义共用一个值，就必然有一条分支走错。

转换侧另有两条：读文件用 `utf-8-sig`（带 BOM 的 SRT 不是罕见情况）；**只合并相邻且内容完全相同**
的字幕（ASR 常把一句话切成好几条，但「内容相同却不相邻」是副歌/重复台词，不能合并）。

### 登录与 biliup 探测

登录两条路都支持：应用内**扫码**（`/x/passport-login/web/qrcode/generate` + `.../poll`，状态码 `0=confirmed / 86101=waiting / 86090=scanned / 86038=expired`），或**导入/替换已有的 `bili_cookies.json`**。扫码成功后从回调 URL 的 query 与 `Set-Cookie` **两处**取凭据，且 query 里的值需要 `unquote` —— `SESSDATA` 自身含 `%2C`，不解码会写出一个坏 cookie。签发二维码的 SVG **尺寸单位是毫米**（`width="29.6mm"`），前端必须显式覆盖宽高，否则在浏览器里会被当成一个巨大的矢量图撑开表单。

`biliup.exe` 的探测有个 Windows 特有的坑：winget 未开开发者模式时会在 `%LOCALAPPDATA%\Microsoft\WinGet\Links\` 留一个 **0 字节的空壳**，而该目录在 PATH 里优先级最高 —— `shutil.which` 找得到、`path` 也在，但 `CreateProcess` 拒绝执行它。所以每级候选命中后都必须验 PE 头（`MZ`，见 [is_pe](../backend/app/bilibili/bili_bin.py#L59)），并把「找到了但不能用」如实报出来（[detect_broken_stub](../backend/app/bilibili/bili_bin.py#L86) / [repair_broken_stub](../backend/app/bilibili/bili_bin.py#L115)）。界面上这种情况必须按**不通过**渲染并附上原因，不能因为 `found` 为真就打勾。

### 表单与硬限

预填与硬限的权威值都在 [bili_meta.py](../backend/app/bilibili/bili_meta.py)：标题 ≤ 80、简介 ≤ 250、标签逗号分隔总长 < 200（最多 10 个、单个 ≤ 20）、评论 ≤ 1000（`COMMENT_MAX`）。默认分区 `tid=122`（野生技术协会）。合集按模板批量生成时，`{index}` / `{title}` 这类占位符在 [render](../backend/app/bilibili/bili_meta.py#L278) 里替换，未知占位符由 `unknown_placeholders` 挑出来单独报错。前端的预填数字全部来自服务端，不写死在组件里。

## 打开字幕文件

任务中心的「打开字幕」只是把**文件**交给操作系统，程序的选择权也一起交给它。这条路上有八件事容易做歪：

**一、不要自建编辑器清单。** 系统已经有一份完整的处理器注册表（`.srt` / `.vtt` → 谁），自己再列一份
notepad / VS Code / Sublime 只能是它的子集：用户装了别的我们不知道，各平台的清单还得各维护一份。
所以接口只有两个动作，界面只用前一个：

| 动作 | Windows 实现 | 谁决定程序 |
| --- | --- | --- |
| 弹「打开方式」（界面唯一动作，接口缺省 `mode=chooser`） | 先请系统（`SHOpenWithDialog` → `OpenAs_RunDLL`），都不可用才用自带对话框 | 用户当场在列表里挑（系统清单或自带清单 + 「浏览…」） |
| 默认打开（仅接口调用方 `?mode=default`，界面不再提供） | `os.startfile(path)` | 该后缀的关联程序（`HKCU\...\FileExts\<ext>\UserChoice`） |

**二、判据是「窗口里有没有可选项」，不是「有没有窗口」。** 上一版只看「本进程里出现了类名
`Shell_Dialog` 的可见窗口」就认定成功，而这个类名两个东西共用：

| 系统给的窗口 | 尺寸（实测） | 里面有什么 |
| --- | --- | --- |
| 真正的「选择应用」 | 约 700×500 | 程序列表 + 浏览 + 确定 |
| 降级后的提示框 | **680×118**（5 种标志位 × 3 种后缀全都一样） | 一句「若要更改默认应用，请转到设置…」+ 确定 |

所以判据改成**高度**（`SHELL_NOTICE_MAX_HEIGHT = 300`，留足 DPI 余量）：低于它就当没弹出来，
就地关掉、继续往下走 —— 否则用户会对着一个只有「确定」的框子，还以为按钮坏了。

**三、系统的两条入口都试，都不可用才自己画。** 两条走的不是同一段代码，失效方式也不同：

| 级 | 入口 | 认领窗口的方式 | 等多久 | 这条路能给出的好处 |
| --- | --- | --- | --- | --- |
| 1 | `shell32.SHOpenWithDialog`（文档化 API） | 本进程的 `Shell_Dialog` + 高度 ≥ 300 | 2.5s（含关闭降级框的时间） | 系统完整清单、含 Store 应用、「始终使用」 |
| 2 | `OpenAs_RunDLL`（explorer 托管的「选取应用」） | 宿主 exe 是 `OpenWith.exe`（标题随语言变） | 1.6s；若第 1 级已判定降级则只给 0.8s | 第 1 级在本机降级时，这条路有时仍是好的（用户实测过 `2c5aafb` 就能选记事本 / VS Code / CLion / 写字板） |
| 3 | 自带 tkinter 对话框 | 本进程 `TkTopLevel` + 标题「打开方式」 | 其余时间 | 前面都不行时，至少让用户能挑一个程序 |

判据、超时和「哪一级失败」都收在 `_open_windows_chooser` / `_run_chooser` 里，请求线程只负责
「看到窗口就返回、超时就报错」。真机实测（本机两级系统入口都不可用）：**1.58s 落到自带对话框**。

**四、兜底对话框的候选从哪来。** 不编清单，但也不指望系统替我们列全：

- **系统为该后缀登记的处理程序**（`HKCU\...\FileExts\<ext>\OpenWithList`、`HKCR\<ext>\OpenWithList`、
  `HKCR\<ext>\OpenWithProgids` 指向的 `shell\open\command`）—— 与系统选择器同源；
- **本机真实存在的常见编辑器**（`_EDITOR_LOCATIONS` 按品牌列安装位置并支持通配 + `App Paths` 里命中
  `_EDITOR_EXE_NAMES` 的条目），只保留 `is_file()` 成立的那些。这张表是**候选来源**，不是界面清单：
  没装的不会出现（本机 `.vtt` 实测 6 条：记事本 / VS Code / CLion / IntelliJ IDEA / PyCharm / Visual Studio；
  写字板在 Win11 24H2 已被移除，所以不出现）；
- **「浏览…」**兜住剩下的：文件对话框从 `Program Files` 起步、默认只显示 `.exe`（普通用户不知道程序装在哪）。

**登记表里的值不是路径，必须先解析。** `OpenWithList` 存的是裸 exe 名（`QQ.exe`）或 AppX 包标识
（`…!App`），`Path(value).is_file()` 对前者也是假（那是相对工作目录找）。所以取值一律过
`_resolve_executable`：带分隔符的按路径判存在，裸名走 `PATH` → `App Paths`，`…!App` 直接放弃
（它要用 `IApplicationActivationManager` 启动，不是 `Popen` 能开的）。解析不出来的一律**在源头丢掉**，
不留 `(名字, "")` 这种点了没反应的行。

判据也是这里的坑：`OpenWithList` 的键名是 `a` / `b` / `c`（程序项）与 `MRUList`（记录它们的顺序），
只有 `MRUList` 不是程序项。曾经写成 `name.isdigit()` —— 真实键名一个都不是数字，于是**两个来源
恒为空**，界面上表现为「清单里只有编辑器，没有我平时用来打开这类文件的那个程序」。
显示名同理：ProgID 没有登记显示名时（`Toolbox.Air.b9908000-…`）退回可执行文件名，
不要把标识印给用户。见 [029](../ai/bug-fix/029-open-with-list-is-never-read.md)。


**五、窗口出现了就算成功；没出现必须报错；点取消不算失败。** 旧实现只能「204 空转」：界面看起来点过了，
其实什么都没发生。现在整条链超时（6s）仍没有任何窗口，就抛 `LocalOpenError`，接口回 400，前端把原因
写在**那一行**。已有窗口时再点**不再叠一个**，而是提示「先在那个窗口里选完」；用户点「取消」则静默返回。
整条链要 1~2 秒，所以任务行在这段时间显示「正在打开…」（`.local-action-hint`）—— 沉默 2 秒和「没反应」
在用户眼里是一回事。

**六、前台：先等它自己上来，超时才抢；自带对话框钉 topmost。** 用户点的是浏览器，窗口是我们的后台
进程画的，Windows 的前台锁默认拒绝后台进程 `SetForegroundWindow`。但**提前抢更糟**：窗口刚创建时它
自己在激活，这时去抢会被拒，而且被拒之后它就不再自行激活 —— 实测「提前抢 → 前台始终是浏览器；
不碰它 → 对话框自己上来」。所以顺序是：先给它 1.2s 自己上来（[_ensure_dialog_foreground](../backend/app/host/system_open.py#L871)），
确实没上来才用 `AttachThreadInput` 接管前台线程的输入队列再抢。自带对话框另外声明 `-topmost`：
即使焦点被别的窗口抢走，它仍然压在最上面（真机探针量 `WS_EX_TOPMOST`）。

**七、不记「上次用的程序」，想设默认请到系统设置。** 对话框里挑一次只影响这一次；要让它以后都用某个
程序打开，正确的位置是「设置 > 应用 > 默认应用」（对话框里也写了这句）。应用侧不存「上次用的查看器」：
两处各记一份时，系统那条关联会在别处（资源管理器双击）生效、我们这条在按钮上生效，用户根本无法判断
该信谁 —— 而且 Windows 11 不允许第三方程序写 `UserChoice`。

**八、没有关联不是错误。** 在干净的 Windows 上对一个 `.srt` 调 `os.startfile`，抛的是
`OSError [WinError 1155] 没有应用程序与此操作的指定文件有关联`。这不是「打不开」，是「还没人认领这个
后缀」—— 正确反应是像资源管理器那样**弹「打开方式」**（[open_path_with_default_app](../backend/app/host/system_open.py#L90)
里只对这一种 `winerror` 降级，其它错误照原样抛：把「拒绝访问」也悄悄变成弹窗，就是把真问题藏起来）。
这条路现在只有接口的 `?mode=default` 会走到。

其它平台没有等价的「选择器」（macOS 的 `open -a` 要的是应用名，Linux 的 `xdg-open` 只有默认），
`mode=chooser` 在那里退化成默认打开，见 [open_path_with_chooser](../backend/app/host/system_open.py#L108)。

前端因此是**一颗**按钮（[SubtitleOpenButton](../frontend/src/task-center/SubtitleOpenButton.tsx#L19)），
`title="打开字幕文件；可选择打开方式"`，点下去就是选择器。它曾经是**组合控件**（主段 + 右侧 20px 箭头）：
那个箭头正是「看起来什么都没发生」的那一处 —— 把一个动作拆成两颗按钮、其中一颗还看不见效果，
比「入口藏起来」更糟。一个动作 = 一颗按钮；Shift 点击这类看不见的修饰键同样不行。

## 文件删除语义

任务中心的“仅删除任务”只删除数据库中的任务记录；“删除任务并删除已下载文件”会删除数据库记录，并按 `JobItem.output_path` 删除输出视频及同名字幕、metadata、缩略图、description、info.json 等 sidecar。若历史任务或运行中任务尚未写入 `output_path`，后端会按 YouTube id 在任务下载目录中发现 `... [id].mp4/.webm/.mkv`、字幕、metadata 和 `.part` 等部分下载文件并纳入删除候选。删除逻辑仍限制在下载根目录或该任务下载目录内，避免误删任意路径。
