# YouTube Downloader 工程文档撰写 Prompt

你是一名资深软件工程技术写作者、系统分析师和文档架构师。请基于当前仓库真实代码、`README.md`、`ai/docs.md`、后端 API schema、前端类型定义、测试用例和项目配置，为 YouTube Downloader 项目创建一套可长期维护的软件工程标准文档。

## 总目标

在不修改任何后端或前端源码的前提下，建立 `docs/` 下的专业文档体系，并将根目录 `README.md` 收敛为简短入口页。文档应能帮助三类读者快速获得所需信息：

- 普通用户：如何安装、运行、配置、使用和排障。
- 开发者：如何理解架构、代码边界、API、数据模型、测试和维护约定。
- 后续维护者/审查者：如何判断文档是否与代码一致，如何在功能变化时同步更新文档。

## 必须遵守的约束

- 不修改 `backend/app`、`frontend/src` 或其他产品源码；本次只允许修改文档、README、`ai/` 任务文件和 UML 产物。
- 所有文档内容必须来自当前仓库事实，不臆测不存在的功能、接口、配置或部署方式。
- 根 `README.md` 只保留项目简介、快速启动和文档导航；详细内容移动到 `docs/`，避免 README 与详细文档重复。
- 各文档之间不得复制大段相同功能说明；需要复用时使用本地相对链接交叉引用。
- 对源文件、文档、代码行、类名、类型、接口和命令的引用必须尽量本地可跳转。Markdown 中引用仓库文件时使用相对路径和行锚，例如 `../backend/app/main.py#L98`。
- 文档语气专业、准确、可维护；优先使用清晰的小节、表格和短列表，不写营销化或空泛内容。
- 涉及 YouTube、cookies、PO token、下载稳定性、版权或权限限制时，要保持合规和边界清晰：不承诺绕过 DRM、会员、地区、年龄、私有视频等权限限制。

## 输入来源

请至少检查并引用下列信息源：

- `ai/docs.md`：本次文档任务源。
- `README.md`：现有项目说明，作为迁移和去重来源。
- `backend/pyproject.toml`、`frontend/package.json`：运行环境、依赖和测试命令。
- `backend/app/main.py`、`backend/app/schemas.py`、`backend/app/models.py`、`backend/app/job_manager.py`、`backend/app/ytdlp_service.py`：API、数据模型、下载流程和核心后端行为。
- `frontend/src/types.ts`、`frontend/src/api.ts`、`frontend/src/App.tsx`、`frontend/src/components/JobQueue.tsx`：前端类型、API 调用和主要 UI 工作流。
- `backend/tests`、`frontend/src/*.test.tsx`、`frontend/src/test`：测试策略和覆盖范围。

## 文档交付物

在 `docs/` 下创建以下文档。每个文档必须有清晰标题、适用读者、相关文档链接和必要的源码引用。

- `docs/index.md`：文档总入口，说明每份文档的用途和阅读路径。
- `docs/user-manual.md`：用户手册，覆盖安装、运行、解析链接、选择清晰度/字幕、任务中心、cookies、下载目录、常见排障入口。
- `docs/requirements.md`：需求分析文档，覆盖目标用户、功能需求、非功能需求、约束、边界和不支持事项。
- `docs/architecture.md`：架构设计文档，覆盖前端、后端、SQLite、yt-dlp、ffmpeg、cookies、SSE、下载目录和关键数据流。
- `docs/development.md`：开发文档，覆盖环境准备、依赖安装、运行命令、配置项、目录结构、第三方工具和框架。
- `docs/api.md`：API 文档，覆盖 HTTP endpoint、请求/响应模型、错误语义、诊断字段和任务状态。
- `docs/technical.md`：技术文档，覆盖下载策略、清晰度/格式自动选择、分辨率降级原因、下载稳定性、cookies 导入、PO token provider、aria2c 可选 fallback。
- `docs/implementation.md`：实现文档，覆盖核心模块职责、关键类/函数、任务调度、进度聚合、读模型、数据库补列、日志安全。
- `docs/testing.md`：测试文档，覆盖后端/前端测试命令、测试层次、mock 策略、手动验收和回归重点。
- `docs/maintenance.md`：维护文档，覆盖文档同步规则、变更 checklist、排障流程、配置变更记录方式和持续维护约定。

## UML 与图片交付物

使用 PlantUML 创建源码文件，并生成对应 SVG 图片。PlantUML 源文件放在 `docs/diagrams/`，SVG 放在 `docs/assets/diagrams/`，两者都必须纳入 Git。

至少生成以下图：

- `system-context.puml` / `system-context.svg`：系统上下文或容器级视图。
- `component-overview.puml` / `component-overview.svg`：主要后端/前端组件关系。
- `download-lifecycle.puml` / `download-lifecycle.svg`：下载任务生命周期状态图或活动图。
- `single-video-sequence.puml` / `single-video-sequence.svg`：单视频解析与下载时序图。
- `playlist-sequence.puml` / `playlist-sequence.svg`：playlist 创建任务、逐项下载和进度更新时序图。
- `cookies-flow.puml` / `cookies-flow.svg`：浏览器 cookies 导入与使用流程。
- `data-model.puml` / `data-model.svg`：核心数据模型关系图。

在相关 Markdown 文档中嵌入 SVG 图片，并在图片附近链接对应 `.puml` 源文件。

## README 收敛要求

重写根目录 `README.md`，只保留：

- 项目一句话定位和合规提醒。
- 最短可用的快速启动命令。
- 文档导航表，链接到 `docs/index.md` 和各关键文档。
- 测试命令摘要。
- 维护提示：功能、命令、配置、API 或架构变化时必须同步更新 `docs/`。

详细 API、cookies、排障、配置解释、架构和实现说明不得继续堆在 README 中，应通过链接指向 `docs/`。

## 质量标准

- 文档应与当前代码一致，特别是 API endpoint、schema 字段、环境变量、默认值、任务状态、下载策略和测试命令。
- 每份文档只承担一个主要职责；如果内容属于另一份文档，应链接过去。
- 每个 UML 图都应服务于某份文档中的具体说明，不生成装饰性图片。
- 所有新增 Markdown、PlantUML 和 README 内容通过 `git diff --check`。
- 运行并通过：
  - `python -m compileall backend\app`
  - `python -m pytest backend\tests -q`
  - `cd frontend && npm test`
  - `cd frontend && npm run build`
- 完成后确认 `git status -sb` 只包含本次文档相关变更，提交并 `git push origin main`。

## 输出要求

请直接修改仓库文件，不要只给建议。完成后在最终回复中简要说明：

- 已创建/更新的主要文档。
- PlantUML 图片是否成功生成。
- 已运行的验证命令及结果。
- 两次提交和 push 的结果。
