# 009 · 8000 端口被 IncrediBuild 长期占着，应用起不来，而报错指着「权限」

- 状态：已修复
- 提交：`fix(dev): 端口成为一等配置项，被占时直接告诉你谁占的`
- 影响面：本地启动（`python -m app`）；前端 Vite 开发模式的 `/api` 代理；`AppSettings` 读取 `.env` 的位置
- 严重度：高（应用**完全**无法启动，且报错把方向引向「权限 / 防火墙 / 杀毒软件」——用户会去查错的地方）

## 问题

按 README 的命令启动后端，服务在绑定端口这一步就死掉：

```text
2026-10-01 14:35:09 ERROR uvicorn.error | [Errno 13] error while attempting to bind on address ('127.0.0.1', 8000):
[winerror 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。
2026-10-01 14:35:09 INFO  uvicorn.error | Waiting for application shutdown.
```

注意顺序：`Application startup complete.` **先**打印，然后才是绑定失败 —— 也就是应用初始化、
依赖探测、日志全都正常，只是端口拿不到。而「以一种访问权限不允许的方式」这句话与真因无关。

## 原因

### 1) 8000 确实被别的进程占着，而且是长期占用

```text
$ netstat -ano | grep ":8000"
  TCP    0.0.0.0:8000           0.0.0.0:0              LISTENING       6948
  TCP    [::]:8000              [::]:0                 LISTENING       6948
  TCP    [fe80::2fc7:6e96:99cc:f401%2]:8000  [fe80::...]:60204  ESTABLISHED     6948

$ Get-CimInstance Win32_Process -Filter "ProcessId=6948"    # 写入 tmp_acceptance/009-port-probe.txt
ProcessId : 6948
Name      : Manager.exe
```

`Manager.exe` 是 `C:\Program Files (x86)\IncrediBuild\Manager\Manager.exe`（分布式编译工具 IncrediBuild 的
Coordinator）；同时 `endpointService.exe`（PID 6064）正连着它。它不是偶尔撞车，而是**一直是这个状态**。

> 事实与推断分开：我实测到的是「PID 6948 以 `0.0.0.0:8000` 监听」这一个事实。
> **我没有**考证 IncrediBuild 为何选 8000、以及它是否可配置到别的端口 —— 那是另一个问题（见「未覆盖」）。

### 2) 为什么是 `10013` 而不是「地址已在使用」`10048`（矩阵实验）

直觉上「端口被占」应该报 `WSAEADDRINUSE(10048)`。实测四种组合（占用方与绑定方各自是否设
`SO_EXCLUSIVEADDRUSE`），结论很干净：

```text
占用方独占=False 绑定方独占=False → 绑定成功
占用方独占=False 绑定方独占=True  → 绑定成功
占用方独占=True  绑定方独占=False → errno=13 winerror=10013
占用方独占=True  绑定方独占=True  → errno=13 winerror=10013
```

**判据是「占用方是否以 `SO_EXCLUSIVEADDRUSE` 独占通配地址」，与绑定方怎么设无关。**
两个反直觉的地方：Windows 上「通配已监听、再绑具体地址」默认是**允许**的（前两行绑定成功）；
只有占用方独占时才失败，且返回的是 `WSAEACCES(10013)`（权限）而不是 `WSAEADDRINUSE(10048)`。

本机观察到的就是第三/四行那种情形 —— 这说明占着 8000 的那个套接字是独占的。
（我测的是 socket 语义，**没有**去验证 `Manager.exe` 代码里具体怎么设的。）

### 3) 已排除的常见解释：保留端口段

`netsh interface ipv4 show excludedportrange protocol=tcp` 在本机输出为空（无任何保留段），
所以「Hyper-V / WSL 把 8000 圈走了」这个常见解释在这里不成立。

### 4) 端口值散落多处，其中 Vite 的代理是硬编码的

`8000` 出现在 README、`docs/development.md`、`docs/api.md`、`docs/openapi.yaml`、
`ai/plan.md`，以及 `frontend/vite.config.ts` 的代理目标里。于是「换个端口跑后端」会得到一个
**更糟**的状态：开发模式下 Vite 仍然把 `/api` 代理到 8000，也就是代理到 **IncrediBuild** 上 ——
页面报出来的错与真实病因毫无关系（这正是 [007](007-js-challenge-fails-only-when-cookies-are-on.md)
花一整轮去消灭的那类「不可诊断的失败」）。

`ai/plan.md:801` 其实早就写下过这条约束：

> Do not kill IncrediBuild; local uvicorn may use port 8001 if 8000 is taken.

但没有任何一行代码支持它：既没有配置项，代理也写死了 8000。

### 5) `.env` 根本不在文档说的位置（实测 A/B）

`docs/development.md` 写「会读取仓库根目录的 `.env`」，而配置里是 `env_file=".env"` ——
**相对路径按当前工作目录解析**，而所有文档命令都是 `cd backend` 之后执行的。实测
（把一份 `.env` 放进 `tmp_pytest/envprobe/`，再从该目录启动）：

