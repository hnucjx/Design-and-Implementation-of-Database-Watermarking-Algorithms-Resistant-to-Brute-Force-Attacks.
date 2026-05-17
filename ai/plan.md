# YouTube 下载器实现计划

## 2026-05-17 +08:00 - 修复清晰度选择静默降级为 360p 计划

### Summary
- 已定位根因：当前环境找不到系统 `ffmpeg/ffprobe`，后端因此走“单文件格式”选择器；YouTube 目标视频的 1440p/1080p 都是 video-only，只有 `format_id=18` 是带音频的 360p mp4，所以 1080p 请求被静默降级为 360p。
- 修复目标：用户选择 1440p/1080p/360p 时，实际输出视频高度必须匹配所选清晰度；如果无法做到，任务必须失败并给出明确原因，绝不静默下载低清。
- 执行时先按本标题追加到 `ai/plan.md`，修复后同步更新 `README.md`，完成验证后 `git commit` + `git push origin main`。

### Key Changes
- 新增 `imageio-ffmpeg` 作为后备 ffmpeg provider；系统 PATH 找不到 ffmpeg 时，自动使用包内 ffmpeg，并把路径传给 yt-dlp 的 `ffmpeg_location`。
- `get_ffmpeg_status()` 以“系统 ffmpeg 或内置 ffmpeg 任一可用”为 `ffmpeg=True`；`ffprobe` 仍按系统可用性报告。
- 高分辨率格式选择改为精确高度优先：例如 1080p 使用 `height=1080` 的 video-only + audio 组合，不再通过 `/best` 静默降级到 360p。
- 对 `best` 保留最佳可用策略；对用户明确选择的 `1440p/1080p/360p`，要求输出高度匹配。若 ffmpeg 完全不可用且无法合并，抛出清晰错误。
- 具体 `format_id` 选择继续保留，但优先与最佳音频合并；有 ffmpeg 时不降级到 `best`。
- 前端下载选项附近在 ffmpeg 不可用时显示明确警告；README 说明内置 ffmpeg fallback 和不再静默降级。

### Test Plan
- 后端新增测试：无系统 ffmpeg 但 `imageio-ffmpeg` 可用时，`build_download_options()` 设置 `ffmpeg_location`，并对 `1080p` 生成精确高度合并选择器。
- 后端新增测试：`1080p` 选择器不得包含会降级到任意低清的裸 `/best` fallback。
- 后端新增测试：ffmpeg 完全不可用且请求高分辨率时抛出明确错误。
- 自动回归：`python -m pytest backend\tests -q`、`npm test`、`npm run build`、`git diff --check`。
- 真实下载验收：使用 `https://youtu.be/NReDubvNjRg?si=glPVWHBZaB91s36W` 分别下载 `1440p`、`1080p`、`360p` 到 `downloads/resolution-verification/`，并确认输出高度为 1440、1080、360。

### Assumptions
- 采用“内置后备 ffmpeg”方案，允许新增 `imageio-ffmpeg` 依赖。
- 目标行为是“匹配所选清晰度或失败”，不再接受静默降级到低清。
- 真实下载测试文件位于已忽略的 `downloads/` 下，不进入 Git。

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
## 2026-05-16 23:56:56 +08:00 - UI 简化与合集子视频重启计划

### Summary
本次专注现有 UI 的减法和一个小型任务中心增强：移除不必要的面板说明文字，移除设置中的“默认清晰度”入口，把任务数量移动到任务中心标题同行右侧，并为 playlist 子视频增加单项重启。继续在 `main` 分支开发；每完成一组改动就测试、提交并 `git push`。README 随功能变化同步更新。

### Key Changes
- UI 简化：
  - 移除“解析链接”“下载选项”“设置”标题下方的补充说明文字。
  - 保留面板标题和图标，不改变核心操作入口。
- 设置面板简化：
  - 移除“默认清晰度”设置控件，避免和单次下载的“清晰度 / 格式”选择重复。
  - 设置面板聚焦下载根目录和并发数；清晰度仍由下载选项中的“清晰度 / 格式”决定，初始默认值继续由应用内默认 `1080p` 提供。
  - 后端设置 API 暂保留 `default_resolution` 字段以维持兼容，不在 UI 中暴露。
- 任务中心标题优化：
  - 将“x 个任务 / 暂无任务”移动到“任务中心”同行最右侧。
- Playlist 子视频重启：
  - 为 playlist 展开后的每个子视频添加独立重启按钮。
  - 后端新增受控 API，只允许重置指定 `job_item`，并将所属任务重新排队；不新增大架构。
  - 子视频重启会清空该子项进度、大小、速度、ETA、错误和输出路径，并刷新所属任务统计。

### Test Plan
- 前端：
  - 断言三个面板标题下的说明文字不再渲染。
  - 断言设置面板不再显示“默认清晰度”，保存设置时不提交 `default_resolution`。
  - 断言任务数量与任务中心标题位于同一标题区域。
  - 断言 playlist 子视频显示重启按钮，点击后调用子项重启 API 并更新任务。
