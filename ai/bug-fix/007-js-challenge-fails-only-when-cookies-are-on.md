# 007 带 cookies 反而解析失败：n challenge 被宿主环境打坏，而报错完全指不到病因

## 问题

三个都能被用户看到、且**互相看起来毫不相干**的现象，其实是同一个根因：

1. **导入 cookies 之后反而下载/解析失败** ——「不带 cookies 能过，一带 cookies 就报错」。
2. 报错是 `ERROR: [youtube] aqz-KE-bpKQ: The page needs to be reloaded.`
   —— 这句话既没提 JS、也没提网络、更没提 cookies，用户唯一的反应只能是「那我该干嘛」。
3. **用户提交问题后，我们自己也查不出来**：因为应用**从不写日志**（见「原因」第 2 条），
   而被日志吞掉的那行 `proxy resolved` / JS 运行时报错，恰恰是唯一的线索。

第 3 条是本条记录真正想修的：一个**不可诊断**的失败，比一个明确的失败昂贵得多。

## 原因

### 1) 根因：`NODE_OPTIONS` 打坏了 yt-dlp 启动的 JS 求解器

YouTube 的 `n` 参数（nsig）需要跑 JS 才能解出来。yt-dlp 这样启动 JS 运行时：

```
node --experimental-permission --no-warnings=ExperimentalWarning -
```

`--experimental-permission` 打开 Node 的权限模型（默认拒绝一切文件读取）。而 **`NODE_OPTIONS` 是每个 node 进程都会读的环境变量** —— 本机宿主（WorkBuddy 桌面壳）往里面塞了：

```
NODE_OPTIONS=--require="C:/Program Files/WorkBuddy/resources/app.asar.unpacked/cli/vendor/shim/node-language-shim.cjs"
```

于是那个补丁被塞进上面这个被沙箱化的 node 进程，Node 拒绝读取并退出：

```
Error: Access to this API has been restricted. Use --allow-fs-read to manage permissions.
  code: 'ERR_ACCESS_DENIED', permission: 'FileSystemRead',
  resource: '...\node-language-shim.cjs'
```

退出码非 0 → n challenge 解不出来 → 某些 client 的格式被判定为「没有可用 URL」→ yt-dlp 抛出的是
**完全指不到病因**的 `The page needs to be reloaded.`

**为什么「带 cookies 反而失败」**：登录态会走需要 n 签名的 client；匿名路径碰巧绕开了它。
所以症状看起来像「cookies 有问题」，实际与 cookies 毫无关系。

### 2) 为什么当时查不出来

`YtDlpService.__init__` 里的 `logger.info("proxy resolved: ...")` 是**唯一**能看出「走没走代理」的地方，
但项目**从未配置过 logging**。于是 root logger 没有 handler，`logging.lastResort`（级别 WARNING、无格式）接手：

- INFO 级别的诊断信息 **被静默丢弃**；
- WARNING 只剩一行没有时间戳、没有模块名的裸消息。

配套地，JS 运行时探测只判断「文件在不在」（[003](003-js-runtime-invisible-outside-path.md) 的遗留）：
`js_runtime: true` 有可能是在一个**跑不起来**的 node 上得出的，于是「检测到 node 却解不出 n challenge」
在界面上完全无从体现。

## 修复方案

### a. 摘掉会打坏子进程的环境变量（`backend/app/runtime_env.py`）

```python
HOSTILE_NODE_OPTION_FLAGS = ("--require", "--import", "--loader", "--experimental-loader")

def sanitize_environment(environ=None) -> list[SanitizedVariable]: ...
def describe_environment_risks(environ=None) -> list[dict]: ...
```

`create_app()` 启动时调用，并逐条 `logger.warning` 说明摘除范围。**只摘命中强加载开关的 `NODE_OPTIONS`**，
其它环境变量一律不动 —— 这不是「清理环境」，是针对一个具体破坏面的定点处理。动作同时写进
`/api/diagnostics.sanitized_environment`，不静默处理。

### b. 探测期真的跑一次 JS 运行时（`ytdlp_service._probe_js_runtime`）

- Node 先用 `--experimental-permission` 跑一次探针（**与 yt-dlp 相同的权限模型**），再退回普通 `-e`；
  Deno 用 `eval`。
