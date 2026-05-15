# YouTube 下载器实现计划

## Summary
实现一个本机单用户 Web UI：FastAPI 后端 + React/Vite 前端，使用 yt-dlp Python API 执行 YouTube 单视频、playlist、字幕和多分辨率下载。首版不做公网部署，不做账号系统；支持用户上传 cookies 文件以处理需要登录态的视频。参考 yt-dlp 官方能力：格式选择、字幕选项、Python 嵌入和 progress hooks（https://github.com/yt-dlp/yt-dlp）。

## Key Changes
- 新建项目结构：
  - `backend/`：FastAPI API、任务队列、SQLite 状态库、yt-dlp 封装、配置管理。
  - `frontend/`：React + TypeScript + Vite 本地 Web UI。
  - `ai/plan.md`：保存本计划。
  - `.gitignore`：覆盖 Python、Node、下载产物、cookies、数据库、日志和编辑器文件。
- 后端核心能力：
  - `POST /api/analyze`：解析单视频或 playlist，返回标题、封面、时长、playlist 条目、可选分辨率、格式、字幕语言和自动字幕语言。
  - `POST /api/jobs`：创建下载任务，支持单视频或 playlist 批量；模式包括视频+字幕、仅视频、仅字幕。
  - `GET /api/jobs`、`GET /api/jobs/{id}`：查看任务列表和详情。
  - `POST /api/jobs/{id}/cancel`：取消未完成任务。
  - `GET /api/events`：SSE 推送任务进度、速度、ETA、当前文件、成功/失败状态。
  - `GET/PUT /api/settings`：配置默认下载目录、并发数、默认字幕语言、默认格式策略。
  - `POST /api/cookies`、`DELETE /api/cookies`：上传/清除 cookies 文件；不在日志或 UI 中回显内容。
- 下载策略：
  - 默认格式为最佳视频+最佳音频并合并为 mp4/mkv；用户可选 best、2160p、1440p、1080p、720p、480p 或具体 format_id。
  - 字幕支持人工字幕、自动字幕、语言多选、格式优先级 `srt/vtt/best`。
  - playlist 支持全选、按序号范围选择、跳过已下载、失败继续、单项失败不阻断整批。
  - 需要 ffmpeg/ffprobe；启动时检测缺失并在 UI 显示可执行提示。
- 前端体验：
  - 首页即下载控制台，不做营销页。
  - URL 输入区支持单链接/playlist；分析后显示视频或 playlist 表格。
  - 右侧/顶部提供下载模式、分辨率、字幕语言、输出目录、cookies 状态、并发数。
  - 任务中心显示队列、单项进度、总进度、失败原因、取消按钮、打开下载目录提示。
  - 额外 brainstorm 功能：下载历史、重复链接提示、缩略图和 metadata 保存开关、速度限制、重试次数、完成后声音/系统通知开关。

## Implementation Plan
- 初始化后端：
  - 创建 `backend/pyproject.toml`，依赖 `fastapi`、`uvicorn`、`yt-dlp`、`pydantic-settings`、`sqlmodel`、`pytest`、`httpx`。
  - 创建 `backend/app/main.py`、`config.py`、`db.py`、`schemas.py`、`models.py`。
  - 配置默认数据目录为本地 `data/`，下载目录默认为 `downloads/`。
- 实现 yt-dlp 服务层：
  - `extract_metadata(url, cookies_path=None)` 使用 `yt_dlp.YoutubeDL(...).extract_info(download=False)`。
  - 将 yt-dlp 原始 formats/subtitles 转成前端友好的 DTO。
  - `download_job(job_id, options)` 通过 progress hook 写入 SQLite 并广播 SSE。
  - 下载选项统一从 API 请求转换为 yt-dlp opts，避免前端直接传任意 yt-dlp 参数。
- 实现任务系统：
  - SQLite 表：`settings`、`jobs`、`job_items`、`events`。
  - 后台 worker 使用 `asyncio.Queue` + 线程池运行阻塞下载；默认并发 2。
  - 任务状态固定为 `queued`、`running`、`succeeded`、`failed`、`cancelled`。
- 实现前端：
  - Vite React TypeScript 项目。
  - 页面组件：`UrlAnalyzer`、`DownloadOptions`、`PlaylistTable`、`JobQueue`、`SettingsPanel`、`CookieManager`。
  - 使用原生 CSS，确保桌面和移动端可用；任务表格在窄屏改为紧凑列表。
- 生成 `.gitignore`：
```gitignore
__pycache__/
*.py[cod]
.venv/
.env
.env.*
!.env.example
.pytest_cache/
.ruff_cache/

node_modules/
dist/
.vite/
coverage/

downloads/
data/
*.sqlite
*.sqlite3
*.db
cookies*.txt
*.part
*.ytdl
*.log

.DS_Store
Thumbs.db
.vscode/
.idea/
```

## Test Plan
- 后端单元测试：
  - analyze 单视频返回格式、字幕和基础 metadata。
  - analyze playlist 返回条目列表，并能处理空 playlist/无权限错误。
  - 请求选项能正确映射到 yt-dlp opts：分辨率、仅字幕、字幕语言、cookies。
  - 任务状态流转：queued -> running -> succeeded/failed/cancelled。
- 前端测试：
  - 输入 URL 后显示分析结果。
  - playlist 可多选条目并提交批量任务。
  - 字幕模式、分辨率选择、cookies 状态能正确反映到请求体。
- 集成/验收：
  - 使用一个公开视频下载 720p 视频。
  - 使用一个公开视频单独下载英文/中文字幕。
  - 使用一个小 playlist 批量下载，确认失败项不阻断其他项。
  - 在缺少 ffmpeg 时显示明确错误，不让任务静默失败。
  - `.gitignore` 确认下载文件、cookies、SQLite、node_modules 不进入 git。

## Assumptions
- 首版只服务本机单用户，不支持公网部署、多用户权限或远程文件管理。
- cookies 仅支持手动上传文件，不自动读取浏览器。
- 下载器用于用户有权下载的内容；不实现 DRM 绕过。
- 计划文件在执行阶段写入 `ai/plan.md`。
