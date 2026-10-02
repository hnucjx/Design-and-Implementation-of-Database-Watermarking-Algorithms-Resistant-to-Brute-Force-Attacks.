# YouTube Downloader 文档中心

本目录是 YouTube Downloader 的长期维护文档入口。根目录 [README](../README.md) 只保留项目简介和导航；详细用户、架构、API、实现、测试和维护说明都在这里。

## 阅读路径

| 读者 | 建议阅读 |
| --- | --- |
| 普通用户 | 从 [用户手册](user-manual.md) 开始；遇到具体报错直接查 [排障手册](troubleshooting.md)。 |
| 后端开发者 | 先读 [架构设计](architecture.md)，再读 [API 文档](api.md)（需要导入工具链时直接取 [openapi.yaml](openapi.yaml)）和 [实现文档](implementation.md)。 |
| 前端开发者 | 先读 [用户手册](user-manual.md) 理解工作流，再读 [API 文档](api.md) 和 [开发文档](development.md)。 |
| 架构评审者 | 先读 [4+1 架构视图](4-plus-1-view.md)，再进入 [架构设计](architecture.md) 和 [设计文档](design.md)。 |
| 安全评审者 | 直接读 [安全审计报告](safety-review.md)，再对照 [需求分析](requirements.md#约束与边界) 的范围边界。 |
| 文档维护者 | 先初始化 [文档写作与生成环境](documentation-workflow.md)，再按 [维护文档](maintenance.md) 更新文档和图。 |
| 维护者 | 先读 [维护文档](maintenance.md)，再根据变更类型更新相关文档、4+1 视图和图。 |
| 测试者 | 直接读 [测试文档](testing.md)，再对照 [需求分析](requirements.md) 验证范围。 |

## 文档职责

- [用户手册](user-manual.md)：面向最终使用者，说明启动入口、下载、代理与网络、cookies、任务中心、辅助说明的查看方式、自检与日志。
- [排障手册](troubleshooting.md)：面向「应用用不起来的人」，按症状（原始报错原文）查该点哪里，含代理、cookies、JS 运行时三篇、端口占用与「页面没变化」，以及日志阅读方法。
- [需求分析](requirements.md)：描述项目要解决的问题、功能需求、非功能需求和明确不支持的边界。
- [架构设计](architecture.md)：说明系统组成、模块边界、数据流和关键架构图。
- [设计文档](design.md)：模块职责矩阵、依赖方向与分层规则、运行时并发模型、关键数据流、状态机与持久化设计取舍、扩展点与已知限制。
- [4+1 架构视图](4-plus-1-view.md)：按逻辑、开发、进程、物理和场景视图组织架构审查入口。
- [开发文档](development.md)：说明本地开发环境、依赖、命令、目录、第三方工具和配置项。
- [文档写作与生成环境](documentation-workflow.md)：说明仓库内文档工具的初始化、UML 渲染、一致性检查和推荐写作流程。
- [API 文档](api.md)：记录后端 HTTP API、请求/响应模型、任务状态和错误语义；配套的机器可读规范见 [openapi.yaml](openapi.yaml)（OpenAPI 3.1.0，覆盖全部 28 个操作）。
- [技术文档](technical.md)：集中解释下载策略、分辨率、格式、cookies、PO token、aria2c 和稳定性策略。
- [实现文档](implementation.md)：面向维护者解释关键代码模块、类、函数和数据持久化方式。
- [测试文档](testing.md)：记录自动测试、手动验收和高风险回归场景。
- [维护文档](maintenance.md)：规定文档同步、变更审查和排障流程。
- [安全审计报告](safety-review.md)：按风险类别记录当前基线的安全审查结论、检查清单与建议。

## 界面截图

截图位于 [assets/screenshots](assets/screenshots/)，由真实浏览器实拍后随文档提交：首页 `home.png`，以及辅助说明的三种状态 `help-collapsed.png`（默认收起）、`help-open-desktop.png`（桌面端浮层）、`help-open-mobile.png`（窄屏底部抽屉）。界面改版后必须重拍替换，且不得包含本机用户名路径等个人信息。

界面缺陷（能跑、不报错，但呈现错位或不一致）按 [ai/ui](../ai/ui/README.md) 的约定一条一记录，
其中需要给出可测量的像素差；修复后同样要重拍上面这些截图，否则文档会停留在旧版界面。

## UML 图

UML 源码位于 [diagrams](diagrams/)，渲染后的 SVG 位于 [assets/diagrams](assets/diagrams/)。

| 图 | 用途 |
| --- | --- |
| [系统上下文](diagrams/system-context.puml) | 项目与用户、浏览器、YouTube、yt-dlp、ffmpeg、SQLite 的关系。 |
| [组件关系](diagrams/component-overview.puml) | 前端和后端内部主要模块边界。 |
| [模块依赖](diagrams/module-dependencies.puml) | 后端分层、依赖方向与分层规则。 |
| [下载数据流](diagrams/download-data-flow.puml) | 请求 → 队列 → yt-dlp → 进度聚合 → 落库 → SSE → 读模型。 |
| [运行时并发](diagrams/runtime-concurrency.puml) | 事件循环、worker 任务、下载线程、锁与 SQLite session 的关系。 |
| [4+1 逻辑视图](diagrams/four-plus-one-logical-view.puml) | 领域对象、服务职责和核心策略关系。 |
| [4+1 开发视图](diagrams/four-plus-one-development-view.puml) | 源码模块、包结构和测试边界。 |
| [4+1 进程视图](diagrams/four-plus-one-process-view.puml) | 运行时并发、队列、事件和设置变更流程。 |
| [4+1 物理视图](diagrams/four-plus-one-physical-view.puml) | 本机部署节点、进程、文件和外部工具依赖。 |
| [4+1 场景视图](diagrams/four-plus-one-scenario-view.puml) | 关键用户用例与架构视图的串联。 |
| [下载生命周期](diagrams/download-lifecycle.puml) | 任务和子视频的状态流转。 |
| [单视频时序](diagrams/single-video-sequence.puml) | 单视频解析、入队、下载和进度返回流程。 |
| [Playlist 时序](diagrams/playlist-sequence.puml) | Playlist 选择条目、按并发并行下载子视频和聚合状态。 |
| [Cookies 流程](diagrams/cookies-flow.puml) | 手动上传、浏览器导入、Edge 锁库和 CDP fallback。 |
| [数据模型](diagrams/data-model.puml) | SQLite 表和 API 读模型之间的关系。 |

## 更新原则

文档以当前代码为准。修改 [backend/app](../backend/app/) 或 [frontend/src](../frontend/src/) 中的行为时，请在同一变更中更新对应文档；具体 checklist 见 [维护文档](maintenance.md)。
