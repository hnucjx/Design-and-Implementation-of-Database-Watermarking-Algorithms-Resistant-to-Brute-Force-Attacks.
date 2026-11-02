# 修复记录（bug-fix）

本目录记录**每一处修复**：一个问题 = 一个文件 = 一个 commit。
目的是让后来的人（包括未来的我们）能回答三个问题：**当时坏在哪、为什么坏、凭什么说修好了**。

> **界面缺陷（能跑、不报错，但呈现错位或不一致）不记在这里，记在 [`ai/ui/`](../ui/README.md)。**
> 判据很简单：有报错、有失败用例、数据被破坏 → bug-fix；功能正常但样子不对 → `ai/ui/`。
> 那条目录同样有自己的「已知但未处理」清单。

## 记录约定

每个文件按固定小节写，宁可写「未验证」也不要写猜测：

| 小节 | 要求 |
|---|---|
| 问题 | 用户能观察到的现象，不是代码现象 |
| 原因 | 具体到文件/行/函数，附**实测**证据（命令与输出），不写「可能是」 |
| 修复方案 | 改了什么、为什么这么改、为什么不做别的 |
| 效果 | 修复前后的可对照结果（数字/日志） |
| 验证 | 新增/修改的测试用例表 + 回归结果 |
| 风险与回滚 | 副作用、回滚命令；有破坏性的必须标红说明 |
| 关联 | 指向相邻缺陷，说明它们是否是同一条失败链 |

`未覆盖 / 如实说明` 小节是**必填的诚实项**：本轮没验到的地方要写清楚，
不能用「已修复」掩盖「没验证」。

## 索引

