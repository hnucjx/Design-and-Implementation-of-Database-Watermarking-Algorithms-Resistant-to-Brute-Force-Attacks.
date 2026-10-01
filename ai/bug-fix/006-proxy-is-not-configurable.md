# 006 应用没有代理配置项

## 问题

**浏览器能打开 YouTube，应用却连不上**；界面上没有任何地方能设置代理，也没有任何地方能看出
「当前到底走没走代理、走的哪个」。想改变行为只能去改宿主进程的环境变量 —— 而用户通常
不知道有这回事。

上一轮的性能排查里，这个现象被记成了「直连超时」。**那个结论是错的**（见「原因」第 1 条），
而之所以会错，正是因为当时没有任何手段把「应用实际用了哪个代理」打印出来。

## 原因

`ydl_opts` **从不包含 `proxy`**（`ytdlp_service.py` 的 `build_download_options()` 与
`extract_metadata()`），于是代理完全由 yt-dlp 自己解析：

```python
# yt_dlp/YoutubeDL.py:4206-4220
opts_proxy = self.params.get('proxy')
if opts_proxy is not None:
    ...
else:
    proxies = urllib.request.getproxies()      # ← 走的就是这里
```

而 `getproxies` 被 yt-dlp 换成了一个自定义实现：

```python
# yt_dlp/compat/urllib/request.py:33-34
def getproxies():
    return getproxies_environment() or getproxies_registry_patched()   # 环境变量「优先」
```

这条链对本地桌面应用有两个不成立的行为，都在本机实测过。

**1) 环境变量静默顶掉 Windows 系统代理。** 本机系统代理是 `127.0.0.1:7890`（浏览器因此能上网），
但进程环境里有宿主注入的 `HTTPS_PROXY=http://127.0.0.1:54109`（本机不存在的代理）：

```
yt-dlp 会选的 Proxy map（空选项）: {'https': 'http://127.0.0.1:54109', 'http': 'http://127.0.0.1:54109'}
youtube.com 走 54109              : URLError: <urlopen error Tunnel connection failed: 502 Bad Gateway>
```

去掉这两个变量后，同一台机器、同一个 CPython 调用 `getproxies()` 得到的是注册表里的
`{'http': 'http://127.0.0.1:7890', 'https': ..., 'ftp': ...}`。也就是说用户**真正生效的系统代理
被一个他看不见的变量顶掉了**，应用里既看不到也改不了。

**2) 只设 `NO_PROXY` 时会退化成直连。** `getproxies_environment()` 把 `no_proxy` 也收进结果
（键名 `no`），返回 `{'no': '...'}` —— **非空**，于是 `or` 后面的注册表分支根本不执行：

```
只设 NO_PROXY 时 env 解析: {'https': ..., 'http': ..., 'no': '127.0.0.1,localhost'}
只设 NO_PROXY 时 yt-dlp 用: 同上（注册表被跳过）
```

## 修复方案

新增 `backend/app/proxy.py`，把代理**显式解析**成一个确定结果，优先级：

```
显式设置  >  Windows 系统代理（WinINet 注册表 ProxyEnable/ProxyServer）  >  环境变量
```

系统代理排在环境变量之前是刻意的：桌面应用应当和浏览器一致，而环境变量极易被宿主
shell / IDE 无意注入。

- `YtDlpService` 新增 `proxy` 参数与 `proxy_resolution()`，并在**元数据提取**与**下载**
  两条路径上写入 `ydl_opts['proxy']`（下载的 `prepare_download` 复用前者）。
- 三种取值语义：
  - 留空 / `auto` → 自动（上表的优先级）；
  - `direct` / `none` / `off` / `no` / `-` → 强制直连（写 yt-dlp 的 `--proxy ""`）；
  - 其余按 URL，缺 scheme 自动补 `http://`（注册表里存的就是 `127.0.0.1:7890` 这种写法）。
  - **空值不算直连**：表单清空、`YTDL_PROXY=` 都不该意外绕过系统代理。
- 设置项贯通：`YTDL_PROXY` 环境变量 / `PUT /api/settings` 的 `proxy` / 持久化到 `Setting` 表 /
  前端「设置」面板新增输入框 + 一行 `当前生效：X（来源：Y）`。
- 诊断新增 `proxy` / `proxy_source` / `proxy_writes_ydl_option` / `system_proxy` /
  `environment_proxy`，其中后两个专门回答「为什么是它」；`user:pass@` 里的密码统一脱敏。
- 服务构造时打一行 `proxy resolved: source=... proxy=...` 日志。

**为什么不总是显式写进 `ydl_opts`：** yt-dlp 的 `proxy` 参数是**单个 URL，不带绕过列表**——
`utils/networking.select_proxy()` 只在 `proxies` 里存在 `no` 键时才做绕过判断，而
`{'all': url}` 没有这个键。所以当生效来源是**环境变量**时我们**不写**，交给 yt-dlp 自己解析，
以保留 `NO_PROXY` 语义；只有「系统代理 / 显式设置 / 强制直连」才覆盖它。

**顺带被覆盖到的地方**（都读过源码确认，写进 `ydl_opts` 就是它们的唯一入口）：
- aria2c：`yt_dlp/downloader/external.py:324` `cmd += self._option('--all-proxy', 'proxy')`；
- PO token provider：`getpot_wpc.py:144-153` 把 `request.request_proxy` 转成 `--proxy-server=`，
  而该值来自 `_video.py:2859` 清洗后的 `select_proxy(...)`（`__noproxy__` 会被清洗成 `None`，
  所以「强制直连」不会把 `__noproxy__` 当代理塞给浏览器）。

