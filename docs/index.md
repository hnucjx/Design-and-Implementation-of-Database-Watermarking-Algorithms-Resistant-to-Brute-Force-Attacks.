# YouTube Downloader 文档中心

本目录是 YouTube Downloader 的长期维护文档入口。根目录 [README](../README.md) 只保留项目简介和导航；详细用户、架构、API、实现、测试和维护说明都在这里。

## 阅读路径

| 读者 | 建议阅读 |
| --- | --- |
| 普通用户 | 从 [用户手册](user-manual.md) 开始，再按需要查看 [技术文档](technical.md) 的排障说明。 |
| 后端开发者 | 先读 [架构设计](architecture.md)，再读 [API 文档](api.md) 和 [实现文档](implementation.md)。 |
| 前端开发者 | 先读 [用户手册](user-manual.md) 理解工作流，再读 [API 文档](api.md) 和 [开发文档](development.md)。 |
| 维护者 | 先读 [维护文档](maintenance.md)，再根据变更类型更新相关文档和图。 |
| 测试者 | 直接读 [测试文档](testing.md)，再对照 [需求分析](requirements.md) 验证范围。 |

## 文档职责

- [用户手册](user-manual.md)：面向最终使用者，说明启动入口、下载、cookies、任务中心和排障入口。
- [需求分析](requirements.md)：描述项目要解决的问题、功能需求、非功能需求和明确不支持的边界。
- [架构设计](architecture.md)：说明系统组成、模块边界、数据流和关键架构图。
- [开发文档](development.md)：说明本地开发环境、依赖、命令、目录、第三方工具和配置项。
- [API 文档](api.md)：记录后端 HTTP API、请求/响应模型、任务状态和错误语义。
- [技术文档](technical.md)：集中解释下载策略、分辨率、格式、cookies、PO token、aria2c 和稳定性策略。
- [实现文档](implementation.md)：面向维护者解释关键代码模块、类、函数和数据持久化方式。
- [测试文档](testing.md)：记录自动测试、手动验收和高风险回归场景。
- [维护文档](maintenance.md)：规定文档同步、变更审查和排障流程。

## UML 图

UML 源码位于 [diagrams](diagrams/)，渲染后的 SVG 位于 [assets/diagrams](assets/diagrams/)。

| 图 | 用途 |
| --- | --- |
| [系统上下文](diagrams/system-context.puml) | 项目与用户、浏览器、YouTube、yt-dlp、ffmpeg、SQLite 的关系。 |
| [组件关系](diagrams/component-overview.puml) | 前端和后端内部主要模块边界。 |
| [下载生命周期](diagrams/download-lifecycle.puml) | 任务和子视频的状态流转。 |
| [单视频时序](diagrams/single-video-sequence.puml) | 单视频解析、入队、下载和进度返回流程。 |
| [Playlist 时序](diagrams/playlist-sequence.puml) | Playlist 选择条目、逐项下载和聚合状态。 |
| [Cookies 流程](diagrams/cookies-flow.puml) | 手动上传、浏览器导入、Edge 锁库和 CDP fallback。 |
| [数据模型](diagrams/data-model.puml) | SQLite 表和 API 读模型之间的关系。 |

## 更新原则

文档以当前代码为准。修改 [backend/app](../backend/app/) 或 [frontend/src](../frontend/src/) 中的行为时，请在同一变更中更新对应文档；具体 checklist 见 [维护文档](maintenance.md)。
