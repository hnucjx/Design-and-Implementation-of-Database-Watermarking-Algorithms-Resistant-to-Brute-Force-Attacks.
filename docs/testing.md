# 测试文档

适用读者：需要验证功能、回归下载策略或审查文档同步情况的开发者和测试者。

本文只维护验证命令和验收范围；应用启动方式已统一为仓库根 `init/` 下的一键脚本，用法见 [README 快速启动](../README.md#快速启动)（前端热更新开发模式见 [开发文档](development.md#本地运行)）。

## 自动测试命令

后端语法检查：

```powershell
python -m compileall backend\app
```

后端测试（当前基线：**354 passed**）：

```powershell
python -m pytest backend\tests -q
```

前端测试和构建（当前基线：**69 passed** + `tsc && vite build` 通过）：

```powershell
cd frontend
npm test
npm run build
```

空白检查：

```powershell
git diff --check
```

文档本地链接和 UML 产物一致性检查：

```powershell
python scripts\docs.py check
```

文档里的代码行锚点（Markdown 链接里带 `#Lnn` 的那些）会随代码漂移，可用脚本核对：

```powershell
python scripts\check_doc_anchors.py          # 只报告，有漂移时退出码 1
python scripts\check_doc_anchors.py --fix    # 能唯一确定符号的锚点直接重算
```

它只改「标签就是符号名」的锚点；标签是文件名或散文的（例如 `[main.py](../backend/app/main.py#L117)`）会列进「待人工复核」。

首次执行前如尚未初始化文档工具，请先阅读 [文档写作与生成环境](documentation-workflow.md)。用例数会随功能变化，本文档记录的是当前基线值；改动测试后请同步更新这里。

## 离线基准

下载并发与节流行为有两个可离线复现的基准脚本，不需要真实 YouTube 连通性：

```powershell
python scripts\bench_concurrency.py <临时目录>
python scripts\bench_throttle_guard.py <临时目录> 64   # 节流守卫开启时的中断-重提取循环
python scripts\bench_throttle_guard.py <临时目录> 0    # 关闭后的连续下载
```

判定标准与历史基线见 [PLAN.md](../ai/perf/PLAN.md) 第 6 节。修改下载调度、并发或节流相关参数后，应至少重跑与改动相关的基准，确认未劣化。

## 真实网络验收

`scripts/acceptance_real.py` 启动真实应用（真实 `YtDlpService` + 真实 yt-dlp），对公开播放列表跑多轮并发并输出逐项 CSV。
脚本会自动读取 Windows 系统代理（WinINet），因此**不需要单独配置代理环境变量**。

```powershell
python scripts\acceptance_real.py --items 1-12 --concurrency 1,3,5 --mode subtitles_only --subtitles en
python scripts\acceptance_real.py --mode video_only --items 1-6    # 需要 data/cookies.txt，否则媒体流 403
```

`scripts/acceptance_network.py` 是**网络与账号链路**的专项验收（代理 / cookies / 环境变量），
它对着真实 `YtDlpService` 跑，并把每一步的原始证据写成 JSON：

```powershell
python scripts\acceptance_network.py                          # 报告写到 tmp_acceptance/network-acceptance.json
python scripts\acceptance_network.py --skip-end-to-end        # 只跑连通性矩阵与 cookies 体检
python scripts\acceptance_network.py --skip-env-experiment    # 跳过 NODE_OPTIONS A/B（不想动环境变量时）
python scripts\acceptance_network.py --video <url> --timeout 30
```

它包含四段：启动环境快照、代理探测矩阵（自动 / 强制直连 / 显式 / 指向不存在的端口）、
cookies 体检与 `LOGGED_IN` 探针、端到端元数据提取（带与不带 cookies），
以及一段**环境净化 A/B**：注入 `NODE_OPTIONS=--require=<空补丁>` 后跑一次（预期失败），
再摘除该变量跑一次（预期成功）——这条 A/B 就是 JS challenge 那个缺陷的复现条件。
退出码 0 表示全部通过。

注意事项：

- 无 cookies 时 YouTube 媒体流一律 403，视频本体无法下载；字幕与元数据路径不受影响。
- 不要用 `--subtitles all`：空语言列表会被解释为下载**全部**字幕轨，触发 HTTP 429 并使整个 item 失败。
- 本地基准走 127.0.0.1，脚本已强制设置 `NO_PROXY`，避免被系统代理的 `<-loopback>` 规则拦截。
- 代理探测矩阵**故意包含两个失败用例**（强制直连、指向 `127.0.0.1:1`）：它们失败才说明代理参数真的被应用了。

最近一次执行结果与口径说明见 [PLAN.md](../ai/perf/PLAN.md) 第 9.1 节。

## 后端测试范围

后端测试位于 [backend/tests](../backend/tests/)（22 个 `test_*.py` + [fakes.py](../backend/tests/fakes.py)，当前 **354 passed**）：

| 文件 | 重点 |
| --- | --- |
| [test_api.py](../backend/tests/test_api.py) | API 行为、任务创建、重启、删除、cookies、设置、诊断、代理自检、cookies 体检、运行环境自检刷新，合集子视频按并发并行下载，happy-path 不再二次 extract_metadata，以及**探针抛异常时降级仍要真的执行并开始下载**。 |
| [test_db.py](../backend/tests/test_db.py) | SQLite WAL、`busy_timeout`、`synchronous=NORMAL` 与 WAL checkpoint。 |
| [test_ytdlp_service.py](../backend/tests/test_ytdlp_service.py) | yt-dlp 参数、profile、PO token、aria2c、格式选择和错误识别；**预检把「选不出格式」归一成 `is_selectable=False` 而不是抛出去，且无关异常仍要原样抛出**；JS 运行时探测（跳过坏候选、原样报错、探测本身不抛异常、诊断暴露失败原因）。 |
| [test_download_progress.py](../backend/tests/test_download_progress.py) | 多子流进度聚合：字幕/chunk 不锁死在 99.9%，分离音视频不把已下载字节重置为 0。 |
| [test_progress_persist.py](../backend/tests/test_progress_persist.py) | 进度 SQLite/SSE 写入节流：首次、终态、时间间隔和进度跳变。 |
| [test_transfer_stats.py](../backend/tests/test_transfer_stats.py) | 平均速度计算。 |
| [test_stall_guard.py](../backend/tests/test_stall_guard.py) | 停滞看门狗：字节推进不触发、零增长超时触发、节流振荡（峰值不刷新）触发、合并格式的流切换不被误判。 |
| [test_paths.py](../backend/tests/test_paths.py) | 安全路径名。 |
| [test_log_safety.py](../backend/tests/test_log_safety.py) | 日志敏感信息清洗。 |
| [test_logging_setup.py](../backend/tests/test_logging_setup.py) | 日志落盘：文件被创建、格式含时间/级别/模块、重复调用不叠加 handler、只读目录降级不抛异常、uvicorn logger 也被接上。 |
| [test_runtime_env.py](../backend/tests/test_runtime_env.py) | 宿主环境净化：只摘命中强加载开关的 `NODE_OPTIONS`、其它值原样保留、返回值可用于留痕、幂等。 |
| [test_error_advice.py](../backend/tests/test_error_advice.py) | 异常翻译层：JS challenge / cookies / 代理 / 媒体流四类分类顺序、异常链展开不因环状引用死循环、无法归类时返回 `None` 而不硬凑。 |
| [test_connectivity.py](../backend/tests/test_connectivity.py) | 代理探针：直连与走代理分别构造正确的 opener、HTTP 错误与网络异常都变成可展示证据、失败必带 `next_steps`、`direct` 失败文案说明「可能预期」。 |
| [test_cookie_health.py](../backend/tests/test_cookie_health.py) | cookies 体检：格式、域名分布、鉴权项命中/缺失、过期、`LOGGED_IN` 三态（真/假/未知）、结论与下一步。 |
| [test_resolution_decisions.py](../backend/tests/test_resolution_decisions.py) | 降级**判定**的纯单测（31 例，0.12 s）：四种降级原因各自的输入条件、"该不该找降级候选"的两个早退条件、两句失败文案不可互换、"元数据不可得"与"零个格式"必须区分、每个 reason 都能被 `fallback_policy` 翻成非空文案。 |
| [test_output_paths.py](../backend/tests/test_output_paths.py) | 产物位置计算：候选链「记录值优先 + 去重」、成品定位与 sidecar 排除；含一条**与重构前实现逐项对照**的用例（见 [ai/refactor/004](../ai/refactor/004-artifact-paths.md)）。 |
| [test_safe_delete.py](../backend/tests/test_safe_delete.py) | 删除白名单的路径安全判定：根目录与子孙放行，父目录、无关绝对路径、以及**共享字符串前缀的兄弟目录**一律拒绝，Windows 大小写差异视为同一根（见 [ai/refactor/005](../ai/refactor/005-safe-delete.md)）。 |
| [test_proxy.py](../backend/tests/test_proxy.py) | 代理**解析**：`direct` 哨兵、显式 > 系统 > 环境变量的优先级、环境变量只报告不落盘、`no_proxy` 绕过与凭据脱敏。 |
| [test_dev_server.py](../backend/tests/test_dev_server.py) | 端口预检与「谁占了这个端口」：`netstat` / `tasklist` 输出解析、可用端口向后查找、`YTDL_API_PORT` 默认值与 `.env` 锚定仓库根（[009](../ai/bug-fix/009-local-dev-port-is-occupied-and-unconfigurable.md)）。 |
| [test_system_open.py](../backend/tests/test_system_open.py) | 本地打开：Windows 目录开新窗口、文件走默认关联、有可用播放器时优先、无可用播放器时如实报告缺失。 |
| [test_finally_guards.py](../backend/tests/test_finally_guards.py) | **守护测试**：扫源码禁止 `finally` 里出现 `return`；自带「违规能被抓到」「嵌套作用域里的 `return` 不算违规」两条自检。 |
| [test_job_manager_finally.py](../backend/tests/test_job_manager_finally.py) | 行为层回归：条目被删除时收尾不再吞掉逃逸中的异常，崩溃的条目被判 `failed` 而不是永久停在 `running`（[008](../ai/bug-fix/008-return-in-finally-swallows-the-real-error.md)）。 |
| [fakes.py](../backend/tests/fakes.py) | API 测试的 fake service 和辅助对象，其中 `RaisingUnselectableProbeService` 刻意让 `prepare_download` **抛异常**而不是返回 `is_selectable=False` —— 复现真实 yt-dlp 的行为，防止降级分支再次退化成死代码。 |

默认自动测试不依赖真实 YouTube 下载，避免网络、地区、cookies 和 YouTube 风控导致不稳定。

## 前端测试范围

前端组件测试是对 `App` 的**集成测试**，按功能面拆成 `frontend/src/*.test.tsx` 多个文件：解析链接与下载选项、cookies 导入与校验、设置面板与代理检测、任务中心（控制/删除/展开、本地文件操作与链接、进度与详情呈现各一个）、清晰度降级、自检信息、帮助浮层、版面与文案不变式，以及只读样式源文的 `styles.test.ts`。

跨文件共享的东西只有三处，都在 [frontend/src/test](../frontend/src/test/)：测试台 [appHarness.ts](../frontend/src/test/appHarness.ts)（后端 `fetch` 替身与前后置钩子）、夹具 [appFixtures.ts](../frontend/src/test/appFixtures.ts)、样式源文解析 [cssRules.ts](../frontend/src/test/cssRules.ts)。

每个文件都必须能**单独**跑绿（`npx vitest run --environment jsdom src/<文件名>`）。拆分前 69 个用例同处一个文件，个别断言实际上是靠同文件里先跑过的用例才成立的（详见 [ai/refactor/008](../ai/refactor/008-split-app-tests.md)）；只按整包绿来看是看不出来的。

重点覆盖：

- 链接解析和 playlist 条目选择。
- 下载选项默认值、全局运行时设置保存和提交请求体，包括默认 `1440p`、默认“两者都要”字幕来源、字幕来源 fallback、限速和重试次数。
- cookies 上传、浏览器导入、Edge 锁库提示。
- 任务中心状态、进度、速度、视频大小、实际分辨率、实际格式和显式删除入口展示。
- 任务中心播放已下载单视频和 playlist 子视频，打开视频所在文件夹和 playlist 文件夹，复制任务/子视频源链接，跳转 YouTube 页面；`output_path` 缺失但下载目录存在时，打开文件夹仍应可用；本地文件操作失败或找不到合适播放器时在对应任务行附近显示错误。
- Playlist 子视频单个删除、多选删除和删除文件确认。
- 旧的全局“删除任务时同时删除已下载视频”复选框应不存在。
- 分辨率降级提示和重启按钮。
- 代理自检：检测失败时显示下一步建议、行内给出代理来源与耗时、以及「先试输入框里的地址」**不会**把地址写进设置。
- 代理端口对照：点浮层里的端口会填进输入框（而不是立刻保存）。
- 运行环境自检：JS 运行时不可用时显示原始报错与「The page needs to be reloaded.」的关联说明、日志文件路径可复制、「重新自检」会打到刷新接口。
- cookies 校验：联网校验、离线体检、以及「域名不对，等于没配」与「google.com-only」两档结论的解释文案。
- 辅助说明浮层：四个入口（获取方式 / 结论解读 / 常用端口 / 排查顺序）默认收起，悬停或点击可读，`Esc`、点击空白处、右上角 × 都能关闭，展开状态写入 `localStorage` 并在刷新后恢复。
- 字幕语言标签：选中几种就**逐个列出**几种语言代码（不许改成「已选 N 项」式的 JS 侧截断 —— 标签太长时正确的解法是让它换行，见 [ai/ui/003](../ai/ui/003-language-trigger-label-overflows-the-page.md)）。
- 样式源文不变式（**jsdom 不做布局，这是布局类缺陷唯一能进单测的部分**）：文件上传控件（`.file-button`）的基类不得声明纵向外边距与宽度（[ai/ui/001](../ai/ui/001-cookie-buttons-not-on-the-same-baseline.md)）；右栏（`.side-column`）必须声明 `overflow-wrap: anywhere`，且全表不得出现 `overflow-wrap: normal`（[ai/ui/002](../ai/ui/002-side-column-overflow-breaks-the-page.md)）；语言选择器标签（`.select-trigger span`）必须允许换行，且不得声明 `overflow:` 或 `text-overflow:`（「藏起来」不等于「放得下」，[ai/ui/003](../ai/ui/003-language-trigger-label-overflows-the-page.md)）。「两个按钮是否共线」「标签是否真的折了行」只能靠真实浏览器量，见 [ai/ui](../ai/ui/README.md)。

## 手动验收

每次修改下载策略、cookies、任务中心或 API 时，建议执行：

1. 用仓库根 `init/` 一键脚本启动单端口应用（`bash init/start.sh` 或 `init\start.ps1`）；若正在开发前端，加 `dev` 参数进入 [开发模式](development.md#前端热更新开发模式)。
2. 解析一个公开单视频，确认可显示标题、封面、清晰度和字幕信息。
3. 创建默认 1440p 下载任务，确认任务中心在下载前或下载开始后很快显示实际分辨率、格式和视频大小；如果源视频没有 1440p，应显示明确降级原因。
4. 下载过程中修改限速或重试次数，确认界面显示“保存中...”“已保存”或失败时的“保存失败”；保存成功后当前视频会短暂重启并继续断点续传，后续任务使用新值。
5. 解析一个小 playlist，选择多个条目，确认单项进度、失败原因和聚合状态。
6. 清除 cookies 后解析需要登录态的视频，确认错误提示可理解；重新导入 cookies 后重试。
7. 对失败任务执行指定清晰度重启，确认请求体和任务中心提示符合 [API 文档](api.md#endpoint)。
8. 下载完成后确认速度仍显示平均值。
9. 对单任务分别点击“仅删除任务”和“删除任务并删除已下载文件”，确认第二个入口会弹出确认框。
10. 展开 playlist 任务，分别验证单个子视频删除和多选子视频删除；删除最后一个子视频时父任务应消失。
11. 在任务中心多选任务，分别验证“批量删除任务”和“批量删除任务和已下载文件”的请求体。
12. 下载完成后点击播放按钮，确认应用选择可解码播放器打开对应文件；点击视频/合集文件夹按钮，确认文件管理器打开对应目录。Windows 下播放器或文件夹窗口应尽量弹出到前台，而不是只在任务栏闪烁；若故意移动文件或卸载可用播放器，错误应显示在对应任务行或子视频行附近，并包含当前格式和建议播放器。对旧任务或运行中任务，可手动清空 `output_path` 后确认后端仍能按文件名中的 YouTube id 发现最终视频或打开任务目录。
13. 点击复制按钮，确认剪贴板内容为对应单视频、playlist 或子视频链接。
14. 点击外链按钮，确认单视频、playlist 和子视频会打开对应 YouTube 页面。
15. 清除 cookies 后在受限视频上触发媒体流失败，确认错误文案以「当前 cookies 状态：未配置」开头，并给出重试建议。
16. 修改设置面板的「单视频并发下载数（1–4）」，确认保存成功且 `/api/diagnostics` 的 `aria2c_connections` 同步变化；确认未设置 `YTDL_ARIA2C_ENABLED=true` 时下载链路不会使用 aria2c。这一项旁的「怎么启用」说明浮层要**默认收起**、点击展开，且展开时不越出视口、内部无横向溢出（探针 `tmp_acceptance/ui_aria2c_help.py`，23 项断言）。
17. 设置面板点「检测代理」：通过时应看到 HTTP 200 与耗时；把端口改成 `1` 再点，应看到「积极拒绝」并给出下一步建议。随后在输入框里填一个地址后点「先试输入框里的地址」，确认**没有**触发 `PUT /api/settings`（试地址不等于保存）。
18. 点代理输入框旁的「常用端口」说明，在浮层里点一行地址，确认它只填进输入框、不立即保存。
19. 把 `cookies_enabled=false`（清除 cookies）后点「校验 cookies」，确认结论是「未配置」，并可从「获取方式」说明看到三种途径；导入 cookies 后再点一次，确认结论与「域上条数 / 命中的鉴权项 / 联网校验」三行证据一致。
20. 辅助说明浮层（页面加载后默认全部收起）：悬停「获取方式」「常用端口」应即看即收；点击后应固定展开，且**光标仍在原来的输入框里**（不抢焦点）。**记忆**：点击展开后直接刷新页面，应保持展开；按 `Esc`（或点空白处、点右上角 ×）关闭后再刷新，应保持收起——关闭会同时清掉这条记忆。
21. 用 DevTools 把视口切到 390px 宽（或设备模拟）：同一个说明入口应变成贴底的**底部抽屉**，可滚动，且抽屉内部**没有横向滚动条**（说明正文必须换行，不能继承触发位置的 `white-space: nowrap`）。
22. 检查 `data/logs/app.log`：启动时应有 `proxy resolved` / `js runtime ready`（或 `js runtime unavailable`）/ `dependencies ready` 三行；故意制造一次失败后，应有 `category=` 结构与紧随其后的 `诊断 / 原因 / 建议N` 块。
23. 设置面板点「重新自检」，确认 JS 运行时状态会被重新探测（装上 Node/Deno 后无需重启应用）。
24. 在桌面宽度（≥1280px）下看解析面板的 cookies 操作行：「选择 cookies」与「清除 cookies」必须**同顶同底**，右侧「自动检测浏览器 / 从浏览器导入」也在同一基线。这一行曾因文件上传控件的基类声明了 `margin-top` 而整体下沉 8px（等宽同高、就是没对齐），见 [ai/ui/001](../ai/ui/001-cookie-buttons-not-on-the-same-baseline.md)。
25. 把窗口宽度依次改成 1280 / 1366 / 1440 / 1520 / 1600，**页面底部不应出现横向滚动条**，右栏（下载选项 / 设置）应完整落在视口内。右栏是固定 390px 的网格轨道，里面任何一个「不能断行的长 token」（自检回显的 Windows 路径最典型）都会把整页撑宽 —— 这一条曾漏到 ≤1600px 时溢出 44px，见 [ai/ui/002](../ai/ui/002-side-column-overflow-breaks-the-page.md)。
26. 解析一个有较多字幕轨道的视频，在「字幕语言」里多勾几种（12 种即可稳定复现）：标签应**就地折成多行**，选择器随之变高，页面**不得**出现横向滚动条，且标签里的语言代码**一个都不能少**（不允许被裁成省略号）。标签是 `已选 N 项：en, zh-Hans, …`、N 无上界，它曾经被 `white-space: nowrap` 锁成一行 → 不是被裁而是把整页顶宽，见 [ai/ui/003](../ai/ui/003-language-trigger-label-overflows-the-page.md)。

## 高风险回归点

- YouTube 页面或媒体流变化导致 `yt-dlp` 解析或下载参数失效。
- 并发只按任务占用 worker，导致合集子视频无法并行下载。
- 字幕、HTTP chunk 或分离音视频流把运行中进度锁在 99.9%，或把已下载字节重置为 0。
- 媒体流 403/连接重置被错误地自动降清晰度重下。
- 720p 自动降级底线失效。
- 单视频失败原因被任务级聚合错误覆盖。
- `throttledratelimit` 被重新默认打开，导致「每约 5 秒中断并重新 extract」的性能回退，见 [PLAN.md](../ai/perf/PLAN.md) §3.1。
- 停滞看门狗的文案被改动后误命中 [is_media_stream_blocked_error](../backend/app/ytdlp_service.py#L670)，把停滞误分类成媒体流阻塞。
- aria2c 多连接在默认配置下被启用，推高 403 率。
- Cookies 导入暴露敏感信息或擅自关闭浏览器。
- JS 运行时探测退化成「只看文件是否存在」：那样「检测到 node 却解不出 n challenge」会重新变成不可诊断的状态。
- 宿主环境变量净化被扩大化：只应摘掉会强加载外部脚本的 `NODE_OPTIONS`，而不是顺手清掉用户的其它环境变量。
- 日志重新退回「只往 stderr 打」：`data/logs/app.log` 消失会让用户失去唯一的取证手段。
- 「先试输入框里的地址」重新变成「先保存再试」：一个填错的端口会被落盘。
- README 与 `docs/` 重复，导致后续维护分叉。

## 文档验收

文档变更也应验证：

- 所有新增 Markdown 链接指向存在文件。
- 每个 SVG 图都由同名 `.puml` 生成，并嵌入至少一份文档。
- README 保持入口页，不重新复制 API、技术策略或排障长文。
- `python scripts\docs.py check` 输出「文档检查通过」，即本地链接有效且 SVG 与 `.puml` 源一致。
- `git diff --check` 无输出。
