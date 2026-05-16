# YouTube Downloader

本项目是一个本机单用户 YouTube 下载控制台：FastAPI 后端负责调用 `yt-dlp`、维护 SQLite 任务状态和 SSE 进度推送；React/Vite 前端提供链接解析、下载选项、字幕选择和任务中心。

请只下载你拥有权利或已获得许可的内容。本项目不实现 DRM 绕过，也不面向公网部署或多用户权限场景。

## 功能概览

- 单视频下载：解析标题、封面、时长、格式、字幕语言和自动字幕语言。
- Playlist 批量下载：支持全选/多选条目，单项失败不会阻断整批。
- 清晰度选择：默认 1080p；若视频不支持 1080p，会自动选用可用的最高分辨率。下载选项用一个“清晰度 / 格式”下拉同时支持分辨率策略和具体 `format_id`，具体格式会显示分辨率、fps、扩展名和文件大小，缺失时标注“大小未知”。
- 字幕下载：支持视频+字幕、仅视频、仅字幕；字幕语言为可搜索下拉多选；支持人工字幕、自动字幕或两者都要。
- Cookies 文件：支持手动上传/清除 `cookies.txt`，用于需要登录态或年龄验证的视频；不会在 UI 或日志中回显内容。
- 任务中心：支持单任务和批量暂停、重启、删除；playlist 任务支持展开/折叠，运行中或失败时自动展开；实时显示任务与子视频的百分比、大小、已用时、预计剩余时间、下载速度、状态和失败原因。
- 可靠性选项：跳过已下载、限速、重试次数、保存 metadata、保存缩略图、完成后通知开关。
- 运行诊断：检测 `ffmpeg`、`ffprobe`、`yt-dlp` 版本和 JS runtime 状态。

## 技术栈

- 后端：Python 3.12+、FastAPI、SQLModel、SQLite、yt-dlp。
- 前端：React 18、TypeScript、Vite、lucide-react、原生 CSS。
- 下载引擎：通过 yt-dlp Python API 执行分析和下载，使用 progress hooks 写入任务进度。

## 环境要求

- Python 3.12 或更高版本。
- Node.js 20+ 推荐；也可以安装 Deno。新版本 YouTube 页面解析有时需要 JS runtime，后端诊断页会提示状态。
- `ffmpeg` 和 `ffprobe` 推荐安装。没有它们时仍会尽量下载单文件格式，但高分辨率音视频合并可能受限。

Windows 可用 `winget` 安装常见依赖：

```powershell
winget install Python.Python.3.12
winget install OpenJS.NodeJS.LTS
winget install Gyan.FFmpeg
```

## 安装

后端依赖：

```powershell
cd backend
python -m pip install -e ".[dev]"
```

前端依赖：

```powershell
cd frontend
npm install
```

## 本地运行

启动后端：

```powershell
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

启动前端开发服务器：

```powershell
cd frontend
npm run dev -- --port 5173
```

打开 `http://127.0.0.1:5173`。Vite 会把 `/api` 请求代理到后端 `http://127.0.0.1:8000`。

也可以先构建前端，再让后端直接托管静态文件：

```powershell
cd frontend
npm run build
cd ../backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

然后打开 `http://127.0.0.1:8000`。

## 使用流程

1. 在首页输入单视频或 playlist 链接，点击“解析链接”。
2. 如果是 playlist，在表格里选择要下载的条目。
3. 设置下载模式、清晰度 / 格式、字幕语言、字幕来源和字幕格式；具体格式会展示对应分辨率和大小。
4. 按需开启 metadata、缩略图、跳过已下载、限速、重试和通知选项。
5. 点击“加入下载队列”，在任务中心查看实时进度。
6. 对单任务或多选任务执行暂停、重启、删除；playlist 任务可展开或折叠查看子视频。

## Cookies

