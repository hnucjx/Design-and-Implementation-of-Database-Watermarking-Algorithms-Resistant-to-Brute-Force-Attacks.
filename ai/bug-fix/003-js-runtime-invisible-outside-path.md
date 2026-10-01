# 003 · JS 运行时只查 PATH，服务进程里必然探测不到 Node

- 状态：已修复
- 提交：`fix(ytdlp): detect Node/Deno outside PATH for JS challenges`
- 影响面：所有需要解 nsig 的 YouTube 提取（现代 YouTube 几乎全部）
- 严重度：高

## 问题

yt-dlp 的 verbose 日志里长期出现：

```
[youtube] [jsc] JS Challenge Providers: bun (unavailable), deno (unavailable), node (unavailable), quickjs (unavailable)
```

四个 JS 运行时全部 unavailable，意味着 yt-dlp **没有任何办法解 YouTube 的 nsig
（JS challenge）**。这会让提取在参数/签名环节就失败或被降级，后面所有 anti403
与 PO token 的努力都失去意义。

本机显然装了 Node（`C:\Program Files\nodejs\node.exe`，v24.18.0）。

## 原因

`YtDlpService._detect_js_runtime()` 只查了 PATH：

```python
deno_path = shutil.which("deno")
if deno_path: ...
node_path = shutil.which("node")
if node_path: ...
```

`shutil.which()` 读的是**当前进程**的 `PATH`。后端服务通常由别的 shell / IDE /
启动脚本拉起，其 `PATH` 里没有 `C:\Program Files\nodejs`（Windows 的 Node 安装器
只改用户级 PATH，且已运行的进程不会继承新的 PATH），于是探测返回 `None`，
`_javascript_runtime_options()` 返回 `{}`，yt-dlp 拿不到 `js_runtimes`
→ provider 全部 unavailable。

判定依据（不是猜测）：把 PATH 剥成只剩 `C:\Windows\System32;C:\Windows` 后
`shutil.which("node")` 返回 `None`——这正是服务进程的处境。

## 修复方案

1. 抽出通用的 `_detect_executable(names, install_subpaths, fallback_paths)`，
   探测顺序为 **PATH → Windows 安装目录（`%ProgramFiles(x86)%` / `%ProgramFiles%` /
   `%LOCALAPPDATA%` / `%LOCALAPPDATA%\Programs`）→ 固定路径（`/usr/local/bin`、
   `/opt/homebrew/bin`、`~/.deno/bin` 等）**，只返回**确实存在**的路径。
   `detect_chromium_executable()`（修复 002 引入）一并改用该助手，消除重复逻辑。
2. 新增 `detect_node_executable()` / `detect_deno_executable()`
   （Node 认 `node`、`nodejs` 两个名字，兼容 `nodejs.exe`）。
3. `_detect_js_runtime()` 改为按候选依次尝试：**显式配置 → Deno → Node**，
   并用新的 `_js_runtime_from_executable()` 统一解析运行时名与版本；
   Node 版本低于 20（或根本跑不起来）时视为没有该运行时，**继续尝试下一个候选**
   而不是直接放弃。
4. 新增配置 `YTDL_JS_RUNTIME_PATH`（`AppSettings.js_runtime_path`），
   显式指定时优先级最高，并在 `create_app()` 里透传给 `YtDlpService`。
5. 诊断接口新增 `js_runtime_path` 字段，可直接看到探测结果。

## 效果

PATH 被剥成 `C:\Windows\System32;C:\Windows` 后（模拟服务进程环境）：

```
shutil.which("node")        = None        <- 旧代码到此为止
detect_node_executable()    = C:\Program Files\nodejs\node.exe
_detect_js_runtime()        = ('node', 'C:\\Program Files\\nodejs\\node.exe', 'v24.18.0')
js_runtimes opt             = {'js_runtimes': {'node': {'path': 'C:\\Program Files\\nodejs\\node.exe'}}}
```

修复前该场景下 `js_runtimes` 为 `{}`。相应地，用应用自己的选项构造函数实测时
verbose 输出从 `node (unavailable)` 变成：

```
[youtube] [jsc] JS Challenge Providers: bun (unavailable), deno (unavailable), node, quickjs (unavailable)
```

## 验证

`backend/tests/test_ytdlp_service.py` 新增 4 例：

| 用例 | 断言 |
|---|---|
| `test_detect_node_executable_falls_back_to_install_dir` | PATH 为空时从 `%ProgramFiles%\nodejs` 找到 |
| `test_detect_js_runtime_prefers_explicit_path` | `YTDL_JS_RUNTIME_PATH` 优先级最高 |
| `test_detect_js_runtime_falls_back_to_install_dir_when_path_is_empty` | 端到端：PATH 空 → 仍产出 `("node", <安装目录>, "v22.22.2")` |
| `test_detect_js_runtime_ignores_unsupported_node_version` | Node < 20 不被当作可用运行时 |

回归：`pytest -q` → **159 passed**（本修复前 155）。

## 风险与回滚

- 风险：极低。只是多了几条「去哪找可执行文件」的候选路径；找不到时行为与修复前完全一致。
  唯一需要注意的是：一旦探测到 Node，yt-dlp 会真的调用它跑一段 JS 来解 nsig，略有耗时。
- 回滚：`git revert <sha>`。

## 关联

- `001` / `002` 修的是「走到 anti403 profile 并在那里拿到 PO token」，
  本修复解决的是它们的前置条件（JS challenge）。三者是同一串失败链上的不同环节。
- 顺带发现但**未在本轮处理**：后端自身没有任何代理配置项，`ydl_opts` 里不含 `proxy`，
  完全依赖宿主进程的 `HTTP_PROXY` / `HTTPS_PROXY`。而 Windows 的系统代理是 WinINet 设置，
  yt-dlp / curl **读不到**。若用户的出口必须走代理，这条需要单独一轮处理。