| # | 标题 | 提交 | 一句话 |
|---|---|---|---|
| [001](001-bot-check-does-not-escalate-profile.md) | bot 校验错误不触发 anti403 profile 阶梯 | `545c846` | `Sign in to confirm you're not a bot.` 既非 403 也非连接重置，被当成「换 profile 也没用」，anti403 阶梯一次都没跑过 |
| [002](002-po-token-provider-has-no-browser-path.md) | PO token provider 拿不到浏览器路径 | `f3f7e50` | 插件要求 `browser_path` 存在，配置项默认 `None` → `wpc-1.1.2 (external, unavailable)` |
| [003](003-js-runtime-invisible-outside-path.md) | JS 运行时只查 PATH | `21ebd9d` | 服务进程 PATH 里没有 node → `JS Challenge Providers: node (unavailable)` → nsig 解不出来 |
| [004](004-empty-subtitle-languages-expand-to-all.md) | 字幕语言为空退化成 `["all"]` | `a579a93` | 空列表变成「拉全部字幕轨」→ HTTP 429 → **整个条目判失败，视频本体也下不到** |
| [005](005-edge-cdp-fallback-touches-the-live-profile.md) | Edge CDP 回退挂载真实 profile | `a482a9e` | 既必然失败（Chromium 拒绝默认数据目录），又曾**清空真实 cookie 库（53 → 0）** |
| [006](006-proxy-is-not-configurable.md) | 应用没有代理配置项 | `d779b52` | 环境变量静默顶掉 Windows 系统代理（实测：坏变量 → 502；修好 → HTTP 200），且界面上看不到也改不了 |
| [007](007-js-challenge-fails-only-when-cookies-are-on.md) | 带 cookies 反而失败：n challenge 被宿主环境打坏 | `5e556f9` | `NODE_OPTIONS=--require=` 让 yt-dlp 启动的 node 在权限模型下 `ERR_ACCESS_DENIED` 退出，报出「The page needs to be reloaded.」；而应用当时**根本不写日志**，连查都没法查 |
| [008](008-return-in-finally-swallows-the-real-error.md) | 收尾阶段出的错被静默吞掉 | `fd8b50e` | `finally` 里的 `return` 丢弃正在传播的异常，而 `_worker` 又没有兜底 → 删除/重启竞态下要么条目永久停在 `running`、要么队列静默少一个消费口，两者都不留一行日志 |
| [009](009-local-dev-port-is-occupied-and-unconfigurable.md) | 8000 被 IncrediBuild 长期占用，启动失败而报错指着「权限」 | `b4f7aba` | `Manager.exe` 以 `0.0.0.0:8000` **独占**监听 → 绑 `127.0.0.1:8000` 得到 `WinError 10013`（权限）而不是 10048（地址已用）；端口值散落多处、Vite 代理还硬编码，换端口会让 `/api` 静默打到 IncrediBuild 上 |
| [010](010-unselectable-probe-raise-skips-the-fallback.md) | 提示「已自动降级到 1080p」，却根本没有开始下载 | `5eb90e3` | yt-dlp 把「选不出格式」实现成**抛异常**，而降级分支挂在「返回 `is_selectable=False`」上 → 整段降级成了死代码；默认清晰度 1440p 遇上只有 1080p 的视频必然命中 |
| [011](011-biliup-refuses-a-token-less-cookie-file.md) | 扫码登录只拿到 cookies，biliup 拒绝开工：投稿必失败且查不出原因 | 本次 | 登录文件里 `token_info.access_token` 是空串，`login_by_cookies` 会**无条件先**拿它去 `oauth2/info` 校验，空串被 B站 判成 `-400 请求错误` → biliup 在发出任何投稿请求之前退出（实测 1.4 s）；同时 `on_log` 没有任何调用方，失败建议却让人去 app.log 搜原文 —— 照做只会扑空 |
| [012](012-preflight-contradicts-itself-and-the-server.md) | 投稿表单的本地预检有两处口径不对：列表还在读就要求用户去选合集、标签回显截断写死 12 个字 | 本次 | 两条 `...(cond ? […] : [])` 各自求值，「正在读取…」与「还没选是哪个合集」在列表未就绪时同时成立 → 表单自相矛盾；而回显截断写死 12（服务端是 `TAG_PREVIEW_MAX = 40`），12 < 单项上限 21，于是那个三元的不截断分支**从未执行过** —— 服务端注释早就点名了这个坑 |
| [013](013-season-section-id-confusion.md) | 「新建合集」之后的归集把合集 id 当成分区 id，平台每次都 `-404` 且重试不自愈 | 本次 | `season/add` 回的是**合集** id，而归集接口的 `sectionId` 要的是**分区** id（实测 `9343273` → `10455347`）。两者都是 `int`、又被同一个变量名装着，替身还一律回 `code:0` —— 本地全绿、真账号必失败；重试读回落库的 `season_id`，于是同一个错值再发一遍 |
| [014](014-a-dead-notify-switch.md) | 「完成后通知」是一个谁都没实现的开关 | `e74fddc` | 字段在 `schemas.py`、开关在 `DownloadOptionsPanel`，但前后端**没有任何一行**消费它（检索 `notify` 只命中定义处）。现按「全局选方式 + 逐任务决定要不要」两层接线：提醒在浏览器里做（Web Audio 合成提示音 + 右下角自动缩回的浮层），后端只存偏好并把逐任务开关从 `options_json` 抬进 `JobRead` |
| [015](015-orphan-items-never-resume.md) | 进程重启后，没下完的任务永远停在「排队中」 | `ead7b81` | 队列是内存里的 `asyncio.Queue`，进程一退 id 就没了，而库里还留着 `queued`/`running` —— 没有任何代码在启动时把两者对上，于是它们既不在队列也不在终态（生产库曾 332 `queued` + 3 `running`）。启动时先改库、再起 worker；默认重新排队并断点续传，`paused` 与被取消的任务保持原样 |
| [016](016-subtitle-lost-while-the-video-is-in-review.md) | 稿件审核期间，字幕被静默丢掉且再也补不上 | 本次 | 稿件进审核时 `x/web-interface/view` 拿不到分P编号 ⇒ `cid` 为空 ⇒ 字幕挂不上，却被记成 `skipped`；而重试只对 `failed` 开放「只补字幕」⇒ 有字幕的稿件字幕永久丢失（实测本机库 2 条）。改为记 `failed`，并把补传判据放宽到「有字幕文件且还没 `posted`」，历史 `skipped` 行也能救回 |
| [017](017-subtitle-status-never-reached-the-ui.md) | 字幕结论从来没有在界面上出现过 | 本次 | 后端三档状态、文案、字段都齐了，但渲染代码（`BiliJobStatus.tsx` / `biliStatus.ts` / `types.ts`）与用例都漏在工作区、没进版本库 ⇒ 用户看不到「CC 字幕」那一行，也没有「重传字幕」入口。本轮补上渲染并新增 5 + 2 条用例；此前 `tsc` 也是红的（测试调用漏了新参数） |
| [018](018-a-long-file-name-looks-like-a-login-failure.md) | 长文件名被报成「标题过长」，又被折成「登录已失效」 | 本次 | B站 的 App 接口把**视频文件名**也按 80 字符校验，报错却说「标题过长」；而失败分类里的 `登录`/`cookie` 是裸词，会命中 biliup 的**成功**日志（「通过cookie登录」）⇒ 真实原因被完全盖掉，用户照提示重扫登录 7 次都无用。实测：stem=80 通过、stem=85 报 21104（标题分别只有 54/71 字符）。修复后同一条记录真实重试成功（`BV1iunv61Eu4`） |
| [019](019-the-title-length-check-never-fires-for-collections.md) | 投稿表单里的标题长度校验对合集从未生效 | 本次 | 合集的预检量的是**模板**（`{title}` 只有 7 个字 ⇒ 永远「合规」），而服务端量的是**渲染后的每一条** ⇒ 前端给出一个假的通过，超限要等提交才被拒；单视频那一侧也只有 `71/80` 一个数字、超限与合规长得一模一样。改为量渲染后的每一条，并在框旁给出「超出 N 个字」与「渲染后最长 N/80（第 X 条…）」 |
| [020](020-the-progress-clock-is-eight-hours-off.md) | 上传进度的时间凭空多出 8 小时 | 本次 | 服务端发的是 **naive 的 UTC 墙上时间**（SQLite 不存时区），而 `Date.parse` 把不带偏移量的串按**本地**时间解析 ⇒ 本机 UTC+8 上「真实 52 秒」显示成「已提交 8:00:52」，切到审核那一刻时钟还冻结在最后一个 tick（于是出现 7:59:59）。用例与夹具里的时间戳**全带 `Z`**，而服务端从来不发 —— 测的是一个真实系统从不输出的形状；`openapi.yaml` 里本就写着 `format: date-time`，只是契约闸门只比字段名不看格式 |
| [021](021-a-dropped-upload-link-looks-like-nothing-at-all.md) | 上传被对端断开，界面上却报「没能识别出具体原因」 | 本次 | Ep 130 的失败输出里一个业务码都没有（`error sending request` / `connection error` / `peer closed connection`），而分类表当时根本没有「传输层」这一类 ⇒ 全表落空、只剩兜底文案，而前端不渲染 `log_tail`，用户手上就只有「不知道」。新增 16 条传输类 needle；能证明「死在把字节传给 CDN 这一步」（同一行里有 `bilivideo.com`）时才敢承诺「重试不会投重」，否则退回保守说法。顺带修掉次序缺陷：CDN 侧 `404 Not Found` 本来会被读成「本地找不到文件」 |
| [022](022-a-subtitle-count-nobody-can-read.md) | 「CC 字幕已上传（239 条，en）」—— 239 是什么 | 本次 | 239 是 BCC `body` 的**段数**，数字本身没算错（平台回读 239 条逐条一致），但它既不是字数也不是句数（这份文件 122 句），旁边还跟着平台码 `en`。虚高的根因：滚动字幕的 **10 毫秒过渡条**没被丢掉（241 条里 119 条是它，`merge_repeated_cues` 只合并整条同文，合不掉）。修法：`drop_transition_cues()`（≤50ms **且**文字被相邻 cue 覆盖）+ 文案改成「CC 字幕已上传（英文），观众可在播放器里开关」。全语料 28 个字幕：20385 → 10973 条（丢的 100% ≤20ms，0 文字丢失、0 末条时间改变） |
| [023](023-the-open-with-dialog-that-never-opened.md) | 点「打开字幕」什么都没有／只弹「若要更改默认应用…」的提示框 | 本次 | 系统的「打开方式」不是稳定依赖，而两条入口会**各自**失效：`OpenAs_RunDLL` 曾在用户机器上可用，现在连宿主进程都不起；`SHOpenWithDialog` 在 5 种标志位 × 3 种后缀下都只给 680×118 的提示框。判据错在「窗口存在」—— 提示框的类名同样是 `Shell_Dialog`。最终做成**三级链**（`SHOpenWithDialog` → `OpenAs_RunDLL` → 自带 tkinter 对话框），用**高度 ≥ 300** 区分选择器与提示框，降级框就地 `WM_CLOSE`；自带对话框的候选 = 系统登记的处理程序 + 本机真实存在的常见编辑器（记事本 / VS Code / CLion / Visual Studio…）+「浏览…」（从 Program Files 起步）。真机 1.58s 落到兜底、浏览器 8/8；整条链期间任务行显示「正在打开…」 |
| [024](024-retry-grouping-never-asks-the-platform.md) | 「重试归集」读的是本机旧记录，从不去平台补分P编号 | 本次 | 投稿那一刻稿件在审核 ⇒ `x/web-interface/view` 回 `-404` ⇒ `cid` 为空；而此后**没有任何路径再去问平台一次**（`retry_upload` 对已成功的稿件直接回「没有重复投稿」，归集读的是库里那几行）⇒ 合集永远停在「等待归集」。实测 Ep 125：平台侧 `state=0`、`cid=42927065206`，本机 `cid=NULL`、界面说「排队审核」，点重试 22 毫秒回同一句话、一个请求都没发，而回执写「已重新尝试归集」。修法：重试**先按 bvid 回读平台**补 `cid` 再归集，回包 `action` 分 `grouped`/`failed`/`waiting`，`waiting` 明说「这次没有发起归集」。修后同一次点击真的把它加进了合集 9355267 |
| [025](025-requests-without-a-deadline.md) | 一条挂起的连接 = 一句永不消失的「正在读取…」 | `5c4511d` | `request()` 直接 `await fetch(...)`，而 `fetch` **没有超时**：后端接了连接却不回话时 Promise 永不 settle，界面停在 loading 上、没有异常也没有日志（此前只有 B站 体检那一处补过 12 s 计时器）。改为统一挂 `AbortController` 看门狗（默认 60 s / 解析 180 s），超时给一句可照做的话并真的掐掉请求 |
| [026](026-a-resolution-that-silently-means-best.md) | 清晰度写成 `1440P`，静默下了最高画质 | `5d5b228` | `DownloadOptions.resolution` 没有任何校验，而 `format_selector()` 认不出大小写/漏 `p` 的写法时会回落成 `bv*+ba/b`（最佳可用），`should_look_for_fallback()` 同样认不出、连降级都不介入 ⇒ **不按用户要求做，也不告诉任何人**。三个入口（建任务 / 重启 / 默认清晰度）加归一化与校验，重启那条必须单独挡（`model_copy` 不触发校验） |
| [027](027-sse-disconnect-keeps-the-listener.md) | SSE 断开后订阅者没注销，每个关掉的标签页留一个无界队列 | `ba10f37` | `async for` **不会**关闭子异步生成器（PEP 525 老坑）⇒ `EventBroker.subscribe()` 的 `finally` 要等 GC 才跑，期间断开的订阅者仍被逐个喂消息。路由里显式 `aclose()` 订阅对象，注销判据仍只在 `EventBroker` 一处 |
| [028](028-upload-timestamp-test-races-the-worker.md) | 时间戳用例与 worker 抢时序，同一份代码跑三次两种结果 | 本次 | 用例用的是**真的** `BiliManager`，`POST` 返回时上传还在跑；worker 收尾写 `finished_at`，与用例手工写值抢先后 —— 谁在后谁生效。证据是只有 `finished_at` 被改成「现在」而 `started_at` 没动。夹具里本来就有 `wait_until_finished`，这条漏了调用；改后整文件 3/3 绿、全量 2/2 绿 |
| [029](029-open-with-list-is-never-read.md) | 自带「打开方式」的清单里，系统登记的一个都不在里面 | `—` | `_registered_handlers` 读 `OpenWithList` 时用 `name.isdigit()` 判「不是程序项」，而真实键名是 `a`/`b`/`c`/`MRUList` ⇒ **两个来源恒为空**；即便过滤修好，值也只是裸 exe 名（`QQ.exe`），`is_file()` 仍为假；第三个来源在 ProgID 没有显示名时把 GUID 印给用户。修法：只跳过 `MRUList`、裸名经 `PATH` / `App Paths` 解析、标识不当作名字 |
| [030](030-the-layer-check-blames-the-wrong-thing.md) | 分层校验把「反向依赖」报成「模块没登记」 | `—` | `imports_of` 对 `from ..pkg import submod` 只记到 `pkg`（真实依赖是 `pkg/submod`）⇒ 目标未登记，先撞上「未登记」分支，方向判据根本没执行。照着这条报错去补文档，反向依赖就被永久掩盖。修法：用「该子模块是否真实存在」把它与符号分开；顺手补 15 条用例（这个闸门此前零测试） |
| [031](031-bili-requests-fall-into-an-ipv6-black-hole.md) | 点「扫码登录」「重新体检」都只得到一句超时 | `—` | 两条投诉看着无关，其实是同一个根因：`bili_http` 的 `timeout=20` 只是**单次 socket 操作**的预算，而 `create_connection` 会把解析出的每个地址都试一遍（本机 6 个 IPv6 全黑洞）⇒ 实测 **160 秒**。于是「重新体检」的 12 s 预算永远先到（每次只剩离线结论），「扫码登录」挂满 60 s 被前端掐断，而用户看到的是**上一条重试请求**遗留的页面级红条。修法：只解析 IPv4 + 整段共享一个截止时刻；前端预算改为 25 s 并**必须大于后端那 20 秒**，探测失败也退回离线结论，超时消息点名动作，开始动作时清掉旧红条 |

