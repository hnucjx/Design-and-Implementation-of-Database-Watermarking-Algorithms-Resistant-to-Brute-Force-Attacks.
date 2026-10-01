# 排障手册

适用读者：**应用用不起来的人**。这里不解释架构，只做一件事：把「你看到的那句话」映射到「现在该点哪里」。

想看原理，去 [技术文档](technical.md)；想看字段含义，去 [API 文档](api.md)；想改代码，去 [实现文档](implementation.md)。

## 三条最省事的路（按顺序试）

1. **打开日志文件**：`data/logs/app.log`。界面上的路径可以一键复制（设置 → 日志文件 → 复制路径）。
2. **点两个自检按钮**：设置面板的「检测代理」与「重新自检」；解析面板的「校验 cookies」。
3. **把日志最后 30 行发出来**：失败时日志里已经有「诊断 / 原因 / 建议」三段，通常不用再问人。

> 应用**从启动那一刻就开始写日志**。以前没有这回事：日志只在终端里、INFO 级别被静默丢弃，
> 于是「明明坏了但什么都看不到」。现在 `data/logs/app.log` 是唯一取证入口，UTF-8、
> 2 MiB 一轮转、保留 3 份备份。想看更细的过程，用 `YTDL_LOG_LEVEL=DEBUG` 启动。

## 按症状查

