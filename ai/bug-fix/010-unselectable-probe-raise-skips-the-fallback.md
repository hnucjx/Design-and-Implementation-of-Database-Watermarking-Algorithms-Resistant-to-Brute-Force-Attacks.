# 010 · 提示「已自动降级到 1080p」，却根本没有开始下载

- 状态：已修复
- 提交：`fix(job_manager): 预检「选不出格式」不再当异常抛出，降级才会真的执行`
- 影响面：**任何**「用户选的清晰度在源视频里不存在」的下载 —— 默认清晰度是 `1440p`，
  而 YouTube 上大量视频最高只有 1080p，所以这是默认路径上的必然命中。
- 严重度：高。任务 100% 失败且**一个字节都不下**，同时给出一句「已自动降级到 1080p」的
  反向保证，把用户引向「是不是网络/cookies 坏了」的错误排查方向。

## 问题

在 Edge 里下载 `https://youtu.be/hTdSU7q5WCo`（"On Vibe Coding"，默认 1440p），任务行依次显示：

```text
failed · 0/1 完成 · 当前没有 1440p 的视频，低于选定分辨率的最高可用分辨率是 1080p。
检测到 1440p 清晰度，但该清晰度当前没有可下载的视频/音频组合，已自动降级到 1080p。
```

然后就停在那里：没有进度、没有 `.part` 文件、`实际分辨率` 与 `视频大小` 一直是空的。
点「重启」重来一次，结果一模一样（日志里 22:29:09 与 22:38:36 两次失败完全相同）。

这两句话**各自都有错**：

1. 「**检测到 1440p 清晰度**」—— 该视频根本没有 1440p，最高只有 1080p。
2. 「**已自动降级到 1080p**」—— 降级只被写进了状态字段，没有任何代码真的去下载 1080p。

## 原因

### 直接原因：yt-dlp 的接口形状与预期不符

`YtDlpService.prepare_download()` 用 `ydl.extract_info(url, download=False)` 做「这个清晰度能不能选」的预检。
**但 yt-dlp 的格式选择就发生在 `extract_info` 内部**：匹配不到时它**直接抛
`DownloadError: Requested format is not available`**，而不是返回空结果。
于是 `prepare_download` 里那句 `if not selected: return DownloadPreparation(is_selectable=False)`
在实际故障下**走不到** —— 函数在它之前就抛出去了。

而 `JobManager._prepare_download()` 的整段降级逻辑挂在**返回值**上：

```python
preparation = self.service.prepare_download(...)
if preparation.is_selectable:      # ← 抛异常时这里根本执行不到
    ...
    return options
if options.format_id or ... is None:
    return options
...                                # ← 降级 + 重试全都在下面，成了死代码
```

异常一路逃到 `_run_item()` 的兜底 `except Exception`，被 `_annotate_resolution_fallback()`
按「下载阶段格式不可用」处理，因此顺序是：

1. `item.error = readable_error_message(exc)`，**紧接着被覆盖**成
   `_resolution_fallback_message()` → 这就是用户看到的第 1 句；
2. `fallback_reason = requested_resolution_unselectable` → 读模型渲染出第 2 句
   （`build_resolution_fallback` 的 UNSELECTABLE 分支文案里带着「已自动降级到 …」）；
3. `item.status = failed`。

**没有任何一步去下载。** 「已自动降级」只是文案，不是行为。

### 实测证据

**E1. 数据库里的终态**（`data/app.sqlite3`，只读打开）

```text
job  78ea0896-2952-4016-9b84-6a4c3366c764  status=failed  error=当前没有 1440p 的视频，低于选定分辨率的最高可用分辨率是 1080p。
item ac19f8c8-1a66-4d01-becc-d3b8270fbed7  status=failed  progress=0.0  downloaded_bytes=None
     actual_width=None  actual_height=None  output_path=None  options_json=None
     requested_resolution=1440p  fallback_resolution=1080p  fallback_reason=requested_resolution_unselectable
```