## 本轮（2026-10-01）背景

起点是上一轮的性能修复与真实验收：验收时发现「部分条目整体失败，且它们根本没在下载视频」。
顺着这条线挖下去，发现 001~003 是**同一条失败链**上的三个环节——

```
提取 YouTube 页面
  ├─ 需要 JS 运行时解 nsig        ← 003（探测不到 node）→ 007（node 看得到却跑不起来）
  ├─ 需要过 bot 校验              ← 001（过不了时不会去试 anti403 profile）
  └─ 需要 PO token               ← 002（provider 因缺 browser_path 而 unavailable）
```

**任何一环断掉，「下载 YouTube 视频」就整体不可用。** 三个修复必须同时生效才有意义，
所以它们虽然各自独立提交，效果要合起来看。

004 与 005 是验收过程中暴露的存量缺陷，与上面三条链无直接关系，
但都会让「下载」这件事对用户失效（一个炸 429、一个破坏用户数据）。

006 是这条链的**前置条件**：上面三环再正确，请求到不了 YouTube 也没用。
它也纠正了上一轮分析里「直连超时」那个错误结论 —— 真因是环境变量把系统代理顶掉了，
而当时没有任何地方能看出「应用实际用了哪个代理」。

007 是这条链的**收尾**，也是三次「查不出来」的总结：它发现 003 之后 node 虽然能被看见、
却仍然**跑不起来**（宿主往 `NODE_OPTIONS` 里塞了 `--require`），而真正的报错被吃掉、
抛给用户的是一句 `The page needs to be reloaded.`。修它必须同时补上三件基础设施：
**日志落盘**（在此之前应用从不写日志，INFO 被静默丢弃）、**真跑一次的运行时探测**、
**把异常翻译成下一步的建议**。三者缺一，同类问题还会再花一轮才能查清。

