# 测试文档

适用读者：需要验证功能、回归下载策略或审查文档同步情况的开发者和测试者。

本文只维护验证命令和验收范围；应用启动方式不在这里重复。普通单端口启动见 [README 快速启动](../README.md#快速启动)，前端热更新开发启动见 [开发文档](development.md#本地运行)。

## 自动测试命令

后端语法检查：

```powershell
python -m compileall backend\app
```

后端测试：

```powershell
python -m pytest backend\tests -q
```

前端测试和构建：

```powershell
cd frontend
npm test
npm run build
```

空白检查：

```powershell
git diff --check
```

## 后端测试范围

后端测试位于 [backend/tests](../backend/tests/)。

| 文件 | 重点 |
| --- | --- |
| [test_api.py](../backend/tests/test_api.py) | API 行为、任务创建、重启、删除、cookies、设置和诊断。 |
| [test_ytdlp_service.py](../backend/tests/test_ytdlp_service.py) | yt-dlp 参数、profile、PO token、aria2c、格式选择和错误识别。 |
| [test_download_progress.py](../backend/tests/test_download_progress.py) | 多子流进度聚合，避免进度回退。 |
| [test_transfer_stats.py](../backend/tests/test_transfer_stats.py) | 平均速度计算。 |
| [test_paths.py](../backend/tests/test_paths.py) | 安全路径名。 |
| [test_log_safety.py](../backend/tests/test_log_safety.py) | 日志敏感信息清洗。 |
| [fakes.py](../backend/tests/fakes.py) | API 测试的 fake service 和辅助对象。 |

默认自动测试不依赖真实 YouTube 下载，避免网络、地区、cookies 和 YouTube 风控导致不稳定。

## 前端测试范围

前端组件测试位于 [App.test.tsx](../frontend/src/App.test.tsx)，测试夹具在 [frontend/src/test](../frontend/src/test/)。

重点覆盖：

- 链接解析和 playlist 条目选择。
- 下载选项默认值和提交请求体，包括默认 `1440p`、默认“两者都要”字幕来源，以及字幕来源 fallback。
- cookies 上传、浏览器导入、Edge 锁库提示。
- 任务中心状态、进度、速度、视频大小、实际分辨率、实际格式和显式删除入口展示。
- 任务中心播放已下载单视频和 playlist 子视频，打开视频所在文件夹和 playlist 文件夹，复制任务/子视频源链接，跳转 YouTube 页面；`output_path` 缺失但下载目录存在时，打开文件夹仍应可用；本地文件操作失败或找不到合适播放器时在对应任务行附近显示错误。
- Playlist 子视频单个删除、多选删除和删除文件确认。
- 旧的全局“删除任务时同时删除已下载视频”复选框应不存在。
- 分辨率降级提示和重启按钮。

## 手动验收

每次修改下载策略、cookies、任务中心或 API 时，建议执行：

1. 按 [README 快速启动](../README.md#快速启动) 启动单端口应用；若正在开发前端，可改用 [开发模式](development.md#前端热更新开发模式)。
2. 解析一个公开单视频，确认可显示标题、封面、清晰度和字幕信息。
3. 创建默认 1440p 下载任务，确认任务中心在下载前或下载开始后很快显示实际分辨率、格式和视频大小；如果源视频没有 1440p，应显示明确降级原因。
4. 解析一个小 playlist，选择多个条目，确认单项进度、失败原因和聚合状态。
5. 清除 cookies 后解析需要登录态的视频，确认错误提示可理解；重新导入 cookies 后重试。
6. 对失败任务执行指定清晰度重启，确认请求体和任务中心提示符合 [API 文档](api.md#endpoint)。
7. 下载完成后确认速度仍显示平均值。
8. 对单任务分别点击“仅删除任务”和“删除任务并删除已下载文件”，确认第二个入口会弹出确认框。
9. 展开 playlist 任务，分别验证单个子视频删除和多选子视频删除；删除最后一个子视频时父任务应消失。
10. 在任务中心多选任务，分别验证“批量删除任务”和“批量删除任务和已下载文件”的请求体。
11. 下载完成后点击播放按钮，确认应用选择可解码播放器打开对应文件；点击视频/合集文件夹按钮，确认文件管理器打开对应目录。Windows 下播放器或文件夹窗口应尽量弹出到前台，而不是只在任务栏闪烁；若故意移动文件或卸载可用播放器，错误应显示在对应任务行或子视频行附近，并包含当前格式和建议播放器。对旧任务或运行中任务，可手动清空 `output_path` 后确认后端仍能按文件名中的 YouTube id 发现最终视频或打开任务目录。
12. 点击复制按钮，确认剪贴板内容为对应单视频、playlist 或子视频链接。
13. 点击外链按钮，确认单视频、playlist 和子视频会打开对应 YouTube 页面。

## 高风险回归点

- YouTube 页面或媒体流变化导致 `yt-dlp` 解析或下载参数失效。
- 分离音视频流导致进度回退。
- 媒体流 403/连接重置被错误地自动降清晰度重下。
- 720p 自动降级底线失效。
- 单视频失败原因被任务级聚合错误覆盖。
- Cookies 导入暴露敏感信息或擅自关闭浏览器。
- README 与 `docs/` 重复，导致后续维护分叉。

## 文档验收

文档变更也应验证：

- 所有新增 Markdown 链接指向存在文件。
- 每个 SVG 图都由同名 `.puml` 生成，并嵌入至少一份文档。
- README 保持入口页，不重新复制 API、技术策略或排障长文。
- `git diff --check` 无输出。