`downloaded_bytes` 与 `output_path` 均为 `None` 且 `progress=0.0` —— 下载从未开始。

**E2. 事件流里没有 `item_prepared`**（`jobevent` 表）

```text
51170 item_started    14:28:45.388
51171 item_finished   failed  14:29:09.250      ← 中间 24 秒，没有任何其它事件
51175 item_started    14:38:15.883
51176 item_finished   failed  14:38:36.298
```

`item_prepared` 是 `_apply_download_preparation()` 唯一发出的 SSE 事件。它缺席，说明
**降级成功的那条正确路径从未被执行过**（那条路径一定会发这个事件）。同理
`item.total_bytes`/`actual_width`/`actual_height` 全是 `None`。

**E3. 用真实 URL 直接复现**（`tmp_acceptance/repro_1440p_fallback.py`）

```text
=== 1. extract_metadata：可用高度直方图 ===
  title: On Vibe Coding
  heights: {144: 3, 240: 3, 360: 4, 480: 3, 720: 3, 1080: 3}
  含 1440: False | 含 1080: True          ← 1440p 本就不存在

=== 2. prepare_download(resolution=1440p) ===
  [RAISED] DownloadError: ERROR: [youtube] hTdSU7q5WCo: Requested format is not available.

=== 2. prepare_download(resolution=1080p) ===
  [RETURNED] is_selectable=True height=1080 format=mp4 · avc1 + mp4a
```

这是在**修复前**跑的：同一个函数，1440p 抛异常，1080p 正常返回。降级目标本身是可达的。

**E4. 测试替身与真实实现不一致，是这个缺陷能出厂的直接原因。**
`tests/fakes.py` 里所有 fake 的 `prepare_download` 都是
`return SimpleNamespace(is_selectable=False, ...)` —— **返回**不可选；
而真实的 `YtDlpService.prepare_download` 是**抛异常**。既有测试
（`test_unselectable_high_resolution_auto_fallback_keeps_original_restart`）因此一直通过，
覆盖的却是现实中不存在的路径。这是本次新增 `RaisingUnselectableProbeService` 的理由。

**E5. 同一形态在此前就出现过一次**（`data/logs/app.log` 17:23:27，另一个视频）：

```text
download item failed: title='C++ Weekly - Ep 59 - Negative Cost Embedded C++ - Part 2'
resolution=1440p category=format_unavailable error=ERROR: [youtube] u615he5wdeo: Requested format is not available.
```

即这不是某个视频的偶发，而是**默认 1440p 下的系统性失败**。

## 修复方案

两处改动，同一语义，分别落在「源头」和「调用方」：

**1. `YtDlpService.prepare_download()`（源头归一）** —— 把该异常转成否定结论：

```python
try:
    info = ydl.extract_info(url, download=False)
except Exception as exc:
    if not self.is_requested_format_unavailable_error(exc):
        raise                       # 网络 / JS challenge / cookies 失效必须继续抛
    return DownloadPreparation(is_selectable=False)
```

预检的语义是「这个清晰度**能不能**选」，选不出来是**正常结论**而非调用失败 ——
这个转换本就该由预检自己完成，而不是让每个调用方去理解 yt-dlp 的接口形状。

**2. `JobManager._probe_preparation()`（调用方兜底）** —— 新增一层，把两种表现都当成不可选，
`_prepare_download` 的首尾两次探测都改走它：

```python
try:
    preparation = self.service.prepare_download(...)
except Exception as exc:
    if not YtDlpService.is_requested_format_unavailable_error(exc):
        raise
    return None                     # None = 不可选
if not preparation.is_selectable:
    return None
return preparation
```

这样即使将来换了 service 实现、或测试替身又写歪，降级分支也不会再退化成死代码。

### 被否决的方案