- 探测按 `js_runtime_path` 记忆化；`reset_js_runtime_cache()` 供「重新自检」。
- 失败原因留痕：`dependencies.js_runtime_error`（原始报错）与 `js_runtime_candidates_rejected`（逐个候选）。
- 整个候选探测被 `try/except Exception` 包住 —— **探测本身出错不能把服务带崩**，只算「这个候选不可用」。

### c. 把异常翻译成「该做什么」（`backend/app/error_advice.py`）

`advise()` 只依赖异常链上的文本（`exception_chain()` 用 BFS 展开 `__cause__`/`__context__`，去重防环），
判定顺序**从最具体到最泛**：JS challenge → cookies → 代理 → 媒体流被挡；无法归类时返回 `None`，不硬凑。

每个结论给出 `code` + 一句中文结论 + **可执行**的 `next_steps`，并渲染成日志里的：

```
诊断: js_runtime_challenge_failed —— YouTube 的 JS challenge（n 参数）解不出来，登录态下的可用格式被判定为空
      原因: ... 当前 JS 运行时自检失败：... ERR_ACCESS_DENIED
      建议1: 按诊断里的提示修复 JS 运行时（Deno 或 Node），修复后在设置里显式填写路径并重新自检
      建议2: ...
```

两处调用点（`YtDlpService` 的 profile 失败、`JobManager._log_item_failure`）都用 `try/except` +
`getattr(..., None)` 保护：**日志与诊断绝不能改变重试/失败行为**（测试里的 fake service 没有这个方法，
第一版就因此挂掉了 6 个 API 测试）。

### d. 日志一定会落盘（`backend/app/logging_setup.py`）

- `create_app()` 第一件事就调 `configure_logging(app_settings.data_dir / "logs")`；
- 控制台 + `RotatingFileHandler(data/logs/app.log, 2 MiB × 3, UTF-8)`；
- 格式 `时间 级别 模块 | 消息`；
- 同一个文件 handler 显式挂到 `uvicorn` / `uvicorn.error` / `uvicorn.access`
  （它们默认 `propagate=False`，不挂就漏启动与访问日志）；
- 模块级 `_configured` 保证幂等（`create_app()` 会被反复调用）；
- 目录不可写时降级为「仅控制台」并 `root.warning`，不让应用起不来。

### e. 把「问不出来的问题」变成能点出来的自检（新增 3 个接口）

| 接口 | 回答 |
|---|---|
| `POST /api/proxy/test` | 代理**通不通**（`app/connectivity.py`，一次朴素 HTTPS 探针，返回状态码/耗时/字节数/原始异常） |
| `POST /api/cookies/verify` | 这份 cookies **到底有没有用**（`app/cookie_health.py`：格式 → 域名 → 鉴权项 → 过期，可选联网读 `LOGGED_IN`） |
| `POST /api/diagnostics/runtime` | 装完 Node/Deno 后**不重启应用**重新自检 |

`proxy.py`（解析）与 `connectivity.py`（验证）刻意分成两个模块：解析是纯函数、可离线单测；
验证必须联网、结果必须是原始证据。同理 `browser_cookies.py`（导入）与 `cookie_health.py`（体检）分开。

失败一律带 `next_steps`。「强制直连失败」的文案会明确写「可能预期」——**只给失败不给下一步的提示等于没做**。

### f. 界面按「结论 → 动作 → 知识」重排

- **先给结论**：`当前生效：X（来源：Y）`、`JS 运行时：可用/不可用`、`日志文件：…`；
- **再给动作**：「检测代理」/「先试输入框里的地址」/「重新自检」/「校验 cookies」；
- **最后给知识**：折叠块里的常见代理端口表、cookies 三种获取方式（含「不要做什么」）、
  「校验结果怎么看」。