## 本轮（2026-10-01 后续）背景：一次启动失败牵出的两处

这一轮的输入只有**一份启动日志**，而日志的头和尾各躺着一个彼此无关的问题：

```text
backend\app\job_manager.py:630: SyntaxWarning: 'return' in a 'finally' block        ← 008（日志开头）
backend\app\job_manager.py:647: SyntaxWarning: 'return' in a 'finally' block        ← 008
...
ERROR uvicorn.error | [Errno 13] error while attempting to bind on address
('127.0.0.1', 8000): [winerror 10013] 以一种访问权限不允许的方式做了一个...尝试。   ← 009（日志结尾）
```

- [008](008-return-in-finally-swallows-the-real-error.md) 是**症状**：两条 `SyntaxWarning`。
  真问题在语义层 —— `finally` 里的 `return` 会吞掉正在传播的异常，而 `_worker` 又没有兜底，
  于是「条目卡在 running」和「队列静默少一个并发口」是同一件事的两种结局。
- [009](009-local-dev-port-is-occupied-and-unconfigurable.md) 是**服务根本没起来**。
  真因与「权限」无关：8000 被 IncrediBuild 的 Coordinator 长期独占，Windows 因此返回 10013；
  而这条报错之所以把人引偏，正是因为此前没有任何地方能看出「谁占了它」。