- **只改 `job_manager`（在调用处吞异常）**：可行，但把「yt-dlp 在这里抛异常」这个事实
  留在服务层之外，每个新调用方都得再学一次。否决。
- **只改 `prepare_download`（不加调用方兜底）**：修好了当下，但 `JobManager` 依然
  依赖「探针不抛异常」这条**未被任何契约保证**的假设。否决。
- **把该类错误当成通用可重试错误，在下载阶段自动重下**：与既有设计相悖 ——
  [architecture.md](../../docs/architecture.md) 明确「分辨率降级只在下载前可判断的场景自动发生；
  媒体流 403/连接重置不会中途自动降级重下」。且这会把「用户点重启」这个显式动作变隐形。否决。
- **把格式选择器从 `[height=1440]` 放宽成 `[height<=1440]`**：等于让 yt-dlp 静默挑一个别的清晰度，
  用户选了 1440p 却拿到 720p 且无人告知。否决 —— 现有策略是「只选精确高度，选不到就降级**并说明**」。

## 效果

| | 修复前 | 修复后 |
|---|---|---|
| `_prepare_download` 返回 | 抛 `DownloadError`（降级分支被跳过） | `options.resolution == "1080p"` |
| `fallback_reason` | `requested_resolution_unselectable`（**错**：1440p 从不存在） | `requested_resolution_missing`（对） |
| 界面文案 | 「检测到 1440p 清晰度，但…已自动降级到 1080p。」 | 「视频本来没有 1440p，已自动降级到 1080p。」 |
| 下载 | 从未开始（`downloaded_bytes=None`） | 媒体流真的开始 |
| 条目状态 | `failed` | `succeeded` |

真实端到端验收（`tmp_acceptance/acceptance_1440p_fallback.py`，真实 yt-dlp + 真实 URL + 本机 cookies）：

```text
[1] _prepare_download(options.resolution=1440p) ...
    返回的 options.resolution = 1080p  (期望 1080p)
    item.requested_resolution = 1440p
    item.fallback_resolution  = 1080p
    item.fallback_reason      = requested_resolution_missing
    item.error                = None
    item.actual_height        = 1080
[2] 用返回的 options 真的下载（拿到首个字节即取消） ...
    [download]   0.3% of  306.92KiB at  884.31KiB/s ETA 00:00
    进度回调次数 = 1   已下载字节 = 1024
验收通过：降级生效，且媒体流真的开始下载。
```

## 未覆盖 / 如实说明      ← 必填

1. **没有跑完整的视频下载**。端到端验收在拿到第一个字节后立刻取消（避免真落一个几百 MB 的文件），
   因此「1080p 能下完、能合并、文件可播放」这一次**没有**再次验证 —— 它由既有测试
   （`test_single_download_auto_falls_back_to_highest_lower_resolution` 等）在 fake 层面覆盖。
2. **`requested_resolution_unselectable` 的文案问题只解决了一半。** 「已自动降级到 …」这句在
   **下载阶段**那条路径上依然会在「没有真的降级」的情况下出现（设计上那里只做标注、等用户重启，
   见 `docs/architecture.md`）。本次修复让用户报告的那个视频不再走这条路径，但该路径本身
   仍然「名实不符」。未改文案，因为改动会波及前端 fixture 与 `docs/openapi.yaml` 的示例值，
   属于另一件事。记为下一轮候选。
3. **探针仍会向 stderr 打一行 `ERROR: ... Requested format is not available.`。**
   实测确认：yt-dlp 在 `trouble()` 里先 `to_stderr()` 再抛异常，`quiet=True` 拦不住它。
   对用户是无害噪音（任务其实是成功的），但会让人误以为出了错。未处理。
4. **`_probe_preparation` 的兜底只在 `is_requested_format_unavailable_error` 命中时生效。**
   如果 yt-dlp 将来改了这句英文报错，两处归一都会失效 —— 缺陷会以「降级不执行」的形式回归。
   这是有意的窄匹配（宁可失效也不要吞掉真故障），但没有做版本探测或双关键词兜底。
