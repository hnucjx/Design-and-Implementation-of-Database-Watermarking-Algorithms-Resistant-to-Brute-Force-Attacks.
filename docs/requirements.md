# 需求分析

适用读者：产品维护者、测试者和需要判断变更是否符合项目边界的开发者。

## 目标

本项目提供一个本机单用户 YouTube 下载控制台，使用户能够解析公开视频或 playlist，选择清晰度和字幕选项，将下载任务加入队列，并在任务中心观察状态、失败原因和最终输出信息。

系统范围由 [FastAPI 路由](../backend/app/main.py#L98)、[API schema](../backend/app/schemas.py#L14) 和 [前端类型](../frontend/src/types.ts#L1) 共同定义。

## 用户角色

| 角色 | 目标 |
| --- | --- |
| 普通用户 | 在本机下载有权访问的视频或 playlist，处理 cookies、清晰度和字幕选项。 |
| 维护者 | 更新下载策略、API、任务状态和排障文档，保持文档与代码同步。 |
| 测试者 | 验证解析、下载队列、任务中心、cookies 导入、清晰度降级和错误显示。 |

## 功能需求

| 编号 | 需求 | 代码依据 |
| --- | --- | --- |
| FR-1 | 支持单视频和 playlist 元数据解析。 | [`POST /api/analyze`](../backend/app/main.py#L117)、[`AnalyzeResponse`](../backend/app/schemas.py#L43) |
| FR-2 | 支持选择 playlist 子项并创建下载任务。 | [`_selected_entries`](../backend/app/main.py#L352)、[`CreateJobRequest`](../backend/app/schemas.py#L72) |
| FR-3 | 支持视频+字幕、仅视频、仅字幕三种模式。 | [`DownloadMode`](../backend/app/schemas.py#L7) |
| FR-4 | 用户只选择清晰度，默认 `1440p`；后端自动选择具体格式。 | [`format_selector`](../backend/app/ytdlp_formats.py#L9) |
| FR-5 | 下载前预检测计划分辨率、格式和视频大小，任务中心展示实际值。 | [`prepare_download`](../backend/app/ytdlp_service.py#L198)、[`_apply_download_preparation`](../backend/app/job_manager.py#L682) |
| FR-6 | 支持明确的分辨率降级原因和重启建议。 | [`fallback_policy.py`](../backend/app/fallback_policy.py#L4) |
| FR-7 | 支持任务暂停、重启、删除、playlist 子视频删除、批量操作，以及本地播放/打开文件夹。 | [`batch_job_action`](../backend/app/main.py#L170)、[`system_open.py`](../backend/app/system_open.py) |
| FR-8 | 默认请求人工字幕和自动字幕；缺少某类字幕时 fallback 到另一类可用字幕，并显示来源与格式。 | [`DownloadOptions`](../backend/app/schemas.py#L56)、[`DownloadOptionsPanel`](../frontend/src/App.tsx#L640) |
| FR-9 | 支持 cookies 上传、浏览器导入和清除。 | [`/api/cookies`](../backend/app/main.py#L293)、[`BrowserCookieImporter`](../backend/app/browser_cookies.py#L55) |
| FR-10 | 支持 SSE 事件流和任务轮询。 | [`/api/events`](../backend/app/main.py#L243)、[`EventBroker`](../backend/app/events.py#L7) |
| FR-11 | 支持诊断依赖状态。 | [`/api/diagnostics`](../backend/app/main.py#L102)、[`get_dependency_status`](../backend/app/ytdlp_service.py#L110) |

## 非功能需求

| 类别 | 要求 |
| --- | --- |
| 可维护性 | 下载策略、降级原因、读模型、cookies 导入和格式选择拆分到独立模块，详见 [实现文档](implementation.md)。 |
| 稳定性 | 支持并发设为 1 的稳定优先运行方式，并保留断点续传、小 HTTP chunk、低速重取 URL、同清晰度多 profile 重试。 |
| 可观测性 | 任务中心显示进度、速度、视频大小、ETA、实际分辨率、实际格式和错误原因；诊断接口返回依赖状态。 |
| 安全性 | 不在 UI 或日志回显 cookies、token、敏感 URL query；日志清洗见 [log_safety.py](../backend/app/log_safety.py#L11)。 |
| 本地化 | 当前 UI 和主要错误信息面向中文用户。 |
| 可测试性 | 后端使用 pytest，前端使用 Vitest/jsdom，默认不依赖真实 YouTube 下载。 |

## 约束与边界

- 本项目是本机工具，不是公网多用户服务。
- 不绕过 DRM、会员、地区、年龄、私有视频等权限限制。
- 不允许用户传入任意 yt-dlp 参数；API 只暴露项目定义的安全选项。
- 下载稳定性策略不能保证所有 YouTube 媒体流可下载，尤其是账号态、地区、会员或 PO token 强制场景。
- `aria2c` 是显式启用的可选 fallback，不是默认依赖；配置详见 [开发文档](development.md#环境变量)。

## 验收口径

功能变更必须同时满足：

- 对应自动测试通过，命令见 [测试文档](testing.md#自动测试命令)。
- API、配置、下载策略或任务字段变化已同步更新 [API 文档](api.md)、[技术文档](technical.md) 和相关 UML 图。
- README 仍保持入口页定位，详细说明不回流到 README。