```text
修复前  env_file = '.env'                    → default_resolution = probe-resolution   ← 读到了 cwd 的那个
修复后  env_file = WindowsPath('D:/code-repo/cascade/.env')
                                             → default_resolution = 1440p              ← 不再跟着 cwd 走
```

也就是说：**按文档把 `.env` 放在仓库根，从 `backend/` 启动时根本不会被读到。**
这一条必须先修 —— 否则「端口只有一个来源」就成了新的坑：同一个变量写在两个文件里，谁生效看 cwd。

## 修复方案

### a. 端口成为一等配置项（`backend/app/config.py`）

新增 `api_port`（环境变量 `YTDL_API_PORT`，默认 `8000`，范围 1..65535）。

### b. `.env` 固定指向仓库根（`backend/app/config.py`）

`env_file=REPO_ROOT / ".env"`：与文档一致，且与 cwd 无关。

### c. 启动入口 `python -m app`（新增 `backend/app/__main__.py`）

启动**前**做端口预检：拿不到就打印「谁占了 + 三条可执行的下一步」并 `exit 2`。

刻意**默认不自动换端口**：静默换端口会制造「后端在 8001、前端还代理 8000」这类查不出来的错配，
正是 007 的教训。要自动，得显式加 `--auto-port`。

### d. 端口检查做成可单测的模块（新增 `backend/app/dev_server.py`）

- `port_available()`：判据是「**真的绑一下试试**」，不是「netstat 里没看到」——
  被保留/被独占的端口在 `netstat` 里可能很干净，只有绑定时才失败。也刻意**不设**
  `SO_REUSEADDR`：在 Windows 上它会让探测在端口已被占用时也「成功」，正好把要诊断的情况判反。
- `find_available_port()`：从目标端口起往后找，`attempts` 用完就返回 `None`（不退化成扫 65535 个端口）。
- `parse_listening_pids()` / `parse_tasklist_name()`：**纯函数**，输入是命令输出文本，
  所以能离线单测；只看 `LISTENING` 行（同端口还有大量 `ESTABLISHED` 行），
  端口匹配用 `:8000` 后缀（不会误中 `:80000`）。
- `describe_occupant()`：拿不到进程名就返回 `None` —— 宁可只说「端口被占」，也不猜一个错的名字。

### e. 前端读同一个变量（`frontend/vite.config.ts`）

用 `loadEnv(mode, <仓库根>, "YTDL_")` 读 `YTDL_API_PORT`，默认仍是 8000。硬编码消失。

### 被否决的方案

| 方案 | 否决理由 |
|---|---|
| 杀掉 IncrediBuild | `ai/plan.md:801` 明确禁止；它是用户要用的编译加速工具 |
| 改 IncrediBuild 的监听端口 | 动别人的工作工具，风险与收益不成比例；也没考证它是否可配置 |
| 只改文档、让人手敲 `--port 8001` | 那就是现状（文档早就写了 8001）：开发模式的代理仍硬编码 8000，等于把「起不来」换成「起来了但前端连到别的程序上」 |
| 默认自动换端口 | 静默换端口制造「前后端端口不一致」的错配，且错误表现离病因极远 |

## 效果

### 1) 端口被占时（实测，`python -m app`）

```text
$ python -m app
端口 8000 无法绑定，占用者：Manager.exe (PID 6948)。
下一步（任选一条）：
  1) 换个端口启动：       python -m app --port 8001
  2) 写进仓库根 .env：    YTDL_API_PORT=8001   （前后端会自动一致）
  3) 看是谁占着：         netstat -ano -p tcp | findstr :8000
  也可以直接加 --auto-port，让本命令自己往后找一个可用端口。
$ echo $?
2
```

修复前同一情形只有 `[WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。`

### 2) 真的能起来（实测）

```text
$ python -m app --auto-port
端口 8000 无法绑定，占用者：Manager.exe (PID 6948)。
已自动改用 8001；前端开发模式请设 YTDL_API_PORT=8001（或写进仓库根 .env）。
...
2026-10-01 14:54:31 INFO  uvicorn.error | Uvicorn running on http://127.0.0.1:8001 (Press CTRL+C to quit)

$ curl -s -w '\nHTTP %{http_code}\n' http://127.0.0.1:8001/health
{"ok":true}
HTTP 200
```

同一次启动输出里也确认了 [008](008-return-in-finally-swallows-the-real-error.md) 的效果：
不再出现那两条 `SyntaxWarning`。

### 3) 前端代理跟随端口（实测 `resolveConfig`）

| 配置 | `/api` 代理目标 |
|---|---|
| 什么都不设 | `http://127.0.0.1:8000` |
| 环境变量 `YTDL_API_PORT=8123` | `http://127.0.0.1:8123` |
| 仓库根 `.env` 写 `YTDL_API_PORT=8321` | `http://127.0.0.1:8321` |

