# 安全审计报告

适用读者：维护者、架构审查者。本文档记录对代码基线（`main` 分支 `f2e3fe6` 及之前）的全面安全审查结果。

> 行号锚点已于 2026-10-01 按 `d367de0` 及之后的代码重新校准。审查结论本身仍是 `f2e3fe6` 基线的结论；`f2e3fe6` 之后新增的代码不在本次审查范围内，已在第 17 节列出并标记为待复核。

## 审查范围

- 后端：`backend/app/` 下全部 Python 源码（FastAPI、SQLModel、yt-dlp 封装、cookies 导入、文件操作、日志清洗、系统调用）
- 前端：`frontend/src/` 下全部 TypeScript/React 源码
- 审查日期：2026-05-31（行号锚点刷新：2026-10-01）

## 审查结论摘要

本项目是本机单用户工具，绑定 `127.0.0.1`，不面向公网或多用户。在此前提下，**未发现高危安全漏洞**。识别的风险点均为低风险或信息性提示，主要与本地工具的固有设计假设相关。

---

## 1. 命令行注入（Command Injection）

| 评估 | 说明 |
| --- | --- |
| **未发现** | 所有外部命令调用均使用 list 参数形式（非 shell 字符串），无可控拼接。 |

详情：

- `subprocess.run([ffmpeg, ...])` 使用固定参数列表（[system_open.py](../backend/app/system_open.py#L67)、[ytdlp_service.py](../backend/app/ytdlp_service.py#L421)）
- `subprocess.run([taskkill, ...])` 使用已知固定命令（[browser_cookies.py](../backend/app/browser_cookies.py#L141)）
- `subprocess.Popen([edge, --remote-debugging-port=..., ...])` 参数由常量或系统路径构成，非用户可控（[browser_cookies.py](../backend/app/browser_cookies.py#L157)）
- `subprocess.run(["netstat", ...])` / `(["tasklist", "/FI", f"PID eq {pid}", ...])` 用于识别端口占用者：命令名与参数都是常量，`pid` 由 [parse_listening_pids](../backend/app/dev_server.py#L71) 用 `int()` 从 `netstat` 输出解析、解析失败即跳过，因此无法注入（[dev_server.py](../backend/app/dev_server.py#L119)）
- yt-dlp 通过 Python SDK（`yt_dlp.YoutubeDL`）调用，非 shell 或 subprocess

## 2. 路径遍历（Path Traversal）

| 评估 | 说明 |
| --- | --- |
| **未发现** | 文件删除操作受限至允许的根目录；用户不直接提供文件路径。 |

详情：

- 文件删除在 [_delete_output_files](../backend/app/job_manager.py#L350) 中执行，所有候选路径通过 [_is_under_allowed_root](../backend/app/job_manager.py#L372) 验证，仅允许在下载根目录或任务下载子目录下操作
- 本地播放/打开文件夹接口（[routers/job_files.py](../backend/app/routers/job_files.py#L16) 起）仅使用数据库记录的 `output_path` 和 `download_dir`，不接受前端传入任意路径
- [safe_path_name](../backend/app/paths.py#L7) 为 playlist 目录名移除 `<>:"/\|?*` 等不安全字符，并限制长度为 120 字符
- [discover_output_file_candidates](../backend/app/output_paths.py#L47) 仅在给定的 `job_download_dir` 内按 YouTube video ID 匹配文件，不遍历上级目录

## 3. SQL 注入（SQL Injection）

| 评估 | 说明 |
| --- | --- |
| **未发现** | 全部数据库操作使用 SQLModel ORM 参数化查询。 |

详情：

- 所有查询通过 `session.exec(select(...))` 执行，使用 SQLModel 的声明式查询
- [_ensure_columns](../backend/app/db.py#L43) 使用 f-string 构建 `ALTER TABLE` 语句，但表名和列名来自硬编码字典，非用户输入
- [checkpoint_wal](../backend/app/db.py#L72) 的 `PRAGMA wal_checkpoint(...)` 同样由常量与函数默认参数构成
- 无原始 SQL 拼接

## 4. 跨站脚本攻击（XSS）

| 评估 | 说明 |
| --- | --- |
| **低风险** | React 默认转义 HTML 输出；无 `dangerouslySetInnerHTML` 使用。 |

详情：

- 前端使用 React + JSX 渲染，React 自动对所有插入内容进行 HTML 转义
- 任务标题、错误消息来自 yt-dlp（YouTube 元数据），通过 HTTP API 的 JSON 传递，React 渲染时自动转义
- 搜索结果中未发现 `dangerouslySetInnerHTML` 调用
- 未设置 Content-Security-Policy 头。对于本地单用户工具，此风险可接受
- 外部链接使用 `window.open(sourceUrl, "_blank", "noopener,noreferrer")`，防止 `opener` 反向引用攻击

## 5. 跨站请求伪造（CSRF）

| 评估 | 说明 |
| --- | --- |
| **不适用** | 无基于 cookie 的身份认证机制。 |

项目没有用户登录、会话 cookie 或 token 认证。API 仅绑定 `127.0.0.1`，浏览器同源策略已提供充分隔离。CORS 中间件限制了 `allow_origins` 为开发服务器地址（`127.0.0.1:5173`、`localhost:5173`），单端口模式下 CORS 基本不适用。

## 6. 身份认证与会话管理

| 评估 | 说明 |
| --- | --- |
| **不适用** | 无用户身份认证系统。 |

本工具设计为单用户本机使用，没有实现用户注册、登录或会话管理。系统安全依赖于操作系统层面的本地访问控制。

## 7. 敏感信息暴露

| 评估 | 说明 |
| --- | --- |
| **良好** | 敏感配置值不出现在诊断响应或日志中。 |

详情：

- [GET /api/diagnostics](../backend/app/routers/diagnostics.py#L29) 仅返回 PO token / visitor data 的"是否已配置"布尔值，不返回原文（[get_dependency_status](../backend/app/ytdlp_service.py#L287)）
- [sanitize_log_message](../backend/app/log_safety.py#L11) 在写入日志前通过正则替换移除 URL query string（包含 cookie、token、authorization 等参数）
- 代理 URL 在日志与 API 响应里都先经 [redact_proxy_credentials](../backend/app/proxy.py#L62) 脱敏（`user:pass@` → `***`）
- cookies 文件（`data/cookies.txt`）和 `.env` 文件均在 `.gitignore` 中排除，不会进入 Git
- 浏览器 cookie 导入只提取 YouTube/Google 域名（`YOUTUBE_COOKIE_DOMAIN_SUFFIXES`，[browser_cookies.py](../backend/app/browser_cookies.py#L20)）

新增的两处暴露面（均为本机、且是排障必需，见第 18 节的复核项）：

- `GET /api/diagnostics` 的 `log_file` 会返回日志文件的**本地绝对路径**。它不返回文件内容，作用只是让用户能找到证据。
- `GET /api/diagnostics` 的 `sanitized_environment` 会返回被摘除环境变量的**原值**（例如注入的 `NODE_OPTIONS=--require="C:\...\某补丁.cjs"`）。原值可能包含本地路径，可能暴露宿主工具链的位置，但不包含凭据。

## 8. Cookie 安全

| 评估 | 说明 |
| --- | --- |
| **可接受** | Cookie 导入流程安全，有过滤和访问控制。 |

详情：

- 浏览器 cookie 导入使用 [BrowserCookieImporter](../backend/app/browser_cookies.py#L67)，支持 Edge/Chrome/Firefox/Brave/Chromium 等
- 导入的 cookies 过滤为仅 youtube.com 和 google.com 域名
- Edge cookies 数据库被锁时，提供提示并支持关闭浏览器重试
- CDP fallback（[_extract_edge_cookies_via_cdp](../backend/app/browser_cookies.py#L176)）使用临时 headless Edge 实例，完成后立即终止进程
- Cookie 导入由 `threading.Lock`（[job_manager.py](../backend/app/job_manager.py#L57)）保护，防止并发导入导致的资源竞争

**注意**：CDP fallback 启动 Edge 时使用了 `--remote-allow-origins=*`。虽然使用了随机空闲端口且进程在获取 cookies 后立即终止（超时 15 秒 + 10 秒），且仅绑定 `127.0.0.1`，但在 Headless Edge 短暂运行期间，同机的其他本地进程理论上可以连接到调试端口。攻击面极小（需要本机已存在恶意进程），且 YouTube cookies 在 Edge 中通常已在登录态下。

## 9. 文件系统操作安全

| 评估 | 说明 |
| --- | --- |
| **良好** | 文件删除/播放均受限至预期目录。 |

详情：

- 文件删除通过 [_is_under_allowed_root](../backend/app/job_manager.py#L372) 验证目标路径位于下载根目录或任务子目录内
- 删除 playlist 子文件夹前检查文件夹确实在下载根目录下（[job_manager.py](../backend/app/job_manager.py#L369)）
- 本地文件打开（[system_open.py](../backend/app/system_open.py#L23)）不涉及路径操作安全风险——仅打开已存在的文件
- 输出路径解析（[output_paths.py](../backend/app/output_paths.py#L30)）仅在给定下载目录内进行，不访问外围文件系统

## 10. 依赖安全

| 评估 | 说明 |
| --- | --- |
| **信息** | 使用标准开源依赖，需定期更新。 |

关键依赖及注意事项：

- `yt-dlp`：核心下载引擎，频繁更新以应对 YouTube 变更。建议保持较新版本
- `yt-dlp[curl-cffi]`：提供 TLS 指纹伪装。curl_cffi 是底层 C 库的绑定，安全更新依赖上游
- `yt-dlp-getpot-wpc`：PO token provider，第三方插件。关注更新
- `fastapi` / `uvicorn` / `SQLModel` / `pydantic-settings`：成熟的开源 Web 框架
- `python-multipart`：文件上传解析。需关注安全公告
- `websockets`：用于 Edge CDP 通信，仅在 cookie 导入时使用
- `imageio-ffmpeg`：ffmpeg 后备二进制，作为 Python 包分发

## 11. 输入验证

| 评估 | 说明 |
| --- | --- |
| **可接受** | URL 验证委托给 yt-dlp；数值参数有 Pydantic 约束。 |

详情：

- `AnalyzeRequest.url` 和 `CreateJobRequest.url` 仅验证 `min_length=1`，不验证 URL 格式。实际 URL 处理由 yt-dlp 完成，无效 URL 会返回错误而非造成安全问题
- `DownloadOptions.speed_limit_kbps` 约束 `ge=1`
- `DownloadOptions.retries` 约束 `ge=0, le=20`
- `SettingsUpdate` 中的并发、重试、aria2c 连接数等数值均有 Pydantic 验证约束
- 批量操作 `job_ids` 和 `item_ids` 要求 `min_length=1`，在后端通过数据库查询验证存在性

## 12. 并发与资源耗尽

| 评估 | 说明 |
| --- | --- |
| **低风险** | 有并发控制，无外部访问。 |

详情：

- Worker 并发由 `default_concurrency` 控制（默认 5），可调至 1
- SSE 连接数理论上可被本机用户耗尽，但对本地单用户工具影响有限
- 下载大小受 YouTube 媒体流限制，非可控攻击向量
- API 无限流机制，但对本机使用场景合理

## 13. 竞态条件

| 评估 | 说明 |
| --- | --- |
| **低风险** | 有基础保护措施。 |

详情：

- Cookie 导入有 `threading.Lock` 保护（[job_manager.py](../backend/app/job_manager.py#L57)）
- 任务状态由 `_cancelled`、`_paused`、`_deleted`、`_runtime_restart_items` 等内存集合协调 worker 行为
- `should_cancel` 回调在下载过程中定期检查，响应暂停/取消/删除请求
- 数据库写入使用 SQLAlchemy session + commit，提供事务保护
- SQLite `check_same_thread=False` 允许多线程访问，在多 FastAPI worker 场景下可能产生写入冲突。当前代码通过独立 session 和 commit 缓解了此问题

## 14. 网络安全

| 评估 | 说明 |
| --- | --- |
| **可接受** | 本地绑定无外部暴露。 |

详情：

- 应用绑定 `127.0.0.1`，不暴露于网络。**端口是可配的，绑定地址不是**：端口来自 `YTDL_API_PORT`（默认 `8000`），可由 `.env` 或 `--port` 改动；而监听地址没有对应的配置项，只能靠 `python -m app --host`（或绕过入口直接跑 uvicorn 时的 `--host`）放宽 —— **不要使用**，应用无认证。安全性结论挂在绑定地址上，与端口号取何值无关。
- HTTP（非 HTTPS）用于本地通信，无敏感认证凭据在 HTTP 中传输
- 与 YouTube 的所有通信由 yt-dlp 处理，使用 HTTPS
- `/api/events`（SSE）未设认证，仅用于单向推送（无用户数据更新风险）

## 15. 安全检查清单

| 检查项 | 状态 |
| --- | --- |
| 命令行/SQL/XSS/路径遍历注入 | 未发现 |
| 敏感信息（cookies/token）泄露 | 已防护（诊断不返回原文，日志清洗，gitignore） |
| 任意文件系统访问 | 已防护（限制至允许的根目录） |
| Cookie 导入安全 | 可接受（域名过滤，并发锁，CDP 临时进程自动终止） |
| 依赖安全 | 需持续关注（定期更新 yt-dlp 及 yt-dlp-getpot-wpc） |
| 输入验证 | 可接受（URL 验证委托给 yt-dlp，数值参数有 Pydantic 约束） |
| 并发/竞态条件 | 可接受（有基础保护措施） |
| 跨域请求 | 不适用（本地单用户，无 cookie 认证） |
| 内容安全策略 | 低优先级（本地工具，React 默认转义 XSS 安全） |

## 16. 建议

以下建议为安全加固方向，优先级较低（不构成实际威胁）：

1. **（低优先级）CDP 端口安全**：考虑限制 Edge CDP 调试端口仅绑定 `127.0.0.1`（已默认），进一步减少临时调试端口可能被其他本地进程探测的窗口。
2. **（低优先级）Content-Security-Policy**：为前端 HTML 添加 CSP 头增强纵深防御。对本地单用户工具的边际效益有限。
3. **（常规维护）依赖更新**：定期更新 `yt-dlp` 和 `yt-dlp-getpot-wpc`，关注安全公告。
4. **（常规维护）版本固定**：`pyproject.toml` 中的依赖使用下限约束（`>=`），可考虑使用 `poetry.lock` 或 `pip freeze` 冻结精确版本以增强可复现性。
5. **（已实现）SQLite WAL 模式**：`create_app_engine` 在连接时启用 WAL、`busy_timeout=5000` 和 `synchronous=NORMAL`，减少多 worker 进度写入时的锁竞争。

## 17. 基线变更后的待复核项（2026-10-01）

以下代码在 `f2e3fe6` 之后引入，**尚未经过同等强度的安全审查**。列出它们是为了避免"报告覆盖范围"被误读为"覆盖了全部现有代码"：

| 变更 | 位置 | 初步判断 | 需要复核的点 |
| --- | --- | --- | --- |
| 停滞看门狗 | [stall_guard.py](../backend/app/stall_guard.py) | 新增模块只读 progress payload 并在超时后抛异常，不接触文件系统、网络或数据库；无用户输入。 | 超时抛错是否会与取消/暂停语义冲突，导致任务状态异常。 |
| WAL checkpoint | [db.py](../backend/app/db.py#L72) | `PRAGMA wal_checkpoint(TRUNCATE)` 由常量构成，失败只记日志。 | 与并发写入的交互、被强杀时的数据完整性。 |
| 进度落库节流 | [progress_persist.py](../backend/app/progress_persist.py) | 纯内存节流，无 I/O。 | 节流是否会导致终态进度丢失。 |
| 上传的 `.env` / 配置注入面 | [config.py](../backend/app/config.py#L19) | 新增 `YTDL_ARIA2C_PATH` 作为外部下载器可执行路径，会被交给 yt-dlp 作为 `external_downloader` 执行。**本机配置文件可控，非远端输入**，因此按本项目的威胁模型仍是低风险。 | 是否需要在文档中明确"该变量只应由本机使用者设置"；是否存在从 UI 可达的写入路径。 |
| SQLite 补列 | [db.py](../backend/app/db.py#L43) | 仍是硬编码列名字典 + f-string，未引入外部输入。 | 无新增风险。 |

复核方式：按第 1–15 节的分类逐项走查上述模块，通过后把结论并入对应章节，并把本文档顶部的基线改为复核时点的 commit。

## 18. 自检 / 日志相关新增（`d779b52` 之后，同样待复核）

为降低排障门槛新增的代码。它们的共同点是**主动发起网络请求**或**读取本地路径**，因此必须显式登记：

| 变更 | 位置 | 初步判断 | 需要复核的点 |
| --- | --- | --- | --- |
| 代理连通性自检 | [connectivity.py](../backend/app/connectivity.py#L73)、`POST /api/proxy/test` | 用 `urllib` 请求**固定的** `https://www.youtube.com/robots.txt`，URL 不可由请求体控制；代理地址来自本机设置或请求体，只作用于这一次请求，不写库、不落盘。响应回显的代理 URL 已脱敏。 | 请求体可控的代理地址等于「本机发起任意代理连接」的原语；本工具本来就允许配置代理，但需确认没有把它暴露成可被网页驱动（无认证 + `127.0.0.1` 绑定下，浏览器侧仍有 CSRF 面）。 |
| cookies 登录态校验 | [cookie_health.py](../backend/app/cookie_health.py#L234)、`POST /api/cookies/verify` | 只读 `data/cookies.txt`，用 `MozillaCookieJar` 发一次 `https://www.youtube.com/`。**不返回 cookie 内容**，只返回条数、域名分布、命中的 cookie **名字**与 `LOGGED_IN` 判定。 | 返回的 cookie 名字集合（如 `SID`/`HSID`）本身是低敏信息；确认没有路径能把文件内容带出来。`deep=false` 时完全不联网。 |
| 日志落盘 | [logging_setup.py](../backend/app/logging_setup.py#L48) | 写 `<data_dir>/logs/app.log`，UTF-8、2 MiB × 3 轮转。目录不可写时降级为「仅控制台」而不是启动失败。写入前统一过 [sanitize_log_message](../backend/app/log_safety.py#L11)。**已确认 `data/` 与 `*.log` 都在 `.gitignore` 内**，日志不会入库。 | 日志是**新增的持久化面**：确认所有进日志的字符串都过了清洗（尤其是来自 yt-dlp 的原始错误与代理 URL）。 |
| 宿主环境变量摘除 | [runtime_env.py](../backend/app/runtime_env.py#L58) | 只在自己的进程内 `os.environ.pop("NODE_OPTIONS")`，且**只在命中 `--require`/`--import`/`--loader`/`--experimental-loader` 时**；不写系统环境、不改注册表；动作记进日志与诊断。 | 这是一处「应用会修改自身环境」的行为，需要确认它不会被误用成静默隐藏宿主配置的手段（当前实现只针对会破坏 JS 运行时的强加载开关，且必定留痕）。 |
| 代理解析 | [proxy.py](../backend/app/proxy.py#L169) | 把「显式配置 / 系统代理 / 环境变量」归一成代理 URL，并**在回显前剥离凭据**（用户密码不会进界面和日志）。不发起请求。 | 解析结果会进日志（`proxy resolved ...`）与诊断响应；确认 `socks`/`http` 等 scheme 归一不会把凭据带出来，且 `direct` 语义不会被误解成「无代理」。 |
| 端口占用者识别 | [dev_server.py](../backend/app/dev_server.py#L111) | 调用 `netstat` / `tasklist` 两个只读命令（list 参数形式，见第 1 节），PID 经 `int()` 校验；命令缺失或超时一律退化成「不知道是谁」。 | 这是一处**新的子进程面**（2026-10-01 引入）：确认 `tasklist` 输出的进程名只用于展示、不会被当成路径或命令再消费；确认失败路径不会泄露本机环境细节。 |
| 错误翻译 | [error_advice.py](../backend/app/error_advice.py#L116) | 纯字符串判定：把异常链映射成「诊断 / 原因 / 建议」，不联网、不读库、不改变重试行为。 | 映射输出会直接展示给用户并进日志；确认原始异常在拼接前统一过 [log_safety](../backend/app/log_safety.py#L11) 清洗，不会把 URL query 或 token 带出来。 |
| 说明浮层 | [HelpPopover.tsx](../frontend/src/components/HelpPopover.tsx#L42) | 纯前端展示：面板内容由调用方以 children 传入，组件自身不拼接 HTML、不使用 `dangerouslySetInnerHTML`；钉住状态只往 `localStorage` 写一个布尔值。 | `localStorage` 键前缀 `cascade.help.open.v1.` 属本机非敏感状态；若将来把说明内容改成远端获取，本节需要重新评估（当前无远端内容）。 |
