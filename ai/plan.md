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

---

## 2026-05-16 09:34:33 +08:00 - 现有功能审计与元信息显示增强计划

### Summary
对现有 YouTube 下载器做一次不新增大功能的审计和测试加固，重点检查当前后端下载/解析、任务中心、字幕、cookies、设置和前端交互是否稳定。已确认当前基线：`python -m pytest backend\tests -q` 通过 12 项，`npm test` 通过 3 项，`npm run build` 通过。实现时先将本计划按本标题追加到 `ai/plan.md`，以后所有计划也按“时间戳 + 计划名”追加。

### Key Changes
- 审计与回归测试：
  - 保留分层测试策略：自动测试使用 mock/单元/前端测试；真实 YouTube 下载作为可选手动验收步骤记录，不纳入默认自动测试。
  - 增补覆盖现有核心路径：解析格式元信息、字幕语言、playlist 选择、任务暂停/重启/删除、实时进度、cookies 状态、缺少 ffmpeg 时的格式降级。
  - 若审计发现明确 bug，先写失败测试复现，再做最小修复；不引入新的大功能或新架构。
- 元信息显示增强：
  - 后端优先复用现有 `FormatOption.filesize` 字段；只在发现映射遗漏时修复 `_map_formats`。
  - 前端在“具体格式”下拉中显示更完整格式文案：`format_id · 分辨率 · fps · ext · 文件大小`。
  - 文件大小用前端 helper 格式化为 `B/KB/MB/GB`；缺失时显示“大小未知”。
  - 在分析结果摘要或下载选项附近显示可选格式数量，并在已选具体格式时显示当前格式的分辨率与大小。
- 文档与计划维护：
  - 将本计划追加写入 `ai/plan.md`，标题固定为本计划标题。
  - 因本次会修改功能显示与测试说明，同步更新 `README.md`，补充“格式大小显示”和审计/测试命令说明。
  - 不修改 `.gitignore`、不新增大型依赖、不增加账号/远程文件管理等新功能。

### Test Plan
- 后端：
  - `test_extract_metadata_maps_playlist_entries_formats_and_subtitles` 增加断言：`filesize` 与 `filesize_approx` 都正确进入 `FormatOption.filesize`。
  - 若发现格式 label 生成缺陷，增加对应单元测试后修复。
  - 跑全量：`python -m pytest backend\tests -q`。
- 前端：
  - 更新 `App.test.tsx` 的 analyze fixture，断言格式下拉显示 `720p` 和格式大小。
  - 增加/更新测试：选中具体格式后，页面显示当前格式分辨率和大小。
  - 跑全量：`npm test` 和 `npm run build`。
- 手动验收建议：
  - 解析一个公开视频，确认格式下拉同时显示分辨率和大小。
  - 解析一个小 playlist，确认选择条目、任务中心控制、进度显示仍正常。
  - 可选真实下载：720p 视频下载一次，英文或中文字幕单独下载一次。

### Assumptions
- 测试范围采用用户选择的“分层测试”：默认自动测试不真实下载 YouTube 内容。
- 本次增强只围绕已有功能质量、测试覆盖和元信息展示，不新增下载策略、账号系统、远程管理或新页面。
- 如果 `yt-dlp` 返回某格式没有大小，前端显示“大小未知”，不尝试额外发请求估算大小。
- 后续每次制定计划，都按“时间戳 + 计划名”追加到 `ai/plan.md`，不覆盖历史计划。

---

## 2026-05-16 +08:00 - 下载选项融合、并发默认值与合集任务展示增强计划

### Summary
在当前干净分支 `feature/audit-format-metadata` 上实现四组增量改动，并按用户要求每完成一组就 `git commit` + `git push`。执行时先将本计划按“时间戳 + 计划名”追加到 `ai/plan.md`，所有功能变更同步更新 `README.md`。

### Key Changes
- 下载清晰度融合：将下载选项里的“分辨率”和“具体格式”合并为一个“清晰度 / 格式”下拉；默认清晰度改为 `1080p`，分析后若当前视频没有 1080p，则自动选择该视频可用的最高分辨率。
- 并发数默认值：后端默认并发数改为 `os.cpu_count()` 返回的逻辑 CPU core 数量，最低为 1，不设置上限；已保存到 SQLite 的用户自定义并发数继续优先。
- 任务中心合集展开/折叠：仅对 `total_items > 1` 的合集任务显示展开/折叠按钮；运行中或失败的合集自动展开，用户手动选择后保留。
- 合集子视频信息展示：展开合集后，每个子视频显示标题、状态、百分比、已下载/总大小、已用时、ETA、速度、失败原因和独立进度条。

### Test Plan
- 每个 task 提交前至少运行对应 targeted tests。
- 每次 push 前运行：`python -m pytest backend\tests -q`、`npm test`、`npm run build`、`git diff --check`。
- 手动验收建议：解析不同分辨率公开视频，确认清晰度默认和降级；创建 playlist 下载任务，确认展开/折叠和子视频进度、大小、速度、已用时、剩余时间显示。

### Assumptions
- 清晰度与具体格式合并为单一下拉。
- 并发数默认等于完整 CPU core 数量，不设上限。
- 合集任务运行中或失败时自动展开。
- 现有 API 的 `DownloadOptions.resolution` + `format_id` wire shape 保持兼容。

---

## 2026-05-16 +08:00 - Main 分支开发、Playlist 输出目录与任务删除增强计划

### Summary
后续开发切换到 `main` 分支进行。实现 playlist 自动下载到同名子文件夹、单视频任务中心去重显示、删除任务时可选择同时删除已下载文件。执行时先将本计划追加到 `ai/plan.md`，并同步更新 `README.md`。每个功能完成后验证、commit、push。

### Key Changes
- 分支策略：先确认工作区干净，切换到 `main`，拉取 `origin/main`，将当前 `feature/audit-format-metadata` 已完成并 push 的功能快进合入 `main`，之后所有本次改动都在 `main` 上提交并推送。
- Playlist 输出目录：设置里的下载目录继续表示基础根目录；playlist 任务自动下载到 `<下载根目录>/<playlist名称>/`；单视频仍保存到下载根目录。
- 任务中心单视频去重显示：`total_items === 1` 的单视频任务不再渲染重复的 `items[0]` 子项详情；playlist 才显示可展开的子视频列表。
- 删除任务时同时删除文件：删除任务支持 `delete_files=false` 选项；开启后删除该任务已下载文件及空的 playlist 子文件夹，默认只删除任务记录。

### Test Plan
- 每个 task 先跑 targeted tests，再跑完整验证。
- 完整验证命令：`python -m pytest backend\tests -q`、`npm test`、`npm run build`、`git diff --check`。
- 手动验收：创建 playlist 任务，确认下载到 `<下载目录>/<playlist名称>/`；创建单视频任务，确认任务中心不重复显示同一个视频；分别验证删除任务时文件保留和文件删除两种路径。

### Assumptions
- 采用用户确认的方案 1：设置里的下载目录保持为基础根目录，playlist 自动使用其下同名子文件夹。
- 后续默认在 `main` 分支开发，除非用户明确指定其他分支。
- 不新增远程文件管理功能；删除文件仅限本机已下载产物。
- 对已有历史任务，如果没有任务级下载目录，则按当前基础下载目录兼容处理。
