# 002 · PO token provider 拿不到浏览器路径，永远 unavailable

- 状态：已修复
- 提交：`fix(ytdlp): auto-detect a Chromium path for the PO token provider`
- 影响面：所有依赖 PO token 的 YouTube 提取（`mweb_pot_chrome` profile）
- 严重度：高（`pyproject.toml` 声明的依赖事实上从未生效）

## 问题

`yt-dlp-getpot-wpc`（wpc）明明装好了，yt-dlp 却始终把它列为**不可用**：

```
[youtube] [pot] PO Token Providers: wpc-1.1.2 (external, unavailable)
```

于是 anti403 profile 里唯一能铸 PO token 的那一档等于空转。

## 原因

两层叠加：

1. **插件侧**：`yt_dlp_plugins/extractor/getpot_wpc.py` 的 `WPCPTP.is_available()` 要求
   `browser_path` **指向一个存在的文件**，否则直接 `return False`：

   ```python
   if (missing_browser or not nodriver_config.browser_executable_path
           or not pathlib.Path(nodriver_config.browser_executable_path).exists()):
       ...  # return False -> provider unavailable
   ```

   （它对 Chrome 的默认探测并不可靠，所以作者自己也在文档里让人用
   `--extractor-args "youtubepot-wpc:browser_path=XYZ"` 显式指定。）

2. **本项目侧**：`_po_token_provider_args()` 只在 `self.youtube_po_browser_path` 非空时才
   把路径传给插件：

   ```python
   if youtube_profile != "mweb_pot_chrome" or not self.youtube_po_browser_path:
       return {}
   ```

   而 `AppSettings.youtube_po_browser_path` 默认是 `None`。**两条一叠：默认配置下
   provider 必然 unavailable**，`pyproject.toml` 里的 `yt-dlp-getpot-wpc>=1.0.0`
   等于白装。

## 修复方案

新增 `detect_chromium_executable()`（模块级函数，便于复用与测试）：

1. 先按名字在 `PATH` 上找：`msedge` → `chrome` → `chromium` → `brave` → `vivaldi`；
2. 再按 Windows 常见安装目录找：
   `%ProgramFiles(x86)%` / `%ProgramFiles%` / `%LOCALAPPDATA%` 下的
   `Microsoft\Edge\Application\msedge.exe`、`Google\Chrome\Application\chrome.exe`。

服务侧新增 `_po_token_browser_path()`：**显式配置优先，否则用探测结果**；
`_po_token_provider_args()` 改为使用它。诊断接口新增 `po_token_browser_path`
字段，让 UI 能直接看到 provider 实际拿到的路径（原来是 `None` 也看不出来）。

探测只返回**确实存在**的路径，因此不会把本来就不可用的 provider 伪装成可用。

## 效果

同一台机器、同一条命令，只差 `browser_path`：

| 场景 | verbose 输出 |
|---|---|
| 修复前（未配 `youtube_po_browser_path`） | `PO Token Providers: wpc-1.1.2 (external, unavailable)` |
| 手工传 `browser_path` | `PO Token Providers: wpc-1.1.2 (external)` |
| **修复后（自动探测）** | `PO Token Providers: wpc-1.1.2 (external)` ✅ |

用应用自己的选项构造函数实测（`mweb_pot_chrome` profile）：

```
== 依赖状态 ==
  po_token_provider_available = True
  po_token_provider_version   = 1.1.2
  youtube_po_browser_path_configured = False        <- 用户没配任何东西
  po_token_browser_path = C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe
== 关键 yt-dlp 选项 ==
  extractor_args = {'youtube': {'player_client': ['mweb', 'default']},
                    'youtubepot-wpc': {'browser_path': ['...\\msedge.exe']}}
```

## 未覆盖 / 如实说明

- 本轮**没有**证明「PO token 能铸出来」或「提取成功」。该次探针最后失败在
  `curl: (56) CONNECT tunnel failed, response 502`，根因是**探针进程继承了沙箱注入的
  `HTTPS_PROXY=http://127.0.0.1:54109`**（一个只在本执行环境里存在的内部代理），
  与本次改动无关。也就是说本次修复把 provider 从「不可用」推进到「可用」，
  但端到端是否放行仍需在干净的代理环境下单独验收。
- 探测顺序里 Edge 优先于 Chrome，仅因为本机只装了 Edge；若两者都在，
  可用 `YTDL_YOUTUBE_PO_BROWSER_PATH` 覆盖（该配置项早已存在）。

## 验证

`backend/tests/test_ytdlp_service.py` 新增 4 例：

| 用例 | 断言 |
|---|---|
| `test_po_token_browser_path_prefers_configured_over_detected` | 显式配置压过探测结果 |
| `test_po_token_provider_args_fall_back_to_detected_chromium` | 未配置时也把探测到的路径传给 wpc |
| `test_po_token_provider_args_absent_when_no_browser_is_found` | 探测不到时**不生成** `youtubepot-wpc` 段（不伪造可用性） |
| `test_detect_chromium_executable_prefers_path_then_install_dirs` | PATH 优先，其次安装目录 |

并扩展现有 `test_dependency_status_reports_po_token_provider_without_secret_values`，
断言 `po_token_browser_path` 等于配置值。

回归：`pytest -q` → **155 passed**（本修复前 151）。

## 风险与回滚

- 风险：低。探测不到时行为与修复前一致（不传 `browser_path`）。
  唯一副作用是：一旦探测到浏览器，**提取时 wpc 会真的拉起一个（最小化的）浏览器窗口**
  来铸 token——这是该 provider 的设计行为，不是本修复引入的，但此前从未被触发过，
  用户会第一次看到浏览器一闪而过。
- 回滚：`git revert <sha>`。

## 关联

- 前一个缺陷见 `001`（bot 校验错误够不到这条路径，所以 002 即使修好也未必被走到，
  两个修复需同时生效）；两者的共同前置条件见 `003`（JS 运行时）。
