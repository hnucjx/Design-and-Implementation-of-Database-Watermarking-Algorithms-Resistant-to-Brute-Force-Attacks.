# 开发文档

适用读者：需要搭建本地环境、运行服务、修改文档或参与开发的维护者。普通用户只需要按 [README 快速启动](../README.md#快速启动) 使用单端口模式；本文只展开开发者需要理解的依赖、配置和热更新模式。

## 技术栈

| 层 | 技术 | 依据 |
| --- | --- | --- |
| 后端 | Python 3.12+、FastAPI、SQLModel、SQLite、yt-dlp、curl_cffi、yt-dlp-getpot-wpc、imageio-ffmpeg | [backend/pyproject.toml](../backend/pyproject.toml) |
| 前端 | React 18、TypeScript、Vite、lucide-react、原生 CSS | [frontend/package.json](../frontend/package.json) |
| 测试 | pytest、pytest-asyncio、Vitest、Testing Library、jsdom | [backend/pyproject.toml](../backend/pyproject.toml)、[frontend/package.json](../frontend/package.json) |
| 图 | PlantUML、Java、Graphviz | [docs/diagrams](diagrams/) |

## 环境要求

- Python 3.12 或更高版本。
- Node.js 20+ 推荐。
- Java 和 Graphviz 用于渲染 PlantUML 图。
- `ffmpeg` 推荐安装；如果 PATH 中没有系统 `ffmpeg`，后端会尝试使用 `imageio-ffmpeg` 后备执行文件。
- `aria2c` 可选，仅当启用 `YTDL_ARIA2C_ENABLED=true` 时作为下载 fallback。

Windows 可用：

```powershell
winget install Python.Python.3.12
winget install OpenJS.NodeJS.LTS
winget install Gyan.FFmpeg
winget install Graphviz.Graphviz
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

普通使用和手动验收优先使用 [README 快速启动](../README.md#快速启动)：先执行 `npm run build` 生成 `frontend/dist`，再启动后端并打开 `http://127.0.0.1:8000`。此时 FastAPI 同时提供页面、静态资源和 `/api` 接口；入口逻辑见 [main.py](../backend/app/main.py#L373)。

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
| `ai` | 任务计划、重构日志和文档生成 prompt。 |

## 环境变量

配置类定义见 [AppSettings](../backend/app/config.py#L19)，前缀为 `YTDL_`。

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `YTDL_DATA_DIR` | `data/` | 数据库和 cookies 目录。 |
| `YTDL_DOWNLOAD_DIR` | `downloads/` | 下载产物目录。 |
| `YTDL_DATABASE_PATH` | `data/app.sqlite3` | SQLite 文件路径。 |
| `YTDL_DEFAULT_CONCURRENCY` | 来自 `YTDL_YOUTUBE_MAX_PARALLEL_DOWNLOADS` 或 `5` | 后台 worker 并发。 |
| `YTDL_DEFAULT_RESOLUTION` | `1080p` | 默认清晰度。 |
| `YTDL_YOUTUBE_PO_TOKEN` | 空 | 高级排障用 YouTube PO token。 |
| `YTDL_YOUTUBE_VISITOR_DATA` | 空 | 与 PO token 配套的 visitor data。 |
| `YTDL_YOUTUBE_PO_BROWSER_PATH` | 空 | PO-token provider 使用的浏览器路径。 |
| `YTDL_YOUTUBE_MAX_PARALLEL_DOWNLOADS` | `5` | YouTube 下载默认并发；若追求稳定，可设为 `1`。 |
| `YTDL_ANTI403_HTTP_CHUNK_SIZE_MB` | `16` | HTTP chunk 大小。 |
| `YTDL_THROTTLED_RATE_KBPS` | `64` | 低速重取 media URL 阈值，`0` 表示禁用。 |
| `YTDL_ARIA2C_ENABLED` | `false` | 是否启用 aria2c fallback。 |
| `YTDL_ARIA2C_PATH` | 空 | aria2c 可执行文件路径或命令名。 |
| `YTDL_ARIA2C_CONNECTIONS` | `1` | aria2c 连接数，建议保持 1。 |

## PlantUML 图更新

UML 源文件位于 `docs/diagrams/`。推荐渲染 SVG：

```powershell
plantuml -tsvg docs\diagrams\*.puml -o ..\assets\diagrams
```

如果没有 `plantuml` CLI，可临时下载 PlantUML jar 后执行：

```powershell
java -jar <plantuml.jar> -tsvg docs\diagrams\*.puml -o ..\assets\diagrams
```

不要把临时 jar 提交进 Git。

## 开发检查

修改文档或代码后至少运行：

```powershell
git diff --check
```

涉及代码行为时还需运行 [测试文档](testing.md#自动测试命令) 中的后端和前端验证。
