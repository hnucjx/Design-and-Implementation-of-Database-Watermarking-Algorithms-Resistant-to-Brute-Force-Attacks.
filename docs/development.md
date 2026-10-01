# 开发文档

适用读者：需要搭建本地环境、运行服务、修改文档或参与开发的维护者。普通用户只需要按 [README 快速启动](../README.md#快速启动) 使用单端口模式；本文只展开开发者需要理解的依赖、配置和热更新模式。

## 技术栈

| 层 | 技术 | 依据 |
| --- | --- | --- |
| 后端 | Python 3.12+、FastAPI、SQLModel、SQLite、yt-dlp、curl_cffi、yt-dlp-getpot-wpc、imageio-ffmpeg | [backend/pyproject.toml](../backend/pyproject.toml) |
| 前端 | React 18、TypeScript、Vite、lucide-react、原生 CSS | [frontend/package.json](../frontend/package.json) |
| 测试 | pytest、pytest-asyncio、Vitest、Testing Library、jsdom | [backend/pyproject.toml](../backend/pyproject.toml)、[frontend/package.json](../frontend/package.json) |
| 文档 | Markdown、仓库内文档工具、PlantUML、Java、Graphviz | [文档写作与生成环境](documentation-workflow.md) |

## 环境要求

- Python 3.12 或更高版本（`backend/pyproject.toml` 的 `requires-python`）。
- Node.js 20+ 推荐；yt-dlp 的 JS 运行时检测要求 Node 主版本 ≥ 20，或存在 `deno`。
- Java 和 Graphviz 用于渲染 PlantUML 图；PlantUML jar 由仓库内文档工具自动下载和校验。
- `ffmpeg` 推荐安装；如果 PATH 中没有系统 `ffmpeg`，后端会尝试使用 `imageio-ffmpeg` 后备执行文件。缺少 ffmpeg 时，需要合并音视频的清晰度会直接报错而不是静默降级。
- `aria2c` 可选，仅当同时满足 `YTDL_ARIA2C_ENABLED=true` 且能找到可执行文件时才插入 `default_aria2c` profile。它也是单视频任务唯一真实的提速手段，默认关闭。

Windows 可用：

```powershell
winget install Python.Python.3.12
winget install OpenJS.NodeJS.LTS
winget install Microsoft.OpenJDK.21
winget install Gyan.FFmpeg
winget install Graphviz.Graphviz
winget install aria2.aria2      # 可选：仅在启用 aria2c fallback 时需要
```

## 安装依赖

README 的快速启动只安装运行依赖；开发者建议安装后端 `dev` extras，以获得 pytest/httpx 等测试依赖。

后端：

```powershell
cd backend
python -m pip install -e ".[dev]"
```

这会以可编辑模式安装后端应用和开发/测试依赖，便于本地修改后立即被 `uvicorn` 和 pytest 使用。

前端：

```powershell
cd frontend
npm install
```

这会安装 React/Vite/Vitest 依赖，并为 `npm run build`、`npm run dev` 和 `npm test` 做准备。

## 本地运行

### 普通单端口模式

普通使用和手动验收优先使用 [README 快速启动](../README.md#快速启动)：先执行 `npm run build` 生成完整的 `frontend/dist/index.html` 和 `frontend/dist/assets/`，再启动后端并打开它打印的地址（默认 `http://127.0.0.1:8000`）。此时 FastAPI 同时提供页面、静态资源和 `/api` 接口；入口逻辑见 [main.py](../backend/app/main.py#L500)。

启动命令是 [`python -m app`](../backend/app/__main__.py)，它比裸 `python -m uvicorn app.main:app` 多做两件事：**启动前先检查端口**（见 [端口被占用时](#端口被占用时)），以及**把实际监听地址打印出来** —— 因为端口是可配的，不再是一个可以写死在文档里的常量。

命令行参数（全部由 [__main__.py](../backend/app/__main__.py#L64) 解析）：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--host` | `127.0.0.1` | 监听地址。不要放宽到 `0.0.0.0`，见 [安全审计报告](safety-review.md#14-网络安全)。 |
| `--port` | `YTDL_API_PORT`（默认 `8000`） | 本次启动的端口。**只影响后端**，前端代理仍读 `.env`。 |
| `--auto-port` | 关闭 | 端口被占时自动往后找一个可用端口（最多试 20 个），并打印前端该设的值。 |
| `--reload` | 关闭 | 源码变更自动重启。 |

### 前端热更新开发模式

需要修改 React UI 时，先启动后端 API：

```powershell
cd backend
python -m app --reload
```

再启动 Vite dev server：

```powershell
cd frontend
npm run dev -- --port 5173
```

开发时打开 `http://127.0.0.1:5173`。Vite 会热更新前端代码，并将 `/api` 请求代理到后端端口，代理配置见 [vite.config.ts](../frontend/vite.config.ts)。

这里的两个端口是**两件不同的事**，不要混为一谈：

- **后端端口可配**，来源是 `YTDL_API_PORT`；`--port` 可临时覆盖。
- **前端 dev server 端口不在配置里**，就是命令行 `--port 5173` 给的值。CORS 中间件只放行 `http://127.0.0.1:5173` 与 `http://localhost:5173`（见 [main.py](../backend/app/main.py#L111)）—— 但开发模式下 [api.ts](../frontend/src/api.ts) 用的是相对路径 `/api`，浏览器只与 Vite 同源通信、由 Vite 服务端转发，所以正常流程**不会触发 CORS**。把 dev server 换到别的端口后，只要仍走 Vite 代理也照样能用；CORS 白名单只在某处改成直连后端时才起作用。

### 端口被占用时

后端端口的**唯一来源**是仓库根 `.env` 里的 `YTDL_API_PORT`（默认 `8000`）。`frontend/vite.config.ts` 读的是同一个变量，所以前后端不会各说各话。

被占用时 `python -m app` 会指名占用者并给出下一步：

```text
端口 8000 无法绑定，占用者：Manager.exe (PID 6948)。
下一步（任选一条）：
  1) 换个端口启动：       python -m app --port 8001
  2) 写进仓库根 .env：    YTDL_API_PORT=8001   （前后端会自动一致）
  3) 看是谁占着：         netstat -ano -p tcp | findstr :8000
  也可以直接加 --auto-port，让本命令自己往后找一个可用端口。
```

三点需要知道的事：

- **退出码是 2**，默认**不会**静默换端口。静默换端口会制造「后端在 8001、前端还代理 8000」这类前后端不一致的错配，而那种失败现象离病因很远。要自动换端口就显式加 `--auto-port`。
- **`WinError 10013` 不是权限问题。** 它在「占用者以 `SO_EXCLUSIVEADDRUSE` 独占通配地址」时出现，见 [009](../ai/bug-fix/009-local-dev-port-is-occupied-and-unconfigurable.md)；本机开发机上长期占着 `8000` 的是 IncrediBuild 的 `Manager.exe`。
- **别用裸 uvicorn 排查端口问题**：`python -m uvicorn app.main:app --port 8000` 只会给出那句 `WinError 10013`，没有占用者、没有下一步。

## 目录结构

| 路径 | 说明 |
| --- | --- |
| `backend/app` | FastAPI 应用、任务管理、yt-dlp 封装、数据库模型和 cookies 导入。 |
| `backend/tests` | 后端 pytest 测试和 fake service。 |
| `frontend/src` | React UI、API 客户端、类型、展示组件和测试夹具。 |
| `data` | 本地 SQLite、cookies，已被 Git 忽略。 |
| `downloads` | 默认下载产物目录，已被 Git 忽略。 |
| `docs` | 工程文档、PlantUML 源和渲染图。 |
| `scripts` | 可复现的工程辅助脚本：文档工具入口 [docs.py](../scripts/docs.py) 与锚点检查 [check_doc_anchors.py](../scripts/check_doc_anchors.py)；离线基准 [bench_concurrency.py](../scripts/bench_concurrency.py)、[bench_throttle_guard.py](../scripts/bench_throttle_guard.py)；cookies 导出 [export_cookies_via_cdp.py](../scripts/export_cookies_via_cdp.py)；需真实网络的验收 [acceptance_network.py](../scripts/acceptance_network.py)、[acceptance_real.py](../scripts/acceptance_real.py)。 |
| `.tools` | 文档工具自动下载的本机缓存，已被 Git 忽略。 |
| `ai` | 任务计划、重构日志和文档生成 prompt。 |
| `ai/perf/PLAN.md` | 下载性能与稳定性修复计划：根因分析、修复项、验证与回滚，是引用性能结论时的权威来源。 |
| `ai/bug-fix/` | 逐条修复记录（001~010）与「已知但未处理」清单；写限制与遗留风险时以此为准。 |
| `ai/docs/` | 文档任务的原始需求与可重复执行的撰写 prompt。 |

## 环境变量

配置类定义见 [AppSettings](../backend/app/config.py#L19)，前缀为 `YTDL_`，并会读取仓库根目录的 `.env`。`env_file` 用的是**绝对路径**（`REPO_ROOT / ".env"`），所以与你从哪个目录启动无关 —— 相对路径会按当前工作目录解析，而文档里的命令都是 `cd backend` 之后执行的。`.env` 已被 Git 忽略。下表列出 `AppSettings` 的全部 22 个字段及其当前默认值；另有 `YTDL_LOG_LEVEL`（默认 `INFO`）不经过 `AppSettings`，由 [configure_logging](../backend/app/logging_setup.py#L48) 直接读取。

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `YTDL_API_PORT` | `8000` | 本机 API 端口。`python -m app` 启动时生效，`--port` 可临时覆盖；前端 Vite 的 `/api` 代理读的是同一个变量。见 [端口被占用时](#端口被占用时)。 |
| `YTDL_DATA_DIR` | `data/` | 数据库和 cookies 目录。 |
| `YTDL_DOWNLOAD_DIR` | `downloads/` | 下载产物目录。 |
| `YTDL_DATABASE_PATH` | `data/app.sqlite3` | SQLite 文件路径。WAL 模式下同目录还会出现 `*.sqlite3-wal` 和 `*.sqlite3-shm`，已由 `.gitignore` 忽略。 |
| `YTDL_COOKIES_FILENAME` | `cookies.txt` | cookies 文件名，实际路径为 `YTDL_DATA_DIR / YTDL_COOKIES_FILENAME`。 |
| `YTDL_DEFAULT_CONCURRENCY` | 同 `YTDL_YOUTUBE_MAX_PARALLEL_DOWNLOADS` | 同时下载的视频数（跨任务和合集子项）。 |
| `YTDL_DEFAULT_RESOLUTION` | `1440p` | 默认清晰度。 |
| `YTDL_DEFAULT_SUBTITLE_LANGUAGES` | `["en"]` | 默认字幕语言；列表类型，作为环境变量时用 JSON 语法提供。 |
| `YTDL_DEFAULT_SPEED_LIMIT_KBPS` | 空 | 全局默认限速，空值表示不限速。 |
| `YTDL_DEFAULT_RETRIES` | `10` | 默认下载重试次数，环境变量层面取值范围 `0..20`。 |
| `YTDL_YOUTUBE_PO_TOKEN` | 空 | 高级排障用 YouTube PO token。 |
| `YTDL_YOUTUBE_VISITOR_DATA` | 空 | 与 PO token 配套的 visitor data。 |
| `YTDL_YOUTUBE_PO_BROWSER_PATH` | 空 | PO-token provider 使用的浏览器路径。 |
| `YTDL_JS_RUNTIME_PATH` | 空 | JS 运行时（Deno / Node）可执行文件路径，用于解 YouTube 的 nsig。留空时按「PATH → 常见安装目录」自动探测，探测不到会直接导致提取失败。见 [技术文档](technical.md#js-运行时与-n-challenge)。 |
| `YTDL_PROXY` | 空 | 代理。留空 = 自动（优先 Windows 系统代理，其次 `HTTP_PROXY`/`HTTPS_PROXY` 环境变量）；`direct`/`none`/`off` = 强制直连；其余按代理 URL 处理，缺 scheme 时补 `http://`（`127.0.0.1:7890` 可直接写）。解析逻辑见 [proxy.py](../backend/app/proxy.py#L169)。 |
| `YTDL_YOUTUBE_MAX_PARALLEL_DOWNLOADS` | `5` | YouTube 同时下载的视频数；若追求稳定，可设为 `1`。 |
| `YTDL_ANTI403_HTTP_CHUNK_SIZE_MB` | `16` | HTTP chunk 大小，按块重开请求以降低 403 概率。 |
| `YTDL_THROTTLED_RATE_KBPS` | `0`（关闭） | 单条流低速重取阈值。`> 0` 时会写入 yt-dlp 的 `throttledratelimit`，见 [技术文档](technical.md#稳定下载策略)。 |
| `YTDL_STALL_TIMEOUT_SECONDS` | `90.0` | 停滞看门狗阈值；该秒数内没有任何新增字节就让任务失败。`0` 表示关闭。 |
| `YTDL_ARIA2C_ENABLED` | `false` | 是否启用 aria2c fallback。 |
| `YTDL_ARIA2C_PATH` | 空 | aria2c 可执行文件路径或命令名；为空时按 PATH 查找。 |
| `YTDL_ARIA2C_CONNECTIONS` | `2` | aria2c 每文件连接数，取值范围 `1..4`。连接越多，403/限速风险越高。 |

运行时可在设置面板修改的字段（下载目录、并发、默认清晰度、字幕语言、限速、重试次数、aria2c 连接数）会写入 `Setting` 表并在下次启动时覆盖环境变量，见 [_apply_stored_settings](../backend/app/main.py#L675)。

## PlantUML 图更新

文档生成环境已经作为项目工程交付物提供，完整说明见 [文档写作与生成环境](documentation-workflow.md)。首次使用时在仓库根目录初始化：

```powershell
python scripts\docs.py bootstrap
```

修改 UML 后统一渲染 SVG：

```powershell
python scripts\docs.py render
```

提交前检查本地链接和 UML 产物一致性：

```powershell
python scripts\docs.py check
```

PlantUML jar 会下载到被 Git 忽略的 `.tools/docs/`，无需全局安装 `plantuml` CLI，也不要提交该缓存目录。

`check` 会把每个已提交 SVG 与「用当前 Java + Graphviz + 固定版本 PlantUML 现场渲染」的结果逐字节比较。Graphviz 版本不同会改变布局字节，因此换机器或升级 Graphviz 后 `check` 期望你先重新 `render` 并提交 SVG，详见 [文档写作与生成环境](documentation-workflow.md#渲染器版本敏感性)。

## 开发检查

修改文档或代码后至少运行：

```powershell
git diff --check
```

涉及代码行为时还需运行 [测试文档](testing.md#自动测试命令) 中的后端和前端验证。