两者没有因果关系，各自独立提交、可独立回滚。共同点是同一个毛病：
**报错文本指向的地方，和真正坏掉的地方不是同一处** —— 与 001~007 那一轮的主题一脉相承。

## 本轮（2026-10-01 晚）背景：一份界面提示牵出的一处

输入是用户在 Edge 里下载 `https://youtu.be/hTdSU7q5WCo` 时看到的两句话：

```text
failed · 0/1 完成 · 当前没有 1440p 的视频，低于选定分辨率的最高可用分辨率是 1080p。
检测到 1440p 清晰度，但该清晰度当前没有可下载的视频/音频组合，已自动降级到 1080p。
```

[010](010-unselectable-probe-raise-skips-the-fallback.md) 的特殊之处在于 ——
**它是一处「看起来已经处理了」的缺陷**。降级逻辑写得相当完整：解析元数据分类原因、
算降级候选、用降级后的清晰度**再探一次**、写回状态与文案。
但它的入口条件挂在「探针**返回** `is_selectable=False`」上，而真实故障里探针是**抛异常**，
于是这一整段从未执行过。

与 [008](008-return-in-finally-swallows-the-real-error.md) 属于同一主题：
**分支本身写对了，触发条件写错了。** 008 在收尾阶段（`finally` 里的 `return` 让收尾走不到），
010 在下载前（降级挂在错误的信号上）。两者的症状也同形 ——
代码「什么都没做」，而日志与界面都在说「已经做过了」。

## 本轮（2026-10-17）背景：一次投稿牵出的一处

输入不是报障，而是**给宣传视频投稿**这件本来很小的事。手动打平台接口时，新建合集后的归集
返回 `-404 啥都木有`，换成分区 id 立刻成功 —— 于是回头读代码，发现两条归集路径都把
**合集 id** 当成了 **分区 id**。

[013](013-season-section-id-confusion.md) 的特殊之处是**它哪里都「写对了」**：端点路径、
参数名（`sectionId`，camelCase）、csrf 走 query —— 每一处都对着社区实测记录核过，
连替身都把参数形态钉成了用例。错的只有**值的来源**：`season/add` 回的是合集 id。
这类缺陷没有死代码可查、没有类型可依（两个值都是 `int`），只能靠「平台上这个字段是什么意思」
去核；而它的表现又恰好是一条最容易被当成偶发故障的错误码。

与 [011](011-biliup-refuses-a-token-less-cookie-file.md) 同一类：**本地用例全绿，真账号必失败。**

## 本轮（2026-10-18）背景：候选池里的一条，落地时发现是「少了一整个步骤」

输入不是报障，而是候选池的 [D-01](../plan/brainstorming.md)：生产库曾积 332 个 `queued` + 3 个 `running`。
所以这轮没有「用户看到的错句」可拆，第一步是把「为什么没人消费」这句话变成代码事实 ——
队列是内存的、重排队的入口只有三个且都由用户动作触发、启动路径上一条都没有。

[015](015-orphan-items-never-resume.md) 与前三条（[008](008-return-in-finally-swallows-the-real-error.md) /
[010](010-unselectable-probe-raise-skips-the-fallback.md) / [011](011-biliup-refuses-a-token-less-cookie-file.md)）
属于同一个更大的主题：**代码什么都没做，而界面/日志看起来已经做过了**。
008 是收尾被 `return` 吃掉、010 是降级挂在永不出现的信号上、011 是令牌字段空着被当成功，
015 更彻底 —— **根本没有人被安排做这件事**。这四种形态的共同判据只有一条：
验证一条分支/流程是否真的执行过，必须给**正面证据**（这里是 `jobs_recovered` 事件与首帧字节数），
「没有报错」证明不了任何事。

另外这轮遇到一个协作事实：同一时间另一个会话正在改 `schemas.py` / `job_read_model.py` / `api.md`，
所以 `git add` 只能显式列文件，锚点漂移也只能修「由本轮改动造成」的那 38 处 —— 剩下 36 处指向
他人在途文件，按原样留着（见下方「已知但未处理」第 24 条）。

## 本轮（2026-10-28）背景：一次全量审查，用覆盖率找缺口而不是靠印象

输入不是报障，而是「全面审查 + 补测试」。做法是先跑一遍覆盖率（`coverage run -m pytest`，
TOTAL 89%），再逐个看**未覆盖的行本身**是不是也可能错 —— 覆盖率只是入口，判据仍是读代码。

产出三个缺陷，都不在某条既有记录里：

- [025](025-requests-without-a-deadline.md)：**41 个调用全都没有超时**。此前记的是「三条 B站
  只读接口缺兜底」，实际范围大一个数量级；症状是「没有终点的 loading」，即「坏了也不说」。