| 你看到的 | 真正发生了什么 | 现在做什么 |
| --- | --- | --- |
| 解析/下载报 `ERROR: ... The page needs to be reloaded.` | YouTube 的 `n` 参数（JS challenge）解不出来。这句话**不指向任何真实病因**，最典型的成因是 JS 运行时被宿主环境变量打坏 | 装一个 Node 18+ 或 Deno，然后点「重新自检」。见下方 [JS 运行时](#js-运行时与-n-challenge) |
| **服务根本起不来**，报 `[WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试` | **不是权限问题。** 端口已被别的程序以独占方式监听着（本机常见：IncrediBuild 的 `Manager.exe` 长期占 `8000`），Windows 返回的是它而不是「地址已在使用」 | 用 `python -m app` 启动：它会指名占用者并给出下一步。见下方 [端口被占用](#启动就失败端口被占用) |
| 报 `Sign in to confirm you're not a bot.` / `Login required` / 年龄限制 / 会员视频 | 请求是匿名的 | 「校验 cookies」→ 按结论重新导出。见 [cookies](#cookies-篇) |
| 下载报 HTTP 403 / 连接被重置 | YouTube 拒绝了媒体流 | 重新导入 cookies（登录态过期最常见）；并发降到 1；关掉 aria2c；确认代理没有中途换 IP |
| 提示「已自动降级到 1080p」，任务却紧接着失败、**根本没有开始下载** | 降级只被**标注**、没被**执行**：yt-dlp 把「选不出格式」实现成**抛异常**，而当时的降级分支只认返回值，整段逻辑被跳过 | 升级到含 [010](../ai/bug-fix/010-unselectable-probe-raise-skips-the-fallback.md) 的版本。临时办法：把清晰度调低一档再重启该任务。见下方 [提示已降级却没有开始下载](#提示已降级却没有开始下载) |
| 解析直接超时、或 `[WinError 10061] 由于目标计算机积极拒绝` | 连不上 YouTube，且形态指向代理 | 「检测代理」。**浏览器能打开不代表应用能**，见 [代理篇](#代理篇) |
| `Tunnel connection failed: 502` | 代理地址存在但连不通（或是一个已经不存在的端口） | 「检测代理」→ 换成正确的端口，或临时填 `direct` 立刻失败而不是等超时 |
| 名单里显示 `cookies` 状态为未配置 | 没有 `data/cookies.txt` | 导入 cookies，见 [cookies 篇](#cookies-篇) |
| 「下载停滞：N 秒内没有新增字节」 | 连接已经不再产出数据，应用主动判失败而不是无限等 | 直接重启该任务；必要时把并发降为 1 |
| 任务卡在下载中但进度不动 | 停滞看门狗被关掉了，或进程被强杀 | 检查是否设了 `YTDL_STALL_TIMEOUT_SECONDS=0`；正常情况下 90 秒内会变成可见失败 |
| 「找不到可确认能解码当前视频的播放器」 | 本机播放器无法解码该格式 | 按任务行提示安装 VLC / mpv / PotPlayer / MPC，或直接打开文件夹手动播放 |
| 「Edge 正在运行，cookies 数据库被锁定」 | 浏览器占用了 cookie 数据库 | 手动关闭 Edge 后重试；**不要**让应用去关（它会 `taskkill` 掉你所有 Edge 窗口，见 005 遗留风险） |

## 启动就失败：端口被占用

### 症状

```text
2026-10-01 14:35:09 ERROR uvicorn.error | [Errno 13] error while attempting to bind on address ('127.0.0.1', 8000):
[winerror 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。
```

注意这一行**不是权限问题** —— 和「以管理员身份运行」「关掉防火墙」「杀毒软件拦截」都无关。
真实情况是端口已被别的程序占用，而 Windows 在这种情形下返回的是 `WSAEACCES(10013)` 而不是
`WSAEADDRINUSE(10048)`。判据是「占用者是否以 `SO_EXCLUSIVEADDRUSE` 独占通配地址」——
四种组合的实测矩阵与结论见 [009](../ai/bug-fix/009-local-dev-port-is-occupied-and-unconfigurable.md)。

另外，这条错误出现在 `Application startup complete.` **之后**：应用初始化、依赖探测、日志全都正常，
唯独端口拿不到。所以别去查 Node、ffmpeg、cookies —— 那些都不在这条路径上。

样例日志里的 `8000` 是**默认端口，不是写死的值**：端口由 `YTDL_API_PORT` 决定，运行中的实际值以启动时的
`服务地址：http://127.0.0.1:<port>` 一行为准。下面命令里的 `8000` / `8001` 同理，按你实际用的端口替换。

### 现在做什么

1. **用 `python -m app` 启动**（而不是裸 `uvicorn`）：它会直接打印占用者是谁，并给出三条下一步。
   本机开发机上长期占着 `8000` 的是 IncrediBuild 的 `Manager.exe`（PID 通常每次开机都变）。
2. **换端口**：把 `YTDL_API_PORT=8001` 写进仓库根 `.env`（前端 Vite 读的是同一个变量，会自动一致），
   或临时 `python -m app --port 8001`。
3. **立刻开工**：`python -m app --auto-port` —— 自动往后找一个可用端口，并把前端该设的值一并打印出来。
4. **自己确认是谁占着**：`netstat -ano -p tcp | findstr :8000`，最后一列 PID 拿去任务管理器对号。

**不要**为了腾出 `8000` 去杀 IncrediBuild：它是编译加速工具，约束见
[PLAN.md 全局约束](../ai/plan.md)。也**不要**只改后端端口就让前端照旧跑 ——
Vite 的代理如果还指着旧端口，`/api` 会静默打到别的程序上，报出来的错与真实病因毫无关系。

## 提示已降级却没有开始下载

### 症状

任务行先出现一句「检测到 1440p 清晰度，但该清晰度当前没有可下载的视频/音频组合，已自动降级到 1080p。」
（或「当前没有 1440p 的视频，低于选定分辨率的最高可用分辨率是 1080p。」），随后该条目直接变成 `failed`，
`实际分辨率` 与 `视频大小` 一直是空的，`downloads/` 里连 `.part` 文件都没有。

```text
2026-10-01 22:29:09 WARNING app.job_manager | download item failed: job_id=... item_id=... title='On Vibe Coding' resolution=1440p category=format_unavailable error_class=DownloadError error=ERROR: [youtube] hTdSU7q5WCo: Requested format is not available. Use --list-formats for a list of available formats
```

### 真正发生了什么

这句话里有两个错误信息：

1. **「检测到 1440p 清晰度」是错的** —— 该视频根本没有 1440p，最高只有 1080p。
2. **「已自动降级到 1080p」也是错的** —— 降级只被写进了状态字段，没有任何代码真的去下载 1080p。

根因是 yt-dlp 的**接口形状与预期不符**：它的格式选择发生在 `extract_info` 内部，选不出来时
**直接抛 `DownloadError`**，而不是返回一个空结果。而 `_prepare_download` 里的降级分支写在
「`prepare_download` **返回** `is_selectable=False`」这一条件下，于是这段代码在真实故障下从未被执行。
异常一路逃到 `_run_item` 的兜底 `except`，被当成「格式不可用」记了 `format_unavailable` 并标注降级说明，
任务随即失败。完整取证见 [010](../ai/bug-fix/010-unselectable-probe-raise-skips-the-fallback.md)。

### 现在做什么

1. **升级**到含 010 的版本即可，无需改设置。修复后同一视频会正常降级到 1080p 并开始下载，
   提示也会变成正确的那句「视频本来没有 1440p，已自动降级到 1080p。」。
2. **临时绕过**：在下载选项里把清晰度手动调到 1080p 或更低，再重启该任务 —— 只要第一次预检就能选出组合，
   就不会走到这条失效路径上。

> 注意它与「媒体流 403」不是一回事：403 发生在**下载开始之后**，会有 `.part` 文件和进度；
> 这里的症状是**下载从未开始**，`downloaded_bytes` 始终为空。

## 日志长什么样

每条失败至少有两行：一行是机器可读的结构化事实，紧跟着一行是**照着做就行**的诊断。

```text
2026-10-01 12:01:33 WARNING job_manager | download item failed: job_id=... item_id=... title='...' resolution=1440p category=js_challenge_failed error_class=DownloadError error=ERROR: [youtube] aqz-KE-bpKQ: The page needs to be reloaded.
2026-10-01 12:01:33 WARNING job_manager | download item failed advice: job_id=... item_id=...
      诊断: js_runtime_challenge_failed —— YouTube 的 JS challenge（n 参数）解不出来，登录态下的可用格式被判定为空
      原因: yt-dlp 报出与 JS challenge 相关的信息（命中 "the page needs to be reloaded"）。 当前 JS 运行时自检失败：node C:\...\node.EXE：以 yt-dlp 相同的权限模型启动失败（returncode=1）：ERR_ACCESS_DENIED
      建议1: 按诊断里的提示修复 JS 运行时（Deno 或 Node），修复后在设置里显式填写路径并重新自检
      建议2: 这类失败只在带 cookies 的登录取数路径上出现；临时可先「清除 cookies」验证是否与登录态相关
      建议3: 查看日志文件 data/logs/app.log 中 "js runtime" 相关行，那里有 node/deno 的原始报错
```

`category` 的取值就是分类本身，便于刷选与统计：

| category | 含义 |
| --- | --- |
| `js_challenge_failed` | n 参数解不出来（JS 运行时问题） |
| `cookie_required` | 需要登录态 / 人机校验 |
| `media_stream_blocked` | 403 或连接被重置 |
| `format_unavailable` | 请求的清晰度拿不到可用格式 |
| `download_failed` | 其它 |

启动时还会固定打三行环境快照，**排障时先看这三行**就能排除掉一半「环境没装齐」的可能：

```text
2026-10-01 12:00:58 INFO  app.ytdlp_service | proxy resolved: source=system proxy=http://127.0.0.1:7890 system=http://127.0.0.1:7890 environment=<none>
2026-10-01 12:00:58 INFO  app.ytdlp_service | js runtime ready: name=node path=C:\Program Files\nodejs\node.exe version=v22.22.2
2026-10-01 12:00:58 INFO  app.ytdlp_service | dependencies ready: ffmpeg=True po_token_provider=True chromium=<none> aria2c=<none>
```

## 代理篇

### 三类事实要分清

| 问题 | 谁回答它 |
| --- | --- |
| 当前**用的是哪个**代理？ | 设置面板的一行 `当前生效：…（来源：…）`，以及 `GET /api/diagnostics` 的 `proxy*` 字段 |
| 用了之后**通不通**？ | 「检测代理」按钮（`POST /api/proxy/test`） |
| 为什么**我填的没生效**？ | 看「来源」：`你手动填写的地址` / `Windows 系统代理` / `环境变量（HTTP_PROXY / HTTPS_PROXY）` / `强制直连` |

### 三种取值语义

| 填什么 | 含义 |
| --- | --- |
| 留空 | **自动**：先看 Windows 系统代理（注册表 WinINet），再看环境变量。留空**不等于**直连 |
| `direct`（也可写 `none` / `off` / `-`） | 强制直连。适合「本机本来就要直连」的场景，好处是立刻失败而不是等 30 秒超时 |
| 代理地址 | 按 URL 用；缺 scheme 会自动补 `http://`，所以 `127.0.0.1:7890` 这样填也可以 |

### 常见端口

| 软件 | 通常填这个 | 说明 |
| --- | --- | --- |
| Clash / Clash Verge / Mihomo | `127.0.0.1:7890` | 混合端口（HTTP + SOCKS 同一个口） |
| v2rayN | `127.0.0.1:10809` | HTTP 代理端口；SOCKS 端口通常是 10808 |
| Shadowsocks / SS 客户端 | `127.0.0.1:1080` | 本地 SOCKS5 端口，本应用同样支持 |
| Surge / Quantumult 等 | `127.0.0.1:6152` | HTTP 代理端口 |

### 「先试输入框里的地址」

设置输入框是**离开时才保存**的。点「先试输入框里的地址」时按钮会拦住这次点击造成的失焦，
所以它**只发一次探针请求、不写设置**——地址没验证通过之前不会落到配置里。通过之后再离开输入框保存。

### 为什么浏览器行、应用不行

浏览器可能走的是系统代理，或者它自己的插件代理；应用用的是**设置里那一份**地址。
两者是两条独立的路径，所以「浏览器能打开 youtube.com」不能证明「应用能」，反之亦然。

### 检测结果怎么读

- `连通：… 返回 HTTP 200，用时 N ms` → 网络这一层没问题，问题在后面（cookies / JS 运行时 / PO token）。
- `强制直连失败：…` → **可能是预期结果**，如果本机本来就需要代理，这句话不表示故障。
- `通过代理 http://… 访问失败：URLError: … 积极拒绝` → 端口上根本没有东西在监听，代理软件没开或端口填错。
- 超时（约 15 秒）→ 地址可达但没有响应，常见于代理软件没启动、或系统代理指向了一个已关闭的端口。

## cookies 篇

### 三种获取方式

1. **仓库里的脚本（推荐）**：`python scripts/export_cookies_via_cdp.py`。它会开一个**独立配置**的浏览器窗口，
   不影响你正在用的浏览器；第一次登录一次 Google 账号，之后自动写 `data/cookies.txt`。
   两个要点：窗口必须**有头**（无头模式下 YouTube 不签发 `.youtube.com` 鉴权 cookie）；
   脚本会先校验域名，拿不到 `.youtube.com` 就**拒绝写文件**（退出码 2），不会用废文件覆盖好文件。
2. **浏览器扩展**：装 *Get cookies.txt LOCALLY* 之类，在 `youtube.com` 页面导出 Netscape 格式，
   另存为 `data/cookies.txt`。**必须选 Netscape / cookies.txt 格式，不要 JSON。**
3. **直接拖进来**：解析面板的「选择 cookies」上传，格式必须是 Tab 分隔的 7 列。

### 不要做什么

不要试图让应用直接读取你正在使用的 Edge/Chrome 配置。Edge 的 cookie 是 **v20 app-bound 加密**，
密钥绑定正在运行的浏览器进程本身，任何离线程序都解不开；绕过这个限制的变通做法（挂载真实配置目录）
会**破坏浏览器的 cookie 库**——本项目实测过一次，53 条 cookie 变成 0 条。这条路径已经从应用里彻底移除。

同理，`关闭 Edge 并导入` 走的是 `taskkill /IM msedge.exe /F /T`，会关掉你**所有** Edge 窗口（含未保存的标签页）。
它不是默认行为，需要你显式点。

### 校验结论对照

「校验 cookies」把结论分成三种强度：**离线体检**（不联网，看格式/域名/鉴权项/过期）、
**联网校验**（拿这份 cookies 访问一次 `https://www.youtube.com/`，看页面里的 `"LOGGED_IN"`）。

| 结论 | 含义 | 怎么办 |
| --- | --- | --- |
| 未配置 | 没有 `data/cookies.txt` | 用上面三种方式之一导入 |
| 域名不对，等于没配 | cookie 全落在 `.google.com` 上 | 重新导出。`.google.com` 的 cookie **永远不会**发给 `www.youtube.com` |
| 只有匿名 cookie | 只有 `VISITOR_INFO1_LIVE` / `YSC` / `PREF` 之类 | 在导出窗口里真正登录后再导一次 |
| 登录态无效 | 域名与鉴权项都对，但 YouTube 仍认为你是匿名 | 多为登录态过期，或导出后换了出口 IP；重新导出 |
| 登录态有效 | 页面里出现 `"LOGGED_IN":true` | 不用管，下载时 403 会明显变少 |

**为什么必须校验而不能只看「文件存在」**：一个看起来完全正常的 `cookies.txt` 可能是废的。
实测过一份 9 条 cookie 的文件，里面 `SID`、`HSID` 都在，yt-dlp 也不报错，但每个请求都是匿名的
——因为那 9 条全部落在 `.google.com`。只看「文件存在」永远发现不了这件事。

### 换网络会让登录态失效

YouTube 把登录态与出口 IP 绑定。导出 cookies 时走代理 A、下载时走代理 B，
就会出现「校验通过但下载 403」。保持导出与下载用同一条出口。

## JS 运行时与 n challenge

### 症状

报 `ERROR: [youtube] <id>: The page needs to be reloaded.`。**这句话与真实病因无关**，
它甚至不会提到 JS。真实链路是：

```text
JS 运行时起不来 → n 参数解不出来 → 某些 client 的格式被判定为「没有可用 URL」
                → yt-dlp 抛出的却是 The page needs to be reloaded.
```

### 最隐蔽的一个成因（实测）

宿主环境（IDE、桌面壳、终端 profile）往 `NODE_OPTIONS` 里塞了 `--require=<某个 .cjs 补丁>`。
`NODE_OPTIONS` 是**每个 node 进程都会读**的，而 yt-dlp 用
`node --experimental-permission` 启动 JS 求解器——权限模型默认拒绝一切文件读取，
于是 node 拒绝加载那个补丁并直接退出（`ERR_ACCESS_DENIED`），n challenge 求解失败。

本机实测的 A/B（同一台机器、同一份 cookies、同一个视频）：

| | JS 运行时 | 结果 | 耗时 |
| --- | --- | --- | --- |
| 注入 `NODE_OPTIONS=--require=<shim>` | 不可用 | `DownloadError: ERROR: [youtube] aqz-KE-bpKQ: The page needs to be reloaded.` | 6.44 s |
| 摘除该变量后 | 可用 | 成功 | 6.84 s |

这也是「**带 cookies 反而失败、不带 cookies 却成功**」的解释：登录态会走需要 n 签名的 client，
n 解不出来就报那句天书；匿名路径碰巧绕开了它。

### 应用做了什么

- **启动时把这类变量摘掉**（`NODE_OPTIONS` 命中 `--require` / `--import` / `--loader` / `--experimental-loader`）。
  动作会记进日志和 `GET /api/diagnostics.sanitized_environment`，不静默处理。
- **探测阶段真的跑一次 JS 运行时**，而不是只判断文件存在。失败时把原始报错留在
  `dependencies.js_runtime_error` 里 —— 这是「检测到 node 却解不出 n challenge」唯一能查清楚的地方。
- 失败被归类成 `js_challenge_failed`，并在日志里附上诊断与下一步。

### 你可以做什么

1. 点设置面板的「重新自检」，看 `JS 运行时（解析 YouTube 的 n 参数，登录状态下必须）：可用` 这一行。
2. 不可用时：装 Node 18+ 或 Deno，或设置 `YTDL_JS_RUNTIME_PATH`（显式路径；字段是 `AppSettings.js_runtime_path`，见 [003](../ai/bug-fix/003-js-runtime-invisible-outside-path.md)）。
3. 看日志里 `js runtime` 相关行，node/deno 的原始报错就在那里。

## 一份最小的事后复盘清单

1. `data/logs/app.log` 的最后 50 行里有没有 `诊断: …`？直接照抄「建议N」执行。
2. 启动那三行快照里，代理来源、JS 运行时、ffmpeg 三项分别是什么？
3. 「检测代理」是不是 HTTP 200？
4. 「校验 cookies」的结论属于上表哪一行？
5. 上面四步都正常、下载仍 403：把并发降到 1，确认代理出口稳定，再试一次。
