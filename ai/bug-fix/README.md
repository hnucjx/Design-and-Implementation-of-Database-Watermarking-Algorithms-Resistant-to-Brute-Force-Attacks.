# 修复记录（bug-fix）

本目录记录**每一处修复**：一个问题 = 一个文件 = 一个 commit。
目的是让后来的人（包括未来的我们）能回答三个问题：**当时坏在哪、为什么坏、凭什么说修好了**。

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
6. **`docs/development.md` 的环境变量表缺两个字段**：`YTDL_JS_RUNTIME_PATH` 与 `YTDL_PROXY`，
   而表头写着「列出全部字段」。同一处还发现文档把 `YTDL_JS_RUNTIME_PATH` 误写成 `YTDL_JS_RUNTIME`
   —— 实测那个名字设了**不生效**（`AppSettings().js_runtime_path` 仍为 `None`），本轮已在
   `docs/troubleshooting.md` 顺手改正。是否反过来把字段改名成 `js_runtime` 属于产品决定，没有动。
7. **端口预检只在 `python -m app` 这条入口生效**（009 遗留）。继续敲
   `python -m uvicorn app.main:app --port 8000` 仍然是原始报错；`docs/openapi.yaml` 的 server URL
   也仍是硬编码 `8000`，没有跟随 `YTDL_API_PORT`。
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