## 效果

同一进程、同一份坏环境变量（`HTTPS_PROXY=http://127.0.0.1:54109` 存在），只比较选项来源：

| | yt-dlp 的 Proxy map | 访问 youtube.com |
|---|---|---|
| 修复前（空选项） | `{'https': 'http://127.0.0.1:54109', 'http': ...}` | `URLError: Tunnel connection failed: 502 Bad Gateway` |
| 修复后（应用生成的选项） | `{'all': 'http://127.0.0.1:7890'}` | **HTTP 200** |

应用侧解析结果（同一进程）：

```
ProxyResolution(url='http://127.0.0.1:7890', source='system',
                system_proxy='http://127.0.0.1:7890', environment_proxy='http://127.0.0.1:54109')
写进 ydl_opts: {'proxy': 'http://127.0.0.1:7890'}
```

强制直连 / 显式指定也都按预期生效（`url=''`、`source='direct'`；`source='setting'`）。

探针脚本：`tmp_acceptance/verify_proxy.py`（`tmp_acceptance/` 已在 `.gitignore` 内）。

## 验证

| 测试 | 覆盖 |
|---|---|
| `tests/test_proxy.py`（29 例） | 直连哨兵、空值=自动、显式设置补 scheme、系统代理优先于环境变量、环境变量来源不写 ydl_opts、来源可观测、`NO_PROXY` 检测、URL 归一化、注册表多协议/纯 socks/垃圾值、密码脱敏 |
| `tests/test_ytdlp_service.py`（+5 例） | 系统代理真的进 `ydl_opts`、无代理时不写该键、`direct` 写空串、**元数据提取**也带代理、诊断暴露来源且脱敏 |
| `tests/test_api.py`（+2 例） | `PUT /api/settings` 保存/清空代理并持久化；不带 `proxy` 的部分更新不会抹掉已配置的值 |
| `frontend/src/App.test.tsx`（+2 例） | 输入框自动保存、清空发送 `null`（而不是 `direct`）；「当前生效」提示文案 |

回归：后端 **165 → 202 passed**，前端 **52 → 54 passed**，`tsc --noEmit` 无错误。
（`FakeYtDlpService` 也随之补上了 `proxy_resolution()`，否则所有 API 测试都会因为
设置响应要读代理而报错。）

## 风险与回滚

- **唯一的行为变化**：`YTDL_PROXY` 未设置、且用户从未在界面上动过代理时，
  「系统代理与环境变量同时存在」的场景现在选**系统代理**（以前选环境变量）。
  这一点必须知情：如果你依赖某个环境变量代理，请显式填进设置或 `YTDL_PROXY`。
- 没有任何破坏性操作：不写注册表、不启动进程、不改环境。
- 回滚：`git revert <sha>`；运行时也可以立刻改回来 —— 设置界面填 `direct` 或填回原来的代理 URL。
- 显式代理**不带绕过列表**（yt-dlp 的 `proxy` 参数本身不支持）。在本机测量过：
  `getproxies_registry()` 返回的字典**没有 `no` 键**（CPython 3.14.6），所以走系统代理
  这条路本来就不认 `ProxyOverride`，显式写它没有额外损失。

## 未覆盖 / 如实说明

- **PAC / 自动配置脚本不支持**。只读 `ProxyEnable` + `ProxyServer`；本机 `AutoConfigURL`
  不存在，且 yt-dlp 本身也不支持 PAC。系统代理走 PAC 的用户仍需显式填代理。
- **aria2c 在「来源=环境变量」时拿不到代理。** `external.py` 只认 `params['proxy']`，而这种情况
  我们有意不写。更准确地说：修复前应用从不写 `params['proxy']`，aria2c **任何**情况下都没用过
  代理；本次在「系统代理 / 显式设置 / 强制直连」三种来源下它开始能拿到代理，只有「来源=环境变量」
  这一种仍然拿不到。aria2c 默认关闭。
- **数据库里的设置会盖过 `YTDL_PROXY`**：一旦在界面上保存过（哪怕是清空），`Setting` 表里的值
  就优先于环境变量 —— 与本项目其它设置项的既有行为一致，但「用界面清空 → 想退回环境变量」
  这条路径会失效（此时会变成「自动」，而自动仍会用到环境变量，因此实际影响很小）。
- **端到端仍未在「必须走代理」的干净环境里验收通过**：本次证明的是「代理被正确选中且真的
  通了 HTTP 200」，不包含「PO token 铸出来 + 视频下下来」（同 001~003 的遗留项）。
- 未验证非 Windows 平台的系统代理行为：`read_system_proxy()` 在非 Windows 上直接返回 `None`
  （回落到环境变量，与修复前一致），未在 macOS/Linux 上实测。

## 关联

- 直接回答 `README.md` 「已知但未处理」里的第 1 条，也是 001~003 那条失败链的**前置条件**：
  那三个修复让「过 bot 校验 / 铸 PO token / 解 nsig」具备条件，而这个修复让请求**能到得了
  YouTube**。链路任何一环断掉，下载都不可用。
- 与 005 同源：都是「本地桌面应用在 Windows 上的隐式环境假设」。005 处理的是浏览器 profile
  的加密/加锁假设，本条处理的是代理来源的假设。
