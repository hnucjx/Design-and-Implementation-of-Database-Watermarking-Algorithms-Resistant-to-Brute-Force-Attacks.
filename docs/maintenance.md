# 维护文档

适用读者：负责长期维护代码、文档和发布质量的开发者。

## 文档同步原则

任何影响用户行为、API、配置、数据库字段、下载策略、错误提示、测试命令、运行方式或架构视图的变更，都必须同步更新 `docs/`。根 [README](../README.md) 只保留入口页，不承载详细设计。

## 变更 checklist

| 变更类型 | 必查文档 |
| --- | --- |
| 新增或修改 API endpoint/schema | [API 文档](api.md)、[openapi.yaml](openapi.yaml)、[架构设计](architecture.md)、相关 UML 图 |
| 修改下载策略、清晰度、格式、cookies、PO token、aria2c、停滞看门狗或节流守卫 | [技术文档](technical.md)、[用户手册](user-manual.md)、[测试文档](testing.md)、[PLAN.md](../PLAN.md)（若属于性能修复范围） |
| 修改任务状态、进度、速度或错误显示 | [API 文档](api.md)、[实现文档](implementation.md)、[用户手册](user-manual.md) |
| 修改本地播放、打开文件夹或本地文件错误提示 | [用户手册](user-manual.md)、[API 文档](api.md)、[实现文档](implementation.md)、[测试文档](testing.md) |
| 修改任务删除或文件清理行为 | [用户手册](user-manual.md)、[API 文档](api.md)、[实现文档](implementation.md)、[测试文档](testing.md) |
| 修改配置或依赖 | [开发文档](development.md)、[技术文档](technical.md)、README 快速启动 |
| 修改数据库模型或补列 | [实现文档](implementation.md)、[架构设计](architecture.md)、[data-model.puml](diagrams/data-model.puml) |
| 修改测试命令或测试策略 | [测试文档](testing.md)、README 测试摘要入口 |
| 重构模块边界或调整依赖方向 | [设计文档](design.md)、[架构设计](architecture.md)、[4+1 架构视图](4-plus-1-view.md)、[实现文档](implementation.md)、组件图、模块依赖图、4+1 逻辑视图和开发视图 |
| 修改任务队列、SSE、运行时设置、进度、暂停/重启/删除流程 | [设计文档](design.md#运行时并发模型)、[4+1 架构视图](4-plus-1-view.md)、[技术文档](technical.md)、[实现文档](implementation.md)、4+1 进程视图、运行时并发图、下载数据流图 |
| 修改端口、绑定地址、部署模式、外部工具、文件位置或本机打开方式 | [4+1 架构视图](4-plus-1-view.md)、[开发文档](development.md)、[安全审计报告](safety-review.md#14-网络安全)、4+1 物理视图 |
| 修改代理、网络自检、cookies 校验或运行环境自检 | [用户手册](user-manual.md)、[排障手册](troubleshooting.md)、[API 文档](api.md)、[openapi.yaml](openapi.yaml)、[技术文档](technical.md) |
| 修改日志、日志级别或错误提示文案 | [排障手册](troubleshooting.md)、[用户手册](user-manual.md#自检与日志)、[测试文档](testing.md)、[安全审计报告](safety-review.md) |
| 新增或删除用户关键操作 | [用户手册](user-manual.md)、[需求分析](requirements.md)、[4+1 架构视图](4-plus-1-view.md)、4+1 场景视图 |
| 修改安全相关的文件操作、CORS、日志清洗、环境变量或依赖 | [安全审计报告](safety-review.md)（更新基线 commit 与审查日期；新增的网络/文件/环境访问面要登记进「待复核项」） |

## 排障流程

0. **先看日志**：`data/logs/app.log`。启动三行快照（`proxy resolved` / `js runtime ...` / `dependencies ready`）
   能直接排除大半「环境没装齐」的可能；失败处会有 `category=` 结构与紧随其后的 `诊断 / 原因 / 建议N`。
   面向用户的完整版见 [排障手册](troubleshooting.md)。
1. 先读取任务中心的具体失败原因；单视频失败应直接显示 `JobItem.error`。
2. 查看 `/api/diagnostics`，确认依赖、cookies、impersonation、PO-token provider、aria2c 和稳定性参数。
   代理通不通另看 `POST /api/proxy/test`，cookies 是否真的能登录另看 `POST /api/cookies/verify`。
3. 按问题类型进入：
   - 面向用户的「我该点哪里」：[排障手册](troubleshooting.md)。
   - 下载策略和媒体流问题：[技术文档](technical.md)。
   - API 字段和请求问题：[API 文档](api.md)。
   - 任务状态和进度问题：[实现文档](implementation.md)。
   - 环境和依赖问题：[开发文档](development.md)。
4. 修复后补充测试；若是 YouTube 行为变化，至少增加可 mock 的单元测试。
5. 改动 `backend/app` 或 `frontend/src` 之后，跑一次 `python scripts\check_doc_anchors.py`，
   把漂移的行号锚点一起改掉（`--fix` 可自动重算能唯一确定符号的那些）。

## UML 更新流程

1. 修改对应 `.puml` 源文件。
2. 运行 `python scripts\docs.py render` 渲染 SVG 到 `docs/assets/diagrams/`。
3. 确认相关 Markdown 已嵌入 SVG，并链接 `.puml` 源文件。
4. 按变更类型同步受影响的视图：
   - 模块边界或依赖方向 → [module-dependencies.puml](diagrams/module-dependencies.puml)、组件图、4+1 逻辑视图与开发视图。
   - 下载链路、进度聚合或事件流 → [download-data-flow.puml](diagrams/download-data-flow.puml)、单视频/Playlist 时序图。
   - 队列、worker、锁、线程或 SQLite 会话 → [runtime-concurrency.puml](diagrams/runtime-concurrency.puml)、4+1 进程视图。
   - 逻辑/开发/进程/物理/场景视图任一变化 → 同步更新 [4+1 架构视图](4-plus-1-view.md) 和对应 `four-plus-one-*.puml`。
5. 运行 `python scripts\docs.py check` 和 `git diff --check`。

首次使用、系统依赖和工具缓存策略见 [文档写作与生成环境](documentation-workflow.md)。

## 提交流程

文档或代码完成后，按 [测试文档的自动测试命令](testing.md#自动测试命令) 执行对应验证，并在提交前运行 `git diff --check` 与 `git status -sb`。

仅文档变更原则上也应运行全量验证，确保没有误改源码或破坏构建。

## 审查重点

- 文档是否引用真实代码位置，而不是凭记忆描述。
- 文档之间是否重复同一段功能说明。
- README 是否仍是入口页。
- 新增环境变量、API 字段、任务状态、错误原因是否都有文档记录。
- 图是否反映当前架构，而不是历史结构。
- [设计文档](design.md) 的模块职责矩阵、依赖分层规则与已知限制列表是否仍与代码一致。
- [4+1 架构视图](4-plus-1-view.md) 是否仍覆盖逻辑、开发、进程、物理和场景五类关注点。
- [openapi.yaml](openapi.yaml) 的路径、状态码与模型字段是否仍与 `main.py`、`schemas.py` 一致（改动接口后必须显式核对，规范不会自动跟随代码）。
- 是否已运行 `python scripts\docs.py check`，确认本地链接和 UML 产物一致；并运行 `python scripts\check_doc_anchors.py` 确认代码行锚点没有漂移。
- 面向用户的报错文案是否都能被用户照着做：只给「失败」不给下一步的提示视为未完成（新增失败路径必须带 `next_steps`）。
- 是否遗漏 `ai/` 下的任务或审查记录。
