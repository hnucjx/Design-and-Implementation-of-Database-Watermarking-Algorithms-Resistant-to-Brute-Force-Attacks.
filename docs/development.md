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

普通使用和手动验收优先使用 [README 快速启动](../README.md#快速启动)：先执行 `npm run build` 生成完整的 `frontend/dist/index.html` 和 `frontend/dist/assets/`，再启动后端并打开 `http://127.0.0.1:8000`。此时 FastAPI 同时提供页面、静态资源和 `/api` 接口；入口逻辑见 [main.py](../backend/app/main.py#L392)。

### 前端热更新开发模式

需要修改 React UI 时，先启动后端 API：

```powershell
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

再启动 Vite dev server：

```powershell
cd frontend
npm run dev -- --port 5173
```

开发时打开 `http://127.0.0.1:5173`。Vite 会热更新前端代码，并将 `/api` 请求代理到 `http://127.0.0.1:8000`，代理配置见 [vite.config.ts](../frontend/vite.config.ts)。

## 目录结构

| 路径 | 说明 |
| --- | --- |
| `backend/app` | FastAPI 应用、任务管理、yt-dlp 封装、数据库模型和 cookies 导入。 |
| `backend/tests` | 后端 pytest 测试和 fake service。 |
| `frontend/src` | React UI、API 客户端、类型、展示组件和测试夹具。 |
| `data` | 本地 SQLite、cookies，已被 Git 忽略。 |
| `downloads` | 默认下载产物目录，已被 Git 忽略。 |
| `docs` | 工程文档、PlantUML 源和渲染图。 |
| `scripts` | 可复现的工程辅助脚本：文档工具入口 [docs.py](../scripts/docs.py)，离线基准 [bench_concurrency.py](../scripts/bench_concurrency.py)、[bench_throttle_guard.py](../scripts/bench_throttle_guard.py)。 |
| `.tools` | 文档工具自动下载的本机缓存，已被 Git 忽略。 |
| `ai` | 任务计划、重构日志和文档生成 prompt。 |
| `PLAN.md` | 下载性能与稳定性修复计划：根因分析、修复项、验证与回滚，是引用性能结论时的权威来源。 |

## 环境变量

配置类定义见 [AppSettings](../backend/app/config.py#L19)，前缀为 `YTDL_`，并会读取仓库根目录的 `.env`（`env_file=".env"`，`.env` 已被 Git 忽略）。下表列出全部字段及其当前默认值。

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
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
| `YTDL_YOUTUBE_MAX_PARALLEL_DOWNLOADS` | `5` | YouTube 同时下载的视频数；若追求稳定，可设为 `1`。 |
| `YTDL_ANTI403_HTTP_CHUNK_SIZE_MB` | `16` | HTTP chunk 大小，按块重开请求以降低 403 概率。 |
| `YTDL_THROTTLED_RATE_KBPS` | `0`（关闭） | 单条流低速重取阈值。`> 0` 时会写入 yt-dlp 的 `throttledratelimit`，见 [技术文档](technical.md#稳定下载策略)。 |
| `YTDL_STALL_TIMEOUT_SECONDS` | `90.0` | 停滞看门狗阈值；该秒数内没有任何新增字节就让任务失败。`0` 表示关闭。 |
| `YTDL_ARIA2C_ENABLED` | `false` | 是否启用 aria2c fallback。 |
| `YTDL_ARIA2C_PATH` | 空 | aria2c 可执行文件路径或命令名；为空时按 PATH 查找。 |
| `YTDL_ARIA2C_CONNECTIONS` | `2` | aria2c 每文件连接数，取值范围 `1..4`。连接越多，403/限速风险越高。 |

运行时可在设置面板修改的字段（下载目录、并发、默认清晰度、字幕语言、限速、重试次数、aria2c 连接数）会写入 `Setting` 表并在下次启动时覆盖环境变量，见 [_apply_stored_settings](../backend/app/main.py#L576)。

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