如果视频需要登录态、地区/年龄确认，先从浏览器导出 Netscape 格式的 `cookies.txt`，再在右侧 Cookies 面板上传。文件会保存到本地 `data/cookies.txt`，该目录已在 `.gitignore` 中排除。

清除 cookies 后，后续分析和下载会回到无登录态模式。

## 配置

默认目录：

- 数据库和 cookies：`data/`
- 下载产物：`downloads/`

可通过环境变量覆盖部分配置，前缀为 `YTDL_`：

```powershell
$env:YTDL_DOWNLOAD_DIR="D:\Videos\YouTube"
$env:YTDL_DEFAULT_CONCURRENCY="8"
$env:YTDL_DEFAULT_RESOLUTION="1080p"
```

前端设置面板也支持修改下载目录、并发数和默认清晰度。未保存自定义值时，并发数默认使用本机逻辑 CPU core 数量，最低为 1，不额外设置上限。

## API 摘要

- `POST /api/analyze`：解析视频或 playlist。
- `POST /api/jobs`：创建下载任务。
- `GET /api/jobs`、`GET /api/jobs/{id}`：读取任务列表和详情。
- `POST /api/jobs/{id}/pause`：暂停任务。
- `POST /api/jobs/{id}/restart`：重启任务。
- `DELETE /api/jobs/{id}`：删除任务。
- `POST /api/jobs/batch`：批量暂停、重启或删除任务。
- `POST /api/jobs/{id}/cancel`：保留的取消接口。
- `GET /api/events`：SSE 任务事件流。
- `GET /api/settings`、`PUT /api/settings`：读取/更新设置。
- `POST /api/cookies`、`DELETE /api/cookies`：上传/清除 cookies。
- `GET /api/diagnostics`：依赖和运行状态诊断。

## 测试

默认自动测试采用分层策略：后端使用单元测试和 mock 覆盖 yt-dlp 映射、任务状态与 API 行为，前端使用组件测试覆盖解析、字幕选择、任务中心和格式元信息展示；真实 YouTube 下载作为可选手动验收，不纳入默认测试命令。

后端：

```powershell
python -m pytest backend\tests -q
```

前端：

```powershell
cd frontend
npm test
npm run build
```

手动验收建议：解析一个公开视频，确认具体格式下拉同时显示分辨率与大小；解析一个小 playlist，确认条目选择、任务中心控制和进度显示正常；可选再执行一次 720p 视频下载和一次字幕单独下载。

## 常见问题

- 分析成功但下载失败：先检查 `GET /api/diagnostics` 或顶部状态，确认 `yt-dlp`、JS runtime、`ffmpeg`/`ffprobe` 是否可用。
- 高分辨率不可用：YouTube 常把视频和音频分离，缺少 `ffmpeg` 时无法合并，系统会降级到可直接下载的单文件格式。
- 需要登录的视频失败：上传有效的 `cookies.txt` 后重新解析并创建任务。
- 新任务立刻失败：查看任务中心失败原因；常见原因包括网络不可达、cookies 失效、视频不可用、格式被删除或 yt-dlp 需要更新。
- 下载目录无文件：确认设置中的下载目录存在且有写入权限。

## 项目结构

```text
backend/
  app/
    main.py            FastAPI 路由和响应组装
    job_manager.py     队列、任务状态、SSE 事件
    ytdlp_service.py   yt-dlp 分析和下载封装
    models.py          SQLite 表模型
    schemas.py         API DTO
    config.py          本地配置
  tests/
frontend/
  src/
    App.tsx            下载控制台 UI
    api.ts             前端 API 客户端
    types.ts           前端类型
    styles.css         页面样式
ai/
  plan.md              按时间戳追加的实现和审计计划
```

## 维护约定

- 下载产物、cookies、SQLite 数据库、日志和依赖目录不进入 Git。
- 后端 API 不接收任意 yt-dlp 参数，只暴露项目定义的安全选项。
- 以后每次修改功能、命令、依赖或运行方式，都同步更新本 README。