- [026](026-a-resolution-that-silently-means-best.md)：清晰度写成 `1440P` 就静默下最高画质。
  一个大小写穿过三层（schema 不校验 → 选择器回落 `best` → 降级判定也不认），
  三处都没错，合起来是「换了画质且无人知晓」。
- [027](027-sse-disconnect-keeps-the-listener.md)：`async for` 不关闭子异步生成器，
  每个关掉的标签页留下一个无界队列直到 GC。

补的测试集中在**此前没有任何直接用例**的四个模块：`ytdlp_formats`（35 条，选择器/信息字典/
降级候选的全部分支）、`bili_http.UrllibTransport`（11 条，对着本机 HTTP 服务器验真实发送）、
`EventBroker` + SSE 路由（5 条）、`job_artifacts` 的 409 与异常映射（15 条），
外加 `routers/jobs` 的「查不到就 404」这一族（13 条参数化）。

**一条方法论教训**：并行跑前端用例时，vitest 会**静默少跑文件**却仍报「全部通过」
（同一条命令四次运行得到 21/20/19/15 个文件）。核数必须加 `--no-file-parallelism`，
详见「已知但未处理」第 25 条 —— 这条比本轮修的三个缺陷都更值得记住，
因为它会让**任何**一次「测试全绿」变成不可信的结论。

## 本轮（2026-10-28 晚）背景：靶子是**重构过的模块**

输入是「全面审查 + 重点看最近几次提交里重构过的模块」。这一天两次重构（[011](../refactor/011-flat-modules-into-feature-packages.md)
后端分包、[013](../refactor/013-frontend-feature-directories.md) 前端分目录）都是**纯移动**：只挪不改名、
行数不变，验证数据也齐全（用例数逐数相同）。纯移动的回归风险只有一个方向 ——
**靠 `__file__` 深度或字符串路径推算**的东西：

- `contracts/config.py` 的 `REPO_ROOT` 从 `parents[2]` 改成 `parents[3]`，改对了，
  但**没有任何测试守它**：`test_dev_server.py` 那条 `env_file == REPO_ROOT / ".env"` 是自证
  （`env_file` 本来就是用它定义的），改错照样绿。改错的后果不是导入失败，而是默认目录悄悄
  挪到 `backend/data` —— 应用照常启动、建一个空库，旧任务像是全丢了。新增
  `test_config_paths.py`（3 条）从**外部事实**判：仓库根下必须有 `backend/app/main.py` 与
  `frontend/package.json`。
- 模块路径的**字符串**形态（monkeypatch 目标、日志里的模块名）导入期不报错，只在某条分支
  被触发时才炸。逐个检索了 `app.<扁平名>` 的残留，只剩 `app.main`（没搬过），干净。
- 重构 011 顺手重写了 `check_layers.py`，而它**零测试** —— 见 [030](030-the-layer-check-blames-the-wrong-thing.md)。

主产出是 [029](029-open-with-list-is-never-read.md)：`host/system_open.py` 是覆盖率的最后一块
洼地（**40%**，562 行里 338 行未覆盖），也是本日改动最多的模块之一。它的「打开方式」候选清单里
两个来源恒定为空，而**那条测试恰好把该函数整个替换成了假的**，所以一直全绿。


## 已知但**未处理**的问题（下一轮候选）

1. ~~**应用没有代理配置项。**~~ → 已由 [006](006-proxy-is-not-configurable.md) 处理。
   遗留：PAC/自动配置脚本不支持；「来源=环境变量」时 aria2c 仍拿不到代理。
2. **`_close_browser_for_cookie_import()` 用 `taskkill /IM msedge.exe /F /T`**，
   会杀掉用户全部 Edge 进程（含未保存标签页）。虽属用户显式同意，破坏面仍过大。见 005 遗留风险。
3. **视频级端到端仍未验收通过。** 001~003 把 PO token provider 从「不可用」推进到「可用」，
   006 让请求能真正出网，007 让 JS 求解真的跑得起来；但本轮只验到 **元数据提取成功**
   （带/不带 cookies 都能解析、`LOGGED_IN` 正确），「媒体流下得下来 + 合得起来」仍需在干净的
   代理环境下单独验收。
4. 同一 item 内多条字幕轨仍是串行请求，若 YouTube 收紧限流可能还需请求间隔控制（见 004 关联）。
5. **环境净化只覆盖 `NODE_OPTIONS`**（007 遗留）。其它同样能打坏子进程的宿主注入没有处理 ——
   只处理实测造成故障的那一个，不凭想象扩大范围。
6. ~~**`docs/development.md` 的环境变量表缺两个字段**：`YTDL_JS_RUNTIME_PATH` 与 `YTDL_PROXY`~~
   → 已处理（2026-10-02 文档同步轮）：两行已补进表内，`YTDL_JS_RUNTIME` 这个名字的错误写法
   也已在 `docs/technical.md` 点明「设了不生效」。
   **遗留**：是否反过来把 `AppSettings.js_runtime_path` 改名成 `js_runtime` 属于产品决定，没有动。
