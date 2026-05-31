# 安全审计报告

适用读者：维护者、架构审查者。本文档记录对当前代码基线（`main` 分支 f2e3fe6 及之前）的全面安全审查结果。

## 审查范围

- 后端：`backend/app/` 下全部 Python 源码（FastAPI、SQLModel、yt-dlp 封装、cookies 导入、文件操作、日志清洗、系统调用）
- 前端：`frontend/src/` 下全部 TypeScript/React 源码
- 审查日期：2026-05-31

## 审查结论摘要

本项目是本机单用户工具，绑定 `127.0.0.1`，不面向公网或多用户。在此前提下，**未发现高危安全漏洞**。识别的风险点均为低风险或信息性提示，主要与本地工具的固有设计假设相关。

---

## 1. 命令行注入（Command Injection）

| 评估 | 说明 |
| --- | --- |
| **未发现** | 所有外部命令调用均使用 list 参数形式（非 shell 字符串），无可控拼接。 |

详情：

- `subprocess.run(["ffmpeg", ...])` 使用固定参数列表（`system_open.py`、`ytdlp_service.py`）
- `subprocess.run(["taskkill", ...])` 使用已知固定命令（`browser_cookies.py`）
- `subprocess.Popen([edge, "--remote-debugging-port=...", ...])` 参数由常量或系统路径构成，非用户可控（`browser_cookies.py`）
- yt-dlp 通过 Python SDK（`yt_dlp.YoutubeDL`）调用，非 shell 或 subprocess

## 2. 路径遍历（Path Traversal）

| 评估 | 说明 |
| --- | --- |
| **未发现** | 文件删除操作受限至允许的根目录；用户不直接提供文件路径。 |

详情：

- 文件删除在 `_delete_output_files()`（`job_manager.py:338`）中执行，所有候选路径通过 `_is_under_allowed_root()` 验证，仅允许在下载根目录或任务下载子目录下操作
- 本地播放/打开文件夹接口（`main.py:246-271`）仅使用数据库记录的 `output_path` 和 `download_dir`，不接受前端传入任意路径
- `safe_path_name()`（`paths.py:7`）为 playlist 目录名移除 `<>:"/\|?*` 等不安全字符，并限制长度为 120 字符
- `discover_output_file_candidates()`（`output_paths.py:30`）仅在给定的 `job_download_dir` 内按 YouTube video ID 匹配文件，不遍历上级目录

## 3. SQL 注入（SQL Injection）

| 评估 | 说明 |
| --- | --- |
| **未发现** | 全部数据库操作使用 SQLModel ORM 参数化查询。 |

详情：

- 所有查询通过 `session.exec(select(...))` 执行，使用 SQLModel 的声明式查询
- `_ensure_columns()`（`db.py:21`）使用 f-string 构建 `ALTER TABLE` 语句，但表名和列名来自硬编码字典，非用户输入
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

- `GET /api/diagnostics`（`main.py:112`）仅返回 PO token / visitor data 的"是否已配置"布尔值，不返回原文（`ytdlp_service.py:131-132`）
- `sanitize_log_message()`（`log_safety.py:11`）在写入日志前通过正则替换移除 URL query string（包含 cookie、token、authorization 等参数）
- cookies 文件（`data/cookies.txt`）和 `.env` 文件均在 `.gitignore` 中排除，不会进入 Git
- 浏览器 cookie 导入只提取 YouTube/Google 域名（`YOUTUBE_COOKIE_DOMAIN_SUFFIXES`，`browser_cookies.py:20`）

## 8. Cookie 安全

| 评估 | 说明 |
| --- | --- |
| **可接受** | Cookie 导入流程安全，有过滤和访问控制。 |

详情：

- 浏览器 cookie 导入使用 `BrowserCookieImporter`（`browser_cookies.py:55`），支持 Edge/Chrome/Firefox/Brave/Chromium 等
- 导入的 cookies 过滤为仅 youtube.com 和 google.com 域名
- Edge cookies 数据库被锁时，提供提示并支持关闭浏览器重试
- CDP fallback（`_extract_edge_cookies_via_cdp`，`browser_cookies.py:157`）使用临时 headless Edge 实例，完成后立即终止进程
- Cookie 导入由 `threading.Lock`（`job_manager.py:48`）保护，防止并发导入导致的资源竞争

**注意**：CDP fallback 启动 Edge 时使用了 `--remote-allow-origins=*`。虽然使用了随机空闲端口且进程在获取 cookies 后立即终止（超时 15 秒 + 10 秒），且仅绑定 `127.0.0.1`，但在 Headless Edge 短暂运行期间，同机的其他本地进程理论上可以连接到调试端口。攻击面极小（需要本机已存在恶意进程），且 YouTube cookies 在 Edge 中通常已在登录态下。

## 9. 文件系统操作安全

| 评估 | 说明 |
| --- | --- |
| **良好** | 文件删除/播放均受限至预期目录。 |

详情：

- 文件删除通过 `_is_under_allowed_root()`（`job_manager.py:360`）验证目标路径位于下载根目录或任务子目录内
- 删除 playlist 子文件夹前检查文件夹确实在下载根目录下（`job_manager.py:357`）
- 本地文件打开（`system_open.py:23`）不涉及路径操作安全风险——仅打开已存在的文件
- 输出路径解析（`output_paths.py`）仅在给定下载目录内进行，不访问外围文件系统

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
- `SettingsUpdate` 中的并发、重试等数值均有 Pydantic 验证约束
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

- Cookie 导入有 `threading.Lock` 保护（`job_manager.py:48`）
- 任务状态由 `_cancelled`、`_paused`、`_deleted`、`_runtime_restart_items` 等内存集合协调 worker 行为
- `should_cancel` 回调在下载过程中定期检查，响应暂停/取消/删除请求
- 数据库写入使用 SQLAlchemy session + commit，提供事务保护
- SQLite `check_same_thread=False` 允许多线程访问，在多 FastAPI worker 场景下可能产生写入冲突。当前代码通过独立 session 和 commit 缓解了此问题

## 14. 网络安全

| 评估 | 说明 |
| --- | --- |
| **可接受** | 本地绑定无外部暴露。 |

详情：

- 应用绑定 `127.0.0.1:8000`，不暴露于网络
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
5. **（信息性）SQLite WAL 模式**：考虑启用 SQLite WAL 模式以在高并发写入场景下减少锁竞争。
