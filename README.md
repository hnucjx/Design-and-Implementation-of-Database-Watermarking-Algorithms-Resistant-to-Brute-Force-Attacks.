# YouTube Downloader

YouTube Downloader 是一个本机单用户下载控制台：FastAPI 后端负责调用 `yt-dlp`、维护 SQLite 任务状态和推送下载进度，React/Vite 前端提供链接解析、下载选项和任务中心。

请只下载你拥有权利或已获得许可的内容。本项目不实现 DRM 绕过，也不面向公网部署或多用户权限场景。

## 快速启动

普通使用建议走单端口模式：先构建前端，再由 FastAPI 在同一个端口上同时托管页面和 API。

```powershell
cd frontend
npm install
npm run build
```

```powershell
cd ..\backend
python -m pip install -e .
python -m app
```

启动时命令会把实际监听地址打印出来，默认是 `http://127.0.0.1:8000`：

```text
服务地址：http://127.0.0.1:8000
```

打开该地址，应看到 YouTube Downloader 首页：左侧是“解析链接”和“任务中心”，右侧是“下载选项”。截图如下：

![YouTube Downloader 首页](docs/assets/screenshots/home.png)

### 端口不是固定的

端口只有一个来源：仓库根 `.env` 里的 `YTDL_API_PORT`（默认 `8000`，取值范围 `1..65535`）。后端读它，`frontend/vite.config.ts` 里前端开发服务器的 `/api` 代理读的也是它 —— 只有一处来源，才不会出现「后端换了端口、前端还代理旧端口」这种静默错配。

`python -m app` 的开关：

| 命令 | 效果 |
| --- | --- |
| `python -m app` | 用 `YTDL_API_PORT`（默认 `8000`）启动，监听 `127.0.0.1`。 |
| `python -m app --port 8010` | 本次临时换端口；写进 `.env` 才能让前端代理一起跟着变。 |
| `python -m app --auto-port` | 端口被占时自动往后找一个可用端口，并打印前端该设的值。 |
| `python -m app --reload` | 源码变更自动重启，用于前端热更新开发模式。 |

它还接受 `--host`，但**不要把监听地址放宽到 `0.0.0.0`**：应用没有认证，也从未按多用户或公网场景设计。

端口**不在设置面板里**（`GET/PUT /api/settings` 不含它），要改就用上面的方式。默认的 `8000` 在开发机上常被别的程序占用（例如 IncrediBuild 的 Coordinator 就长期监听它），此时 `python -m app` 不会只丢一句 `WinError 10013` —— 它会指名占用者并给出下一步，换端口与前端代理如何保持一致见
[开发文档：端口被占用时](docs/development.md#端口被占用时)，症状驱动的处置见 [排障手册](docs/troubleshooting.md#启动就失败端口被占用)。

`http://127.0.0.1:5173` 只用于前端热更新开发模式，需要另外启动 Vite dev server。不同读者的运行方式见 [用户手册](docs/user-manual.md#启动应用) 和 [开发文档](docs/development.md#本地运行)。

## 文档导航

| 文档 | 用途 |
| --- | --- |
| [文档总入口](docs/index.md) | 按读者角色选择阅读路径。 |
| [用户手册](docs/user-manual.md) | 启动入口、下载操作、代理与网络、cookies、自检与日志。 |
| [排障手册](docs/troubleshooting.md) | 按「你看到的那句话」查该点哪里：代理、cookies、JS 运行时与日志阅读。 |
| [需求分析](docs/requirements.md) | 项目目标、功能需求、非功能需求和边界。 |
| [架构设计](docs/architecture.md) | 前后端、SQLite、yt-dlp、ffmpeg、SSE 和外部依赖关系。 |
| [设计文档](docs/design.md) | 模块职责与依赖分层、运行时并发模型、关键数据流、扩展点和已知限制。 |
| [4+1 架构视图](docs/4-plus-1-view.md) | 逻辑、开发、进程、物理和场景视图，用于架构审查。 |
| [开发文档](docs/development.md) | 环境准备、依赖安装、配置项、目录结构和运行命令。 |
| [文档写作环境](docs/documentation-workflow.md) | 初始化文档工具、渲染 UML、检查本地链接和生成产物。 |
| [API 文档](docs/api.md) | HTTP endpoint、请求响应模型、任务状态和诊断字段；配套机器可读规范见 [openapi.yaml](docs/openapi.yaml)。 |
| [技术文档](docs/technical.md) | 清晰度/格式选择、降级策略、cookies、PO token 和稳定下载策略。 |
| [实现文档](docs/implementation.md) | 核心模块职责、任务调度、进度聚合、读模型和数据库补列。 |
| [测试文档](docs/testing.md) | 自动测试、手动验收和回归重点。 |
| [维护文档](docs/maintenance.md) | 文档同步规则、变更 checklist 和排障流程。 |
| [安全审计报告](docs/safety-review.md) | 安全审查结论、检查清单与待复核项。 |

## 测试摘要

验证命令集中维护在 [测试文档](docs/testing.md#自动测试命令)，README 不重复展开，避免后续命令分叉。

## 维护约定

功能、命令、配置、API、架构、4+1 视图、下载策略或测试方式变化时，必须同步更新 `docs/` 中的对应文档和 UML 图。README 只作为入口页，不承载详细设计内容。