- 后端：
  - 增加 playlist 子项重启 API 测试：重置单个 `JobItem`、刷新任务状态并返回更新后的 `JobRead`。
  - 继续跑全量：`python -m pytest backend\tests -q`、`npm test`、`npm run build`、`git diff --check`。

### Assumptions
- “默认清晰度”只从前端设置 UI 移除，后端字段暂保留以避免破坏现有配置和测试。
- 子视频重启只针对 playlist 子项显示；单视频继续使用任务级重启。
- 删除/暂停/批量操作行为保持不变。
## 2026-05-17 10:03:11 +08:00 - 设置 UI 即时保存与下载目录选择计划

### Summary
本次只优化设置面板：移除看不出作用的“保存设置”按钮，改为设置项即时保存；将并发说明并入标签；新增本机文件夹选择对话框用于选择下载根目录。playlist 下载目录逻辑保持不变：若下载根目录为 `dir`，playlist 名称为 `list`，实际保存到 `dir/list/`。

### Key Changes
- 保存设置按钮：
  - 移除“保存设置”按钮。
  - 并发数输入仍保留，用户修改后在控件失焦时保存到 `/api/settings`。
  - UI 保留轻量保存状态提示，避免用户不知道修改是否生效。
- 并发标签：
  - 将下方说明“默认跟随 CPU core 数量，可按需覆盖。”移到标签中，改为“并发 (默认跟随 CPU Core 数量，可按需调整。)”。
- 下载目录选择：
  - 新增后端受控 API 打开本机目录选择对话框，不新增大型依赖，优先使用 Python 标准库 `tkinter.filedialog.askdirectory`。
  - 前端“下载目录”显示当前路径，并提供“选择文件夹”按钮；选择后立即保存设置并刷新 UI。
  - 保持默认下载目录和 playlist 自动同名子文件夹逻辑不变。

### Test Plan
- 前端：
  - 断言设置面板不再显示“保存设置”按钮。
  - 断言并发标签文案为“并发 (默认跟随 CPU Core 数量，可按需调整。)”。
  - 断言修改并发并失焦会调用 `PUT /api/settings`。
  - 断言点击“选择文件夹”会调用目录选择 API 并更新路径显示。
- 后端：
  - 测试目录选择 API 使用注入的选择器返回路径，并持久化为下载根目录。
  - 测试用户选择下载根目录后，playlist 任务仍创建 `<dir>/<playlist 名称>/`。
- 全量验证：
  - `python -m pytest backend\tests -q`
  - `npm test`
  - `npm run build`
  - `git diff --check`

### Assumptions
- 浏览器无法可靠读取任意本机文件夹绝对路径，因此目录选择由本机后端弹出系统对话框完成。
- 若运行环境没有 GUI 或用户取消选择，后端返回当前设置或明确错误，不改变下载目录。
- 本次不改变 cookies、任务中心、playlist 子目录生成规则或真实下载策略。

---

# 2026-05-17 +08:00 - 任务时间/实际分辨率展示与 ffprobe 警告移除计划

## Summary
- 实现前先将本计划按标题追加到 `ai/plan.md`，所有功能变更同步更新 `README.md`。
- “任务中心”每个任务显示开始时间、结束时间、实际下载分辨率；playlist 在合集行显示统一结果：相同则显示 `1920x1080`，不同则显示 `混合分辨率`。
- `ffprobe` 不是当前下载/合并的必需依赖；顶栏移除 `ffprobe` 红色警告，只保留 `ffmpeg` 与 cookies 状态。

## Key Changes
- 后端新增实际分辨率记录：给 `job_items` 增加 nullable `actual_width`、`actual_height`，通过现有轻量迁移补列，并在 `JobItemRead` 暴露。
- 后端新增 `JobRead.actual_resolution` 聚合字段：单视频使用唯一子项分辨率；playlist 若已知分辨率一致显示具体尺寸，若不一致显示 `混合分辨率`，未知时返回 `null`。
- 下载完成时从 yt-dlp progress payload 的 `info_dict/requested_formats` 提取实际宽高；若缺失，则用当前可用的 `ffmpeg` 执行文件探测兜底。重启任务或重启 playlist 子视频时清空旧分辨率。
- 前端 `JobQueue` 在任务指标区显示：开始时间、结束时间、实际分辨率；时间使用稳定格式 `YYYY-MM-DD HH:mm:ss`，空值显示 `--`，未知分辨率显示 `检测中`。
- 前端顶栏移除 `ffprobe` 状态 pill；README 说明 `ffmpeg` 是高分辨率合并所需，`ffprobe` 仅为可选诊断能力，不再作为警告项。

## Test Plan
- 先写失败测试再实现：
  - 后端 API 测试：任务响应包含 `started_at`、`finished_at`、`actual_resolution`，子项包含 `actual_width/actual_height`。
  - 后端 playlist 测试：多个子视频分辨率相同返回具体尺寸，不同返回 `混合分辨率`。
  - 后端 helper 测试：能从 yt-dlp `requested_formats` 提取 video-only 的实际宽高。
  - 前端测试：任务中心展示开始时间、结束时间、实际分辨率；playlist 展示 `混合分辨率`。
  - 前端测试：当 `settings.ffmpeg.ffprobe=false` 时不再渲染 `ffprobe` 红色状态。