> 注（2026-10-02）：本节的「折叠块」后来被界面重构替换 —— 四段说明不再平铺，而是按所属功能区
> 就近挂成浮层（获取方式 / 结论解读 / 常用端口 / 排查顺序），默认收起、悬停或点击展开、
> 桌面与窄屏同款内容。知识内容本身未变，见 [用户手册：辅助说明的查看方式](../../docs/user-manual.md#辅助说明的查看方式)。

其中「先试输入框里的地址」在按钮上拦掉 `mousedown` 的默认行为，避免这次点击让输入框失焦 ——
输入框是 `onBlur` 自动保存的，不拦的话「先试」就变成了「先把错的端口保存下来再试」。

## 效果

### A/B：同一台机器、同一份 cookies、同一个视频

| | JS 运行时 | 结果 | 耗时 |
|---|---|---|---|
| 注入 `NODE_OPTIONS=--require="<空补丁>"` | 不可用 | `DownloadError: ERROR: [youtube] aqz-KE-bpKQ: The page needs to be reloaded.` | 6.44 s |
| 调用 `sanitize_environment()` 后 | 可用 | 成功，`formats=37` | 6.84 s |

`js_runtime_error` 里拿到的是原始报错，不再是一句天书：

```
node C:\...\node.EXE：以 yt-dlp 相同的权限模型启动失败（returncode=1）：node:internal/modules/cjs/loader:1386 / throw err;
```

### 代理验收矩阵（真实网络）

| 用例 | 解析来源 | 结果 | 耗时 |
|---|---|---|---|
| A 自动 | `system` `http://127.0.0.1:7890` | HTTP 200，792 字节 | 0.30 s |
| B 强制直连 | `direct` | `URLError: timed out` —— **按预期失败** | 24.05 s |
| C 显式填地址 | `setting` `http://127.0.0.1:7890` | HTTP 200，792 字节 | 0.17 s |
| D 指向 `127.0.0.1:1` | `setting` | `[WinError 10061] 由于目标计算机积极拒绝` —— **按预期失败** | 2.07 s |

B 与 D 是**故意保留的失败用例**：它们失败才反证 A/C 的成功确实来自代理配置，而不是碰巧走通了直连。

### cookies 体检

`data/cookies.txt` 44 条；`youtube.com` 域 23 条；鉴权项 8 个全齐（`SID`/`HSID`/`SSID`/`APISID`/`SAPISID`/`LOGIN_INFO`/`__Secure-1PSID`/`__Secure-3PSID`）；
带 cookies → 页面里 `"LOGGED_IN":true`；不带 cookies → `"LOGGED_IN":false`。

### 日志（修复前 vs 修复后）

修复前：终端里什么都看不到（INFO 被丢弃）。修复后 `data/logs/app.log` 里启动就有三行快照：

```
2026-10-01 12:00:58 INFO  app.ytdlp_service | proxy resolved: source=system proxy=http://127.0.0.1:7890 system=http://127.0.0.1:7890 environment=<none>
2026-10-01 12:00:58 INFO  app.ytdlp_service | js runtime ready: name=node path=... version=v22.22.2
2026-10-01 12:00:58 INFO  app.ytdlp_service | dependencies ready: ffmpeg=True po_token_provider=True chromium=<none> aria2c=<none>
```

失败时是「结构化事实 + 可执行诊断」两行（见上文 c 段）。

## 验证

| 测试 | 覆盖 |
|---|---|
| `tests/test_logging_setup.py`（新，4 例） | 文件被创建、格式含时间/级别/模块、重复调用不叠加 handler、只读目录降级不抛异常 |
| `tests/test_runtime_env.py`（新，7 例） | 只摘命中强加载开关的 `NODE_OPTIONS`、其它值原样保留、返回值可用于留痕、幂等 |
| `tests/test_error_advice.py`（新，9 例） | 四类分类顺序、异常链展开不因环状引用死循环、无法归类返回 `None` |
| `tests/test_connectivity.py`（新，9 例） | 直连/走代理各自构造正确的 opener、HTTP 错误与网络异常都变成证据、失败必带 `next_steps`、`direct` 失败文案说明「可能预期」 |
| `tests/test_cookie_health.py`（新，12 例） | 格式/域名/鉴权项/过期、`LOGGED_IN` 三态、`deep=false` 不联网、结论与下一步 |
| `tests/test_ytdlp_service.py`（+4 例） | 跳过跑不起来的候选、运行时报错原样上报、探测本身不抛异常、诊断暴露失败原因 |
| `tests/test_api.py`（+8 例） | 三个新接口：保存值/临时覆盖/返回体、cookies 缺失/上传后离线/deep 联网、诊断暴露日志与运行时刷新计数 |
| `frontend/src/App.test.tsx`（+9 例） | 检测失败+下一步、试地址不保存、预设表、JS 运行时不可用+重新自检、日志路径、校验成功、google-only 解释、离线体检、三种获取方式 |
| `scripts/acceptance_network.py`（新） | 真实网络的四段验收：环境快照 / 代理矩阵 / cookies 体检 / 端到端 + 环境净化 A/B |

回归：后端 **202 → 255 passed**，前端 **54 → 63 passed**，`tsc --noEmit` 无错误，
`python scripts\docs.py check` 通过。真实网络验收 `tmp_acceptance/network-acceptance.json` 中 `passed: true`。

## 风险与回滚

- **应用会在自己的进程里修改环境变量**（`os.environ.pop("NODE_OPTIONS")`）。作用域仅当前进程，
  不写系统环境、不改注册表，且**必定留痕**（日志 warning + `sanitized_environment`）。
  回滚：`git revert <sha>`。
- **新增了一个持久化面**：`data/logs/app.log`（2 MiB × 3）。`data/` 与 `*.log` 都在 `.gitignore` 内，不会入库；
  写入前统一过 `sanitize_log_message`。
- `/api/proxy/test` 会按请求体里的地址发一次请求。本工具本来就允许配置代理，且只绑定 `127.0.0.1`、
  无认证；仍按「新增网络访问面」登记进 [安全审计报告](../docs/safety-review.md) 第 18 节待复核。
- `/api/diagnostics` 新增 `log_file`（本地绝对路径）与 `sanitized_environment`（被摘变量的原值，可能含本地路径）。
  都是排障必需，且不返回文件内容；同样登记在第 18 节。
- 回滚单点：三个新接口都是只读的，删掉它们不影响下载链路；`logging_setup` 与 `runtime_env`
  可以分别独立回滚，但**不建议**回滚 `runtime_env` —— 那等于把 n challenge 重新交给宿主环境去破坏。
  测试与嵌入式调用可用 `create_app(sanitize_environment_variables=False)` 跳过净化（没有对应的环境变量开关，
  这是有意的：不想让用户随手把这条保护关掉）。

## 关联

- **是 [003](003-js-runtime-invisible-outside-path.md) 的续集**：003 解决了「PATH 里没有 node 就看不到」，
  本条解决「看得到 node、但 node 跑不起来，而报错指不到 JS」。两者合起来才让 JS 运行时真正可观测。
- **与 [001](001-bot-check-does-not-escalate-profile.md) / [002](002-po-token-provider-has-no-browser-path.md) / [006](006-proxy-is-not-configurable.md) 同属一条失败链**：
  006 让请求出得了网，001/002 让它具备过校验的条件，本条让 003 之后的 JS 求解真的能跑起来。
  链路末端「PO token 真能铸出来 + 视频真能下下来」仍需在干净代理环境下单独验收（本轮只验到元数据提取）。
- 复用了 006 建立的「来源可观测」思路：那里让**代理来源**可观测，这里让**运行环境与失败原因**可观测。

## 未覆盖 / 如实说明

- **只验收了 `extract_metadata`，没有做整段视频下载。** 端到端那一步验到「解析成功、格式列表非空」为止；
  「媒体流下得下来 + 合得起来」仍属未验收项（与 001~003 的遗留项相同）。
- **环境净化只覆盖 `NODE_OPTIONS` 一个变量。** 其它同样能打坏子进程的宿主注入（例如某些 `PYTHONPATH`/`PYTHONSTARTUP`
  组合、或 node 版本管理器注入的 `NODE_PATH`）没有处理 —— 目前只处理了**实测造成故障**的那一个，
  不凭想象扩大范围。
- **JS 运行时探测会起子进程**，因此有 20 秒超时与记忆化；若候选运行时是一个「启动后挂住」的程序，
  自检会等满超时而不是立刻返回。
- **`--experimental-permission` 的探针依赖 Node 支持该开关**。老版本 Node 会走「普通 `-e`」的退路，
  此时探针不再等价于 yt-dlp 的启动方式 —— 也就是说「探针通过」不能 100% 保证 yt-dlp 能跑起来。
- **cookies 体检的 `LOGGED_IN` 判定依赖 YouTube 页面里的 `"LOGGED_IN":true/false` 字面量**。
  YouTube 改版后这个字符串可能消失，那时结论会变成「未确认」而不是误报 —— 会退化，但不会说谎。
- **代理探测的探针地址是固定的 `https://www.youtube.com/robots.txt`**，不接受请求体指定 URL（避免把它变成通用 SSRF 原语）；
  如果 robots.txt 本身在某些网络下被拦，探测会失败而下载可能仍然可用。
- **UI 引导是按本机实测的失败形态写的**（Clash/v2rayN/SS 的常见端口）。非 Windows 平台的系统代理行为、
  以及 PAC 自动配置脚本仍然不支持（与 006 的遗留一致）。
