# YouTube Downloader

本项目是一个本机单用户 YouTube 下载控制台：FastAPI 后端负责调用 `yt-dlp`、维护 SQLite 任务状态和 SSE 进度推送；React/Vite 前端提供链接解析、下载选项、字幕选择和任务中心。

请只下载你拥有权利或已获得许可的内容。本项目不实现 DRM 绕过，也不面向公网部署或多用户权限场景。

## 功能概览

- 单视频下载：解析标题、封面、时长、格式、字幕语言和自动字幕语言。
- Playlist 批量下载：支持全选/多选条目，单项失败不会阻断整批；playlist 会自动保存到下载目录下的同名子文件夹。
- 清晰度选择：默认 1080p；若视频不支持 1080p，会自动选用可用的最高分辨率。下载选项用一个“清晰度 / 格式”下拉同时支持分辨率策略和具体 `format_id`，具体格式会显示分辨率、fps、扩展名和文件大小，缺失时标注“大小未知”；解析结果标题区也会随当前选择显示对应大小。明确选择 1440p、1080p、360p 等清晰度时，后端会要求实际下载高度匹配；无法匹配时任务失败，不再静默降级到低清。若失败原因是当前视频没有所选高度，任务中心会提示低于所选高度的最高可用分辨率，并允许一键用该分辨率重启。
- 字幕下载：支持视频+字幕、仅视频、仅字幕；字幕语言为可搜索下拉多选；支持人工字幕、自动字幕或两者都要。
- Cookies：在“解析链接”面板内支持手动上传/清除 `cookies.txt`，也支持通过 yt-dlp 从本机浏览器自动导入 YouTube/Google 相关 cookies；用于需要登录态、年龄验证或 bot 校验的视频，不会在 UI 或日志中回显内容。
- 界面简化：解析、下载选项和设置面板保留标题与核心控件，去掉重复说明文案。
- 任务中心：任务数量显示在标题同行右侧；每个任务显示开始时间、结束时间和实际下载分辨率，playlist 合集行会统一显示具体分辨率或“混合分辨率”；支持单任务和批量暂停、重启、删除；删除任务默认保留已下载文件，也可勾选“删除任务时同时删除已下载视频”；单视频任务不重复显示子项详情；playlist 任务支持展开/折叠，运行中或失败时自动展开，并显示每个子视频的百分比、大小、已用时、预计剩余时间、下载速度、状态、失败原因、分辨率 fallback 提示和单项重启按钮。
- 可靠性选项：跳过已下载、限速、重试次数、保存 metadata、保存缩略图、完成后通知开关；限速默认 2048 KB/s，清空输入表示不限速。
- 运行诊断：顶部状态显示 `ffmpeg` 和 cookies；诊断接口还会返回 `yt-dlp` 版本和 JS runtime 状态。系统 `ffmpeg` 缺失时会优先使用内置 `imageio-ffmpeg` 后备执行文件。

## 技术栈

- 后端：Python 3.12+、FastAPI、SQLModel、SQLite、yt-dlp、imageio-ffmpeg。
- 前端：React 18、TypeScript、Vite、lucide-react、原生 CSS。
- 下载引擎：通过 yt-dlp Python API 执行分析和下载，使用 progress hooks 写入任务进度。

## 环境要求

- Python 3.12 或更高版本。
- Node.js 20+ 推荐；也可以安装 Deno。新版本 YouTube 页面解析有时需要 JS runtime，后端诊断页会提示状态。
- `ffmpeg` 用于合并 YouTube 高分辨率音视频流；如果系统 PATH 中没有 `ffmpeg`，后端会使用 `imageio-ffmpeg` 提供的内置后备执行文件。`ffprobe` 不是当前下载流程的必需依赖，不会在顶部状态中作为警告显示。明确清晰度下载不会静默降级：无法合并到所选高度时会失败并在任务中心显示原因。

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
4. 按需开启 metadata、缩略图、跳过已下载、限速、重试和通知选项；限速默认 2048 KB/s，清空表示不限速。
5. 点击“加入下载队列”，在任务中心查看实时进度。
6. 对单任务或多选任务执行暂停、重启、删除；删除任务默认只删除任务记录，勾选“删除任务时同时删除已下载视频”后会同步移除已下载文件，playlist 任务还会尝试清理空的同名子文件夹。
7. Playlist 任务可展开或折叠查看子视频。

