# 005 · Edge 的 CDP 回退既必然失败、又会破坏真实 cookie 库

- 状态：已修复（该路径已停用）
- 提交：`fix(cookies): stop pointing CDP at the live Edge profile`
- 影响面：UI 上的「从浏览器导入 cookies」→ Edge 分支
- 严重度：高（**有数据破坏性**；且产品里这个功能从来没成功过）

## 问题

产品里「从浏览器导入 cookies」对 Edge 的实际效果是：

1. yt-dlp 原生读取失败（`Failed to decrypt with DPAPI`）；
2. 落入 CDP 回退，用 `--user-data-dir=<真实 Edge 目录>` + `--headless=new` 启动 Edge；
3. 失败，或**更糟**——把用户的真实 cookie 库搞坏。

## 原因

`backend/app/browser_cookies.py` 的 `_extract_edge_cookies_via_cdp()` 有三处硬伤，全部实测：

| 硬伤 | 实测证据 |
|---|---|
| 在**默认** user-data-dir 上开 CDP | Chromium 直接拒绝：`DevTools remote debugging requires a non-default data directory` |
| 用 junction 绕过该限制 | 端点通了，但**真实 cookie 库被清空（53 → 0）**，靠快照才恢复 |
| 即便跑通也用错模式 | `--headless=new` 下等 35s 也拿不到 `.youtube.com` 域的鉴权 cookie；同样的代码换有头窗口 21s 就拿到 40 条 |

也就是说这条回退路径**没有任何一种情形能成功**，却带来了真实的破坏面。
它之所以存在，是当时假设「CDP 能读到运行中浏览器的 cookie」——该假设对 v20
app-bound 加密不成立：密钥绑定 Edge 二进制本身，**只有运行中的 Edge 进程能解开**，
而挂 CDP 恰恰不是「让 Edge 自己解密」，是去读它的磁盘库。

## 修复方案

不再尝试触碰用户的真实配置，改为给出**可执行**的下一步：

1. 新增 `BrowserCookieImportError.edge_app_bound()`，把三条依据写进消息，
   并明确告诉用户两条可行路径：
   - 运行仓库里的 `scripts/export_cookies_via_cdp.py`（独立 profile，首次登录一次）；
   - 或用浏览器扩展（如 Get cookies.txt LOCALLY）导出到 `data/cookies.txt`。
2. `_extract_edge_cookies_via_cdp()` 改为直接抛该错误，**不再启动任何进程**。
   保留方法名，继续作为可注入接缝（`extract_edge_cookies_via_cdp`），
   将来若接入基于**独立 profile** 的导出流程，只替换这一处。
3. 删除随之变成死代码的整套 CDP 机制（`_edge_executable`、`_edge_user_data_dir`、
   `_free_tcp_port`、`_wait_for_cdp_websocket_url`、`_read_cdp_cookies`、`_cdp_cookie`、
   `_terminate_edge_process` 及 `YtDlpService` 上的包装），并清理不再使用的 import。
   文件从 323 行降到 204 行——**一个指向真实 profile 的启动器留在代码里就是地雷**。
4. `import_browser_cookies()` 在 `auto` 模式下会记住该错误，并在没有浏览器成功时
   **优先抛出**（`browser_locked` 优先于它，因为「关掉浏览器重试」真的可能成功）。

## 效果

- 不再有任何代码路径会用 CDP 挂载真实 Edge 配置 → 结构上不可能重演清库。
- 用户拿到的是可执行指引，而不是「再试一次」。
- 该错误码不在前端的 `browser_locked` 分支里，会作为普通错误消息展示，
  而 `ApiError` 会自动取 `detail.message` —— 也就是上面那段指引，无需改前端。

## 验证

`backend/tests/test_ytdlp_service.py`：

| 用例 | 断言 |
|---|---|
| `test_edge_cdp_fallback_never_launches_a_browser` | 把 `subprocess.Popen` 换成「一调用就断言失败」的桩 → 确认**没有任何进程被启动**，且错误码为 `edge_app_bound`、消息含导出脚本路径 |
| `test_import_browser_cookies_reports_edge_app_bound_after_dpapi_failure` | 端到端：DPAPI 失败后抛 `edge_app_bound`，且**不写出**坏 cookies 文件 |
| `test_auto_browser_cookie_import_prioritizes_edge_app_bound` | `auto` 模式下其余浏览器只有泛化失败时，优先抛这条可执行的错误 |

删除 `test_edge_cdp_fallback_terminates_process_tree`（被测方法已不存在）。

回归：`pytest -q` → **165 passed**（本修复前 163：净删 1 例、新增 3 例）。

## 遗留风险（未在本轮处理，建议单列一轮）

`_close_browser_for_cookie_import()` 用的是 `taskkill /IM msedge.exe /F /T`，
会**杀掉用户全部的 Edge 进程**（连同未保存的标签页）。它只在用户显式勾选
「由应用关闭 Edge 并重新导入」时触发，属于已获同意，但破坏性远大于必要：
至少应改为先尝试只关必要的进程 / 先提示保存，或改为「检测到锁定就提示用户自己关」。

## 回滚

`git revert <sha>`（会一并恢复那套 CDP 机制，不建议）。