7. **端口预检只在 `python -m app` 这条入口生效**（009 遗留）。继续敲
   `python -m uvicorn app.main:app --port 8000` 仍然是原始报错；`docs/openapi.yaml` 的 server URL
   也仍是硬编码 `8000`，没有跟随 `YTDL_API_PORT`。
   → **后半句已处理**：`docs/openapi.yaml` 现在把该 server 明确标为「默认端口」，
   并在 `info.description` 里写明端口来自 `YTDL_API_PORT`、以启动日志打印的地址为准。
   OpenAPI 的 `servers[].url` 本身必须是具体 URL，无法表达变量，所以只能这样降级表达。
   前半句（裸 uvicorn 无预检）保持未处理。
8. **008 的触发路径没有在真实界面里出现过**：删除竞态 + 收尾出错这个组合只在测试里构造过，
   没有真的在浏览器里删掉一个正在下载的条目来观察。
9. **`port_available()` 存在「探测通过、随后被抢走」的竞态**（009 遗留），没有加锁也没有重试 ——
   预检只负责把话说清楚，不构成占用保证。
10. **`requested_resolution_unselectable` 的文案仍然名实不符**（010 遗留）。该原因在**下载阶段**
   被标注时，文案会写「已自动降级到 X」，但设计上那里只做标注、**不**自动重下
   （见 [architecture.md](../../docs/architecture.md)），用户必须自己点重启。
   010 修掉了「预检抛异常被误判成该原因」这条错路，但文案本身没动 ——
   改它要同步前端 fixture 与 `docs/openapi.yaml` 的示例值，属另一件事。
11. **预检仍会向 stderr 打一行 `ERROR: ... Requested format is not available.`**（010 遗留）。
   实测确认 yt-dlp 在 `trouble()` 里先 `to_stderr()` 再抛异常，`quiet=True` 拦不住。
   对用户无害（任务其实成功），但看起来像报错。修法是在探针上挂一个静默 logger，未做。
12. **`is_requested_format_unavailable_error` 是纯字符串匹配**（010 遗留）。yt-dlp 一旦改这句英文，
   两处归一同时失效，缺陷会以「降级不执行」的形式回归。有意保持窄匹配（宁可失效也不吞真故障），
   但没有版本探测或第二个关键词兜底。