5. **只验了「选的清晰度不存在」这一种不可选。** 「元数据显示存在、但 selector 选不出组合」
   （`requested_resolution_unselectable` 的正统场景）没有用真实视频复现过。

## 验证

新增/修改的测试：

| 用例 | 断言什么 |
|---|---|
| `test_prepare_download_reports_unselectable_instead_of_raising` | monkeypatch `yt_dlp.YoutubeDL` 使其抛 `Requested format is not available` → `prepare_download` **返回** `is_selectable=False`，不抛 |
| `test_prepare_download_reraises_errors_that_are_not_about_format_selection` | 抛 `The page needs to be reloaded.` → 原样抛出（对照组，修复前后都应通过） |
| `test_unselectable_probe_that_raises_still_auto_falls_back_and_downloads`（e2e） | 用会**抛异常**的 `RaisingUnselectableProbeService` → 任务 `succeeded`、实际下载清晰度 `720p`、`fallback_reason=requested_resolution_missing`、`error is None` |
| `RaisingUnselectableProbeService`（`tests/fakes.py`） | 新增 fake：让 `prepare_download` 抛异常而非返回，复现真实 yt-dlp 行为，防止测试替身再次与实现脱节 |

A/B（把两个 app 文件 `git checkout --` 回修复前，逐个点名跑这三个用例）：

```text
F.F                                                                      [100%]
FAILED tests/test_ytdlp_service.py::test_prepare_download_reports_unselectable_instead_of_raising
FAILED tests/test_api.py::test_unselectable_probe_that_raises_still_auto_falls_back_and_downloads
2 failed, 1 passed in 7.99s
```

修复前 e2e 用例的失败信息与线上日志同形：

```text
AssertionError: Job 9d2b7fcc-... did not reach succeeded
WARNING app.job_manager | download item failed: ... resolution=1080p category=format_unavailable
error_class=DownloadError error=ERROR: [youtube] unsupported: Requested format is not available.
```

全量回归：后端 **286 → 289 passed**；前端 63 passed；`tsc --noEmit` 通过。

## 风险与回滚

- **风险**：把该异常吞成「不可选」后，若某个视频在**下载阶段**也抛同一句错误，预检会如实判定不可选，
  进而降一级重试 —— 这是期望行为（多花一次元数据请求）。真正需要警惕的是
  `is_requested_format_unavailable_error` 这个字符串匹配**将来失效**，那时回退成今天的行为
  （任务失败 + 误导性文案），不会造成数据损坏。
- **副作用**：对「所选清晰度不存在」的视频，现在会多一次 `extract_metadata`（预检失败 → 解析元数据分类原因），
  这是设计内的一次网络请求，与既有 `requested_resolution_missing` 路径完全一致。
- **回滚**：`git revert <010 的 sha>`。仅退化行为，不动数据与表结构。

## 关联

- 与 [008](008-return-in-finally-swallows-the-real-error.md) **无因果关系**，但同属一个主题：
  **代码看起来「已经处理了这种情况」，而那条处理路径在真实输入下从不执行**
  （008 是 `finally` 里的 `return` 让收尾逻辑走不到；这里是分支条件挂在了错误的信号上）。
- 与 [007](007-js-challenge-fails-only-when-cookies-are-on.md) 的关系是**症状相似、病因不同**：
  两者都会抛出一句指不到病因的 yt-dlp 报错，但 007 在**提取阶段**（JS challenge，整条链不可用），
  本缺陷在**格式选择阶段**（只是清晰度降级没执行）。排障时先看 `category`：
  007 是 `js_challenge_failed`，本缺陷是 `format_unavailable`。
- 是本轮「任务提示与实际行为不一致」的第一例；同一主题的剩余项见
  [README 的「已知但未处理」](README.md#已知但未处理的问题下一轮候选) 第 10 条。