- 每个编号任务完成后运行对应测试并提交推送：
  - `git commit -m "feat: show job timing and actual resolution"`，`git push origin main`
  - `git commit -m "chore: remove optional ffprobe warning"`，`git push origin main`
- 最终全量验证：
  - `python -m pytest backend\tests -q`
  - `npm test`
  - `npm run build`
  - `git diff --check`

## Assumptions
- `ffprobe` 不作为必需依赖；当前应用用 `ffmpeg` 已能满足下载合并与必要探测。
- playlist 合集行只需要一个统一分辨率显示；子视频展开区保留现有进度/大小信息，不额外强制显示每个子视频分辨率。
- 已有历史任务若没有分辨率记录，界面显示 `检测中` 或 `--`，不做批量回填。

---

# 2026-05-17 +08:00 - 并发/限速审计与解析区 UI 整合计划

## Summary
- 实现前先将本计划追加到 `ai/plan.md`，功能变更同步更新 `README.md`。
- 修复并发数只在 worker 启动时读取、设置修改后不生效的问题。
- 将 Cookies 上传/清除整合进“解析链接”面板，移除独立 Cookies 卡片。
- 在解析结果标题区显示当前所选清晰度/格式对应的视频大小。
- 将限速默认值改为 `2048 KB/s`；清空限速输入仍表示不限速。

## Key Changes
- 并发数：
  - 根因：`JobManager.start()` 只按启动时 `default_concurrency` 创建 worker，`PUT /api/settings` 更新后不会调整已运行 worker。
  - 后端新增可动态调整并发的 manager 方法；`PUT /api/settings` 保存新并发后立即应用，无需重启服务。
  - 启动时先加载 SQLite 中已保存的设置，再启动任务 manager，确保历史并发设置也生效。

- Cookies UI：
  - 删除右侧独立 `CookieManager` 卡片。
  - 在“解析链接”面板内加入紧凑 cookies 状态行：状态文本、上传 `cookies.txt`、清除按钮。
  - 保持现有 `/api/cookies` 上传/清除接口不变；上传后刷新 settings，解析请求继续默认使用 cookies。

- 清晰度大小显示：
  - `AnalysisPanel` 接收当前下载选项。
  - 标题区在视频标题旁显示当前选择对应大小：具体 `format_id` 使用该格式大小；清晰度策略使用同高度格式中最大可用 `filesize/filesize_approx`；缺失时显示“大小未知”。
  - 选择不同清晰度或具体格式时，标题区大小实时更新；`best` 显示“最佳可用 · 大小未知”。

- 限速：
  - `DownloadOptions.speed_limit_kbps` 后端默认改为 `2048`，前端初始值也改为 `2048`。
  - 后端保持现有行为：`None/null` 不写入 yt-dlp `ratelimit`，表示不限速；`2048` 写入 `2048 * 1024` bytes/s。
  - UI 文案明确“清空表示不限速”，避免把网络或 YouTube 侧速度波动误认为本地限速。

## Test Plan
- 后端：
  - 新增 `JobManager` 并发测试：并发为 1 时两个阻塞任务只启动一个；更新为 2 后第二个无需重启服务即可启动。
  - 新增设置加载测试：SQLite 中已有 `default_concurrency` 时，应用启动使用保存值。
  - 扩展限速测试：默认 `DownloadOptions()` 生成 `ratelimit=2048*1024`；显式 `speed_limit_kbps=None` 时不包含 `ratelimit`。
  - 运行 `python -m pytest backend\tests -q`。

- 前端：
  - 更新测试断言：Cookies 控件出现在“解析链接”面板内，独立 Cookies 卡片不再渲染；上传/清除仍调用原 API 并刷新 settings。
  - 新增测试：切换 `1080p`、`720p`、具体 `format_id` 后，标题区显示对应大小或“大小未知”。
  - 新增测试：限速输入默认显示 `2048`，清空后提交任务体为 `speed_limit_kbps: null`。
  - 运行 `npm test` 和 `npm run build`。

- 提交与推送：
  - 完成并发修复后验证、提交 `fix: apply concurrency changes immediately`，`git push origin main`。
  - 完成 Cookies UI 整合后验证、提交 `feat: integrate cookies into analyzer`，`git push origin main`。
  - 完成标题区大小显示后验证、提交 `feat: show selected quality filesize`，`git push origin main`。
  - 完成限速默认值后验证、提交 `fix: default speed limit to 2048 kbps`，`git push origin main`。
  - 最终运行 `git diff --check` 并确认工作区干净。

## Assumptions
- “不填写限速”定义为不限速；默认值改为预填 `2048 KB/s`，用户清空才发送 `null`。
- 分辨率大小只使用 analyze 已返回的格式大小，不额外请求 YouTube 估算。
- Cookies 只做 UI 整合，不改变 cookies 文件存储路径、接口或安全策略。