（验证用的临时 `.env` 已删除，工作区无残留。）

### 4) 回归

后端 **267 → 286 passed**（+19）；前端 **63 passed**（改造 `vite.config.ts` 后无影响）。

## 未覆盖 / 如实说明

- **前端只验到「解析出的代理目标地址」**，没有真的开 5173、在浏览器里走一次 `/api` 请求。
- **`--reload` 路径没有端到端跑过**（uvicorn 会另起子进程重新绑定端口，理论上没问题，但没实测）。
- **只在本机 Windows 验证。** `describe_occupant` 依赖 `netstat -ano -p tcp` 与 `tasklist` 的英文 CSV /
  英文状态列；非 Windows 平台上它只会返回 `None`（即只说「端口被占」、不说被谁占），不会报错，
  但也没有对应测试。
- **`port_available()` 存在「探测通过、随后被别人抢走」的竞态**，没有加锁也没有重试。
  预检只是把话说清楚，不构成占用保证。
- **端口预检只在 `python -m app` 这条入口生效。** 继续敲
  `python -m uvicorn app.main:app --port 8000` 仍然是原始报错 —— 文档已改推荐 `python -m app`，
  但裸 uvicorn 这条路堵不住（也不该堵）。
- **`docs/openapi.yaml` 里的 `http://127.0.0.1:8000` 仍是硬编码**（API 规格的默认示例），没跟随 `YTDL_API_PORT`。
- **没有考证 IncrediBuild 能否改到别的端口。** 如果能，那会是更彻底的方案，但本轮选择不动它 ——
  开发机上一个编译工具占着 8000 是常态，应用侧可配置才是稳的那一侧。
- **没有做「`.env` 放在 `backend/` 下」的兼容处理**：那种布局从本 commit 起不再被读取（见「风险与回滚」）。

## 验证

| 测试 | 覆盖 |
|---|---|
| `tests/test_dev_server.py`（新，19 例） | 解析：只看 LISTENING 行、同端口去重、`:8000` 不误中 `:80000`、查不到 PID 的本地化提示/非 `.exe` 名字一律返回 None；绑定：占用端口返回 False、空闲返回 True、向后跳过占用者、`attempts` 用完返回 None、空闲端口上 `describe_occupant` 返回 None；启动决策：空闲端口原样返回、被占时 `exit 2` 且提示里含 `--port N` / `YTDL_API_PORT=N` / `netstat` 命令、`--auto-port` 时换到可用端口；配置：`api_port` 默认 8000、`YTDL_API_PORT` 可覆盖、`env_file` 是仓库根绝对路径 |
| 手工验收 | `python -m app`（被占 → exit 2，指名 `Manager.exe (PID 6948)`）、`python -m app --auto-port` + `curl /health` → HTTP 200、`resolveConfig` 三种端口来源 |

回归：后端 `267 → 286 passed`；前端 `63 passed`。

## 风险与回滚

- **行为变化（可能影响老用户）**：`env_file` 从「cwd 下的 `.env`」改为「仓库根 `.env`」。
  本仓库不存在 `.env`（实测 `ls -a` 只有 `.venv`），因此无实际影响；但如果谁当初把 `.env` 放在
  `backend/` 下，从那之后它**不再被读取**。这是有意的（文档一直写的是仓库根），也是让前后端
  能读到同一个文件的前提。
- `api_port` 只影响 `python -m app` 的默认端口，不影响裸 uvicorn 的行为。
- `--auto-port` 是显式开关；默认行为是**失败退出**，不会静默换端口。
- 新增两个模块（`dev_server` / `__main__`）与一个配置字段，**未引入任何第三方依赖**
  （只用标准库 `socket` / `subprocess` / `csv` / `argparse`）。
- 回滚：`git revert <sha>`。三处（config / dev_server+`__main__` / `vite.config.ts`）可分别回滚，
  但**不建议**单独回滚 `env_file` 那一行 —— 它同时决定前后端能不能读到同一个文件。

## 关联

- 与 [008](008-return-in-finally-swallows-the-real-error.md) 是**同一份启动日志里的两个独立问题**：
  008 修的是日志开头的两条 `SyntaxWarning`，本条修的是日志结尾的绑定失败。两者无因果关系。
- 是 [007](007-js-challenge-fails-only-when-cookies-are-on.md) 思路的延续：
  006 让**代理来源**可观测，007 让**运行环境与失败原因**可观测，本条让**端口占用**可观测 ——
  三个都是「把一句查不到的报错变成一句能照着做的报错」。
- 与 `ai/plan.md:801` 的既有约束形成闭环：那里的「8000 被占可以改用 8001」现在有了配置项、
  有了一致的代理、也有了文档。
- 记录 008/009 都碰了 `backend/app/` 下的文件，但改动文件完全不重叠，可独立 revert。