13. **删任务不连带删 B站 上传记录**（bilibili 分支移植发现）。`JobManager.delete()` /
   `delete_items()` 只删 `JobEvent` / `JobItem` / `Job`，`bili_upload` / `bili_season` 会留下；
   而 `read_all_job_uploads()` 的 job 集合正是从这两张表取并集，于是
   `GET /api/bilibili/uploads/summary` 会一直为已不存在的任务回记录，行数也无限累积
   （界面上看不见：前端按 `/api/jobs` 渲染徽标，所以这是个静默的存储面损伤）。
   修法不止一行删表 —— 投稿跑在 `BiliManager` 的另一个 worker 上，删任务时可能正有一条在途，
   删完再写回等于白删；要先给 `BiliManager` 加**按 job 取消在途上传**的能力
   （现在还只有整体 `stop()`）。详见
   [03-implementation.md §5.5](../feature/bilibili-upload/03-implementation.md#55-删任务不连带删上传记录)。
14. **令牌的自动续期（`renew_tokens`）没有实测过**（011 遗留）。`token_info` 的有效期是 180 天，
   到期后 biliup 会拿 `refresh_token` 自己去续；我们只保证了 `platform="BiliTV"`（它靠这个挑 appkey）
   与 `refresh_token` 已写入，**没有构造过「令牌将过期」的状态来跑那条路**。
   真要验收得手工把 `expires_in` 改小或等 180 天。风险是「某天开始投稿又变成 -400」——
   届时界面上的凭据体检（011 新增的那一项）会把它显出来，不会再静默。
15. ~~**B站 的三条只读请求没有超时与 `AbortController`**（012 遗留）~~ → 已由
   [025](025-requests-without-a-deadline.md) 处理，且范围更大：排查时发现**全部 41 个调用**
   都没有超时，不只是那三条。现在 `request()` 统一挂看门狗（默认 60 s / 解析 180 s）。
   **遗留**：60 s 是按「比所有合法阻塞都长」定的，没有实测 P99；调用方若自己传
   `init.signal` 会被覆盖（目前无人传）。
16. **归集时「这批稿件已经在合集中」被当成失败**（013 遗留）。实测重复添加同一条稿件，
  平台返回 `code=20080 message=当前稿件已存在在合集中`，而 `add_episodes` 把任何非零 code
   都当失败 → 一次**实际已成功但响应丢失**的归集，重试会落成「视频没能加进去」。
   没顺手判成功，是因为**混合批的语义没验证**：一批里既有已存在的、又有新的时，
   平台是整批拒绝还是逐条处理？前者会让「判成功」变成谎报。要修得先在真账号上验这一条。
17. **`/api/bilibili/seasons` 只读第一页**（013 遗留）。`list_seasons` 固定 `pn=1, ps=30`，
   账号下合集多于 30 个时第 31 个之后的**在界面上选不到**（本机账号 27 个，还没撞上，
   代码面是确定的）。`find_season_section` 已加翻页，这条没动。
18. **提示音没有在真实浏览器里听过**（014 遗留）。合成参数（C6→G6、0.5 s、峰值 0.16）
   与「浏览器是否放行音频」都只在替身层面验过（起振 2 次 / `blocked` 不抛）；
   真机上「提交任务 → 十几分钟后切回来 → 到底响不响」没有实测。失败路径是静默的。
19. **提醒只覆盖 `succeeded` / `failed`**（014 遗留）。`paused` 不是终态所以不提醒，
   但用户手动暂停后忘了就没有任何后续提示。这是有意的选择，不是遗漏 ——
   记在这里是因为「有意」和「忘了」在半年后看不出来区别。
20. **浮层没有做过浏览器级测量**（014 遗留）。右下角 20px 间距、360px 上限、`z-index: 60`
   是按 `.modal-backdrop`（50）推的，没量过它会不会盖住任务中心右下角的按钮
   （`ai/ui/` 那套 CDP 探针本轮没跑）。
21. **两份进程同时启动会重复恢复同一批孤儿条目**（015 遗留）。端口预检默认只允许一个实例，
   但 `--auto-port` 可以绕过；此时两个进程会同时重新排队、同时往同一个 `.part` 写。
   修复前也有同类风险（两人同时点同一个下载），本轮没加锁。
22. **恢复不做分批也不做聚合**（015 遗留）。一次启动可能立刻把几百条塞进队列（受并发限制，
   但「一开应用就开始跑几十 GB」对用户是突然的），且 300 个任务被恢复就写 300 条
   `jobs_recovered` 事件。要改得先想清楚「分批」的判据是什么。
23. **`YTDL_ORPHAN_RECOVERY=fail` 不清理 `.part`**（015 遗留）。标失败后残留的临时文件会一直留着，
   下次重下时还能被续用 —— 所以**没有**顺手删（那是用户磁盘上的东西）。
24. **`check_doc_anchors.py` 报的 36 处漂移不属于本轮**（2026-10-18 记录）。它们指向
    `schemas.py`（29）、`JobQueue.tsx`（4）、`api.ts`（2）、`output_paths.py`（1）——
    都是另一个会话当时**未提交**的改动，`docs/api.md` 里还混着他们的 `open-subtitle` 新段落，
    所以本轮只修了由 `job_manager.py` / `config.py` 位移造成的 38 处，没有 `--fix` 全局重算。
    等那批改动落地后应当再跑一次。
25. **`.hint.hint-error` 在 `styles.css` 里没有对应规则**（2026-10-21 记录，B站 上传表单遗留）。
    `BiliUploadDialog.tsx` 在「读不到账号里的合集」时用它输出原因与下一步，但没有这一档样式，
    那句错误提示就以普通灰字呈现 —— 和一条普通说明没有区别。修法是一行
    `color`（照 `.bili-problems` 的 `#b42318`），属另一处改动，未顺手做。
26. **`.char-count` 的 `color: #657187` 从未生效**（2026-10-21 探针实测）。
    `.field > span, .field-label`（特异性 0,1,1）压过 `.char-count`（0,1,0），
    于是所有「已填 N / 上限」实际是 `.field > span` 的 `#334155`。
    本轮只保证**超限档**（`.char-count.is-over`，0,2,0）压得赢、红色真的显示（探针量到
    `rgb(180,35,24)`）；合规档改不改颜色属另一处改动，未顺手做。
25. **并行跑前端用例时 vitest 会静默少跑文件**（2026-10-28 实测，影响的是测试闸门本身）。
   同一条 `npx vitest run --environment jsdom` 连续运行，得到过 **21 文件/286 条**、
   **20 文件/275 条**、**19 文件/261 条**、甚至 **15 文件/172 条** 四种结果，**全部报「passed」**：
   被丢掉的用例不会变成失败，只会消失。加 `--no-file-parallelism` 后稳定复现 21/286。
   怀疑是本机并行 fork 的 worker 被静默回收（32 位？内存？未见报错）。**判据**：
   在这台机器上核数一律加 `--no-file-parallelism`，且不相信「文件数变少了但全绿」。
   未改 `package.json` 的 `test` 脚本（那是所有人的默认速度，要不要默认串行是另一个决定）。
26. **老数据里无法解释的清晰度仍会静默变成「最佳可用」**（026 遗留）。入口已收紧，
   但库里早于校验的行（例如 `hd`）恢复出来仍是 `best`；只有大小写差异（`1440P`）会被救回。
   要做数据迁移得先枚举存活行，收益不明。
27. **`1440p60` 这类带帧率的写法被拒**（026 遗留）。yt-dlp 支持 `[fps=60]`，本工具没有帧率选择；
   现在会明确报错而不是静默降级。要支持得先想清楚界面怎么表达「清晰度 × 帧率」。
28. **SSE 没有背压**（027 遗留）。慢消费者仍会积压（无界队列，有意为之：不能阻塞其他订阅者）。
   本轮只修了「断开后不注销」，没动「连接着但读得慢」。
29. **自带对话框列不出 AppX 包程序**（029 遗留）。`…!App` 这类包标识要用
   `IApplicationActivationManager` 启动，而自带对话框是 `subprocess.Popen([exe, path])`。
   现在明确丢掉而不是列出来点不动；系统那两级仍能列出它们。
30. **裸 exe 名解析不出来就没了**（029 遗留）。本机 `.mp4` 的 `QQ.exe` / `Everything.exe`
   既不在 `PATH` 也不在 `App Paths`，实测解析为 `None`。再往下只能全盘搜索，不值得。