## Cookies

如果视频需要登录态、地区/年龄确认或出现 `Sign in to confirm you’re not a bot`，可以在“解析链接”面板内点击“从浏览器导入”。应用会调用 yt-dlp 从本机 Edge、Chrome、Firefox、Brave、Chromium 等浏览器读取 cookies，过滤后只保存 YouTube/Google 相关 cookies 到本地 `data/cookies.txt`。单视频和 playlist 解析都使用同一套 cookies；当解析阶段遇到 bot 校验且当前 cookies 不可用时，后端会自动尝试导入并重试一次。若 playlist 解析成功但后台下载某个子视频时才遇到 bot 校验，下载任务也会自动刷新浏览器 cookies 并重试该子视频一次。

Windows 上 Edge 正在运行时可能会锁定 `Default\Network\Cookies` 数据库，导致 yt-dlp 报 `Could not copy Chrome cookie database`。应用会把这种情况识别为 Edge 锁库，并在解析区显示“关闭 Edge 并导入”按钮；只有用户确认后，应用才会关闭所有 Edge 窗口、重新导入 cookies，并在 playlist 解析失败场景中自动重试刚才的解析。若 Edge 关闭后 yt-dlp 仍遇到 DPAPI 解密限制，应用会启动临时 headless Edge DevTools 会话，让 Edge 自己读取 YouTube/Google cookies 后再保存为 `cookies.txt`。

也可以手动从浏览器导出 Netscape 格式的 `cookies.txt` 后上传。文件会保存到本地 `data/cookies.txt`，该目录已在 `.gitignore` 中排除。

清除 cookies 后，后续分析和下载会回到无登录态模式。为降低连续请求触发 YouTube 风控的概率，后端会给 yt-dlp 设置轻量请求间隔和下载前随机等待；这会让 playlist 下载启动稍慢一些，但比连续失败并反复请求更稳。

## 配置

默认目录：

- 数据库和 cookies：`data/`
- 下载产物：`downloads/`
- Playlist 下载产物：`downloads/<playlist 名称>/`

可通过环境变量覆盖部分配置，前缀为 `YTDL_`：

```powershell
$env:YTDL_DOWNLOAD_DIR="D:\Videos\YouTube"
$env:YTDL_DEFAULT_CONCURRENCY="8"
$env:YTDL_DEFAULT_RESOLUTION="1080p"
```

前端设置面板支持通过系统文件夹对话框选择下载根目录，并支持修改并发数；并发标签直接标注“默认跟随 CPU Core 数量，可按需调整”，修改后会自动保存并立即调整后台 worker 数，无需重启服务。单次下载的清晰度由“下载选项”里的“清晰度 / 格式”决定。未保存自定义值时，并发数默认使用本机逻辑 CPU core 数量，最低为 1，不额外设置上限。Playlist 任务会在下载根目录下自动创建同名子文件夹；如果选择的下载根目录为 `dir`，playlist 名称为 `list`，则保存到 `dir/list/`；单视频任务仍直接使用下载根目录。

## API 摘要

- `POST /api/analyze`：解析视频或 playlist。
- `POST /api/jobs`：创建下载任务。
- `GET /api/jobs`、`GET /api/jobs/{id}`：读取任务列表和详情。
- `POST /api/jobs/{id}/pause`：暂停任务。
- `POST /api/jobs/{id}/restart`：重启任务；可选请求体 `{ "resolution": "720p" }` 用指定清晰度重启。
- `POST /api/jobs/{id}/items/{item_id}/restart`：重启 playlist 中的单个子视频；可选请求体 `{ "resolution": "720p" }` 只覆盖该子视频。
- `DELETE /api/jobs/{id}`：删除任务；可加 `?delete_files=true` 同步删除该任务已下载文件。
- `POST /api/jobs/batch`：批量暂停、重启或删除任务；删除时可在请求体中传 `delete_files: true`。
- `POST /api/jobs/{id}/cancel`：保留的取消接口。
- `GET /api/events`：SSE 任务事件流。
- `GET /api/settings`、`PUT /api/settings`：读取/更新设置。
- `POST /api/settings/download-dir/select`：打开本机文件夹选择对话框并保存下载根目录。
- `POST /api/cookies`、`DELETE /api/cookies`：上传/清除 cookies。
- `POST /api/cookies/from-browser`：通过 yt-dlp 从本机浏览器导入 YouTube/Google cookies；请求体可传 `{ "browser": "auto" }` 或指定 `edge`、`chrome`、`firefox` 等浏览器；当 Edge 锁库时，可在用户确认后传 `{ "browser": "edge", "close_browser_if_locked": true }` 关闭 Edge 并重试导入。
- `GET /api/diagnostics`：依赖和运行状态诊断。

## 测试

默认自动测试采用分层策略：后端使用单元测试和 mock 覆盖 yt-dlp 映射、任务状态、API 行为、Edge 锁库和 DPAPI fallback，前端使用组件测试覆盖解析、字幕选择、任务中心、cookies 锁库提示和格式元信息展示；真实 YouTube 下载作为可选手动验收，不纳入默认测试命令。

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

手动验收建议：解析一个公开视频，确认具体格式下拉同时显示分辨率与大小；解析一个小 playlist，确认条目选择、任务中心控制、开始/结束时间、实际分辨率和进度显示正常；可选再执行一次字幕单独下载。清晰度回归验收使用 `https://youtu.be/NReDubvNjRg?si=glPVWHBZaB91s36W` 分别下载 1440p、1080p、360p，并用 `ffmpeg -i` 或系统文件属性确认输出高度分别为 1440、1080、360。

## 常见问题

- 分析成功但下载失败：先检查 `GET /api/diagnostics` 或顶部状态，确认 `yt-dlp`、JS runtime、`ffmpeg` 和 cookies 是否可用；`ffprobe` 为可选诊断项，不影响常规下载。
- 高分辨率不可用：YouTube 常把视频和音频分离，缺少可用 `ffmpeg` 时无法合并。系统会优先使用内置 `imageio-ffmpeg`；如果仍不可用或所选高度不存在，任务会失败并显示原因，不会降级成 360p。若视频没有所选高度，任务中心会显示低于所选高度的最高可用分辨率，并提供“以 xx 重启”按钮。
- 需要登录或遇到 bot 校验的视频失败：先在“解析链接”面板点击“从浏览器导入”。如果 Edge 正在运行导致 cookie 数据库被锁定，界面会提示确认关闭 Edge 后再导入；后台下载任务不会擅自关闭浏览器，遇到锁库时会提示你回到解析面板确认导入后再重启任务。如果 yt-dlp 直接解密 Edge cookies 失败，应用会尝试临时 headless Edge DevTools fallback；如果浏览器未登录 YouTube、系统密钥链不可用、YouTube 已轮换或拒绝当前登录 cookies，或自动导入仍失败，再改用手动上传有效的 `cookies.txt`。
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

- 默认在 `main` 分支开发；只有在明确要求其他分支时才切换到特性分支。
- 下载产物、cookies、SQLite 数据库、日志和依赖目录不进入 Git。
- 后端 API 不接收任意 yt-dlp 参数，只暴露项目定义的安全选项。
- 以后每次修改功能、命令、依赖或运行方式，都同步更新本 README。
