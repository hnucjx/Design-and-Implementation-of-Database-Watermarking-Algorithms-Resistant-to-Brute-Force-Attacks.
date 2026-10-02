# 下载性能与稳定性修复计划（2026-09-30）

> 适用范围：YouTube 下载器（`cascade`）后端下载链路。本文是**根因分析 + 修复方案 + 验证与回滚**的执行计划，实施阶段按「优先级 + 每个修复一个 commit」推进。

---

## 1. 结论摘要

三个现象对应三个不同的根因，**彼此独立，不要混为一谈**：

| 现象 | 根因（一句话） | 优先级 |
| --- | --- | --- |
| 下载易中断 / 看似超时 | `throttledratelimit=64 KB/s` 触发 yt-dlp 的 `ThrottledDownload`，而它是 `ReExtractInfo`，被 yt-dlp 的**无计数器的 `while True` 重提取循环**接住 → 每约 5 秒中断并重新开始一次；叠加 `no_warnings=True`，全程静默，任务表现为「卡住」 | **P0** |
| 下载速度慢 | 同一个中断-重提取循环把有效吞吐切成 5 秒一段，每段前还要付一次完整 extract（含 `sleep_interval_requests=1.0` 的 bot pacing）；叠加 66%（141/213）的媒体流 403 失败率 | **P0 / P1** |
| 并发未产生加速 | **并发逻辑本身是生效的**（实测完美线性：并发 1/2/4/8 → 8.16/4.31/2.28/1.26s）。问题是：① 并发是**视频（item）级**的，单视频任务只有 1 个 item，并发对它恒等于 1；② 并发越高，单流速度越低，越容易跌破 64 KB/s 的节流阈值 → 并发越高越容易被上述循环打断 | **P1** |

一句话执行顺序：**先关掉节流守卫（1 行改动、收益最大），再补停滞可观测性，最后处理并发语义与可选项。**

---

## 2. 审查方法与环境基线

### 2.1 证据来源

全部结论来自下列四类取证，**不依赖猜测**：

1. 源码静态审计：`backend/app/` 全部模块；
2. 依赖源码审计：本机 `yt-dlp 2026.07.04`（`.venv/Lib/site-packages/yt_dlp/`）的下载器与 `YoutubeDL` 主控流程；
3. 生产数据取证：`data/app.sqlite3` 中 548 条 `jobitem` 的状态 / 速度 / 错误分布；
4. 可复现实验：两个已固化的基准脚本（见 §6.3、§6.4）。

### 2.2 环境基线（2026-09-30 实测）

| 项 | 值 | 说明 |
| --- | --- | --- |
| Python | 3.14.6 | `asyncio.to_thread` 默认线程池 `max_workers=28`（实测），**不是并发瓶颈** |
| CPU | 24 逻辑核 | — |
| yt-dlp | 2026.07.04 | — |
| ffmpeg | 可用（`/c/msys64/ucrt64/bin/ffmpeg`） | 合并链路正常 |
| Node | 22 可用 | yt-dlp JS 运行时可用 |
| aria2c | **未安装** | 外部下载器不可用 |
| `data/cookies.txt` | **不存在** | 403 高风险状态 |
| `.env` | 不存在 | 无 PO token / visitor data / 代理配置 |
| 本机到 youtube.com | **HTTPS 直连超时**（DNS 可解析） | 真实验收必须先解决连通性，见 §6.5 |

### 2.3 生产数据取证（`data/app.sqlite3`，548 条 jobitem）

```
status 分布 : succeeded 70 / failed 143 / queued 332 / running 3
失败原因    : 141 条 = "YouTube 拒绝了媒体流下载（HTTP 403）或重置了媒体流连接"
              2 条 = sqlite3.OperationalError: database is locked（发生在 WAL 启用之前）
速度(79 条) : avg 171 KB/s，min 0.9 KB/s，max 1.86 MB/s
```

判定：

- 已结束项共 213 条（succeeded 70 + failed 143），**失败率 67%**；其中 **141/213（66%）是 403 / 连接重置**，即「不稳定」的主因在 YouTube 侧与网络侧，不是应用内部逻辑；
- 2 条 `database is locked` 的时间戳早于 WAL 提交（2026-08-13 vs `db.py` 修改时间 08-14），说明 **WAL 已生效**；
- 79 条有速度记录的 item：avg 171 KB/s、min 0.9 KB/s、max 1.86 MB/s —— 分布跨越 64 KB/s 阈值，说明**至少有相当比例的流会跌破阈值**（仅凭此数据无法给出精确比例，因为记录的是平均速度而非瞬时速度）。

### 2.4 上一轮性能计划已完成项（本计划不再重复）

`ai/plan.md` 中 "Download Performance and Stability Implementation Plan" 的 Task 1–4 已落地且已验证在代码中：

- SQLite WAL + `busy_timeout=5000` + `synchronous=NORMAL`（`backend/app/db.py:14-24`）；
- 进度写入节流 `ProgressPersistGate`（`backend/app/progress_persist.py`，`job_manager.py:520`）；
- 快乐路径只做一次 `prepare_download`（`job_manager.py:849-905`，`_options_for_available_resolution` 已删除）；
- 下载选项不再设置 `sleep_interval` / `max_sleep_interval`（`ytdlp_service.py:242-260` 中已无这两项）。

---

## 3. 根因分析（逐个问题）

### 3.1 问题一：下载连接不稳定、易中断、看似超时

**根因：节流守卫 `throttledratelimit` 把「慢」误判成「被节流」，触发 yt-dlp 的无界重提取循环。**

证据链：

1. **我们设置了阈值**：`backend/app/config.py:36`

   ```python
   throttled_rate_kbps: int = Field(default=64, ge=0)
   ```

   `backend/app/ytdlp_service.py:270-273`

   ```python
   if options.mode != "subtitles_only":
       ydl_opts["http_chunk_size"] = self.anti403_http_chunk_size_mb * 1024 * 1024
       if self.throttled_rate_kbps > 0:
           ydl_opts["throttledratelimit"] = self.throttled_rate_kbps * 1024   # = 65536 B/s
   ```

2. **yt-dlp 的判定是按「单条流」速度**：`yt_dlp/downloader/http.py:315-323`

   ```python
   if speed and speed < (self.params.get('throttledratelimit') or 0):
       if ctx.throttle_start is None:
           ctx.throttle_start = now
       elif now - ctx.throttle_start > 3:
           raise ThrottledDownload
   ```

   `speed` 是**这一条流**的速度，不是总带宽。并发 N 条流时，每条流约等于总带宽 / N。

3. **`ThrottledDownload` 是 `ReExtractInfo`**：`yt_dlp/utils/_utils.py:1131-1136`

   ```python
   class ThrottledDownload(ReExtractInfo):
       """ Download speed below --throttled-rate. """
       msg = 'The download speed is below throttle limit'
   ```

4. **yt-dlp 对 `ReExtractInfo` 是「无计数器重提取」**：`yt_dlp/YoutubeDL.py:1727-1741` 的 `_handle_extraction_exceptions` 装饰器，作用于 `YoutubeDL.__extract_info`（`YoutubeDL.py:1862-1863`）——这正是 `download()` → `extract_info` 的实际执行体（`YoutubeDL.py:3714-3716`）：

   ```python
   while True:
       try:
           return func(self, *args, **kwargs)
       except ReExtractInfo as e:
           ...
           continue      # ← 没有次数上限，没有退避上限
   ```

   因此 `ThrottledDownload` **永远不会冒泡到我们 `download()` 的 profile 链**（`ytdlp_service.py:323-350`），任务既不失败也不结束。

5. **全程静默**：`ytdlp_service.py:244` 设置了 `"no_warnings": True`，循环中唯一的 `report_warning` 被吞掉；UI 只看到进度条不动。

6. **可复现实验（已固化）**：`scripts/bench_throttle_guard.py`（本地 HTTP 服务以 ~10 KB/s 供流）

   ```
   throttled_rate_kbps=64 : 25s 内 HTTP GET 10 次，每 ~5s 重启一轮；
                            25s 后下载线程仍在运行；无异常抛出
                            progress 采样：0s→56331B，5.01s→回落到 1024B，10.03s→再次回到 1024B
   throttled_rate_kbps=0  : HTTP GET 2 次，单请求连续下载至 46%+，不再重启
   ```

   对照组证明：去掉这个阈值，行为立刻从「每 5 秒中断重启」变为「连续下载」。

**次级因素（放大「超时」观感）**：

- `socket_timeout=30`（`ytdlp_service.py:254`）+ `retries=10`（`schemas.py:68`）+ `_bounded_retry_sleep = min(30, n*2)`（`ytdlp_service.py:611-613`）→ 单 profile 最长约 110s 纯等待；
- profile 链共 5 个（`ytdlp_service.py:52`），每次 profile 切换都要重新完整 `extract_info`，并付一次 `sleep_interval_requests=1.0` 的 pacing（`ytdlp_service.py:259`）→ 失败路径最长可达 10 分钟无可见进展。

### 3.2 问题二：下载速度偏慢

**根因 1（主）：§3.1 的中断-重提取循环把吞吐切成碎片。**

每 5 秒有效传输后，要重做一次完整 extract（页面请求 + player 请求 + JS 求解 + 若干 1 秒 `sleep_interval_requests`），再重新开始传。实测循环周期 ≈ 5s 传输 + 5s 重启开销 → **有效吞吐腰斩甚至更低**。这是「速度慢」最可量化、也最容易被误判成「网速慢」的部分。

**根因 2：403 失败率高，重试成本直接计入耗时。**

141 条媒体流失败（占已结束项 66%）全部走完 5 个 profile 才放弃；每次失败前已付出至少一次完整 extract 与若干重试 sleep。

**根因 3：`http_chunk_size=16MiB` 会按块重开 HTTP 请求（静态证据，非速度主因）。**

`yt_dlp/downloader/http.py:332-334` + `360-370`：当设置了 `http_chunk_size` 时，每下载满一个 chunk 就 `raise NextFragment`，随后**重新发起带 Range 的新 HTTP 请求**。这是刻意的 anti-403 手段，代价是请求数增加（一个 200 MB 文件 ≈ 13 次请求）。当前 16 MiB 是合理折中，**不建议调小**（请求更多 = 403 机会更多），也不建议调大（丧失 anti-403 作用）。**保持现状，只记录。**

**根因 4：单条流本身是单连接。**

格式选择器 `bv*[height=1440]...+ba[...]`（`ytdlp_formats.py:18-24`）意味着**视频流与音频流在同一个 item 内串行下载**，`concurrent_fragment_downloads=1`（`ytdlp_service.py:255`）进一步保证 item 内没有任何并行。因此单视频吞吐 = 单条 HTTP 流吞吐。

### 3.3 问题三：并发未产生预期加速

**先回答「并发逻辑是否真正生效」：生效，而且是线性生效。**

判定依据（`scripts/bench_concurrency.py`：8 个 item 的 playlist 任务，假服务 `download()` 固定 sleep 1s）：

```
concurrency=1: wall=8.16s  peak_parallel_downloads=1
concurrency=2: wall=4.31s  peak_parallel_downloads=2
concurrency=4: wall=2.28s  peak_parallel_downloads=4
concurrency=8: wall=1.26s  peak_parallel_downloads=8
```

逐项排除用户点名的可疑原因：

| 可疑原因 | 结论 | 依据 |
| --- | --- | --- |
| 串行执行 | **否** | `job_manager.py:381-390` worker 各自 `await asyncio.to_thread(self._run_item_work, item_id)`；实测 peak = 并发数 |
| 线程池上限过小 | **否** | 实测默认 `ThreadPoolExecutor` `max_workers=28` > 默认并发 5 |
| 锁竞争 | **否** | `_item_claim_lock`（`job_manager.py:411`）只包住「刷新状态 + 置 running + commit」，不覆盖下载；`_cookie_import_lock` 仅在 403 后导入 cookies 时短暂持有 |
| 连接池上限 | **否（且不是瓶颈）** | SQLite 侧已 WAL + `busy_timeout`；HTTP 侧由 yt-dlp 每请求独立连接，无池上限 |
| yt-dlp 参数不当 | **部分** | `concurrent_fragment_downloads=1` 使 item 内无并行（对 YouTube DASH 单文件流本也无用）；真正的问题是 §3.1 的节流阈值 |

**真正的原因有三条：**

1. **并发是 item（视频）级的，单视频任务只有 1 个 item。**
   `main.py:145-171`：创建任务时按 `entries` 建 `JobItem`，单视频 URL 得到 1 条 entry → 1 个 item。因此**并发数对单视频任务恒为 1**，无论设置成多少。UI 标签「并发（若追求稳定，可设为 1）」（当时在 `frontend/src/App.tsx:1037`，2026-10-02 重构后随设置面板移到 `frontend/src/components/SettingsPanel.tsx`）没有说明这一点，这是「调了并发没变化」的直接来源。

2. **并发越高越容易触发节流中断。**
   阈值 65536 B/s 是**按单条流**判定的（§3.1 证据 2）。并发 N 时单流速度约 = 总带宽 / N：
   - 总带宽 500 KB/s、并发 5 → 单流 100 KB/s > 64 KB/s，安全；
   - 总带宽 300 KB/s、并发 5 → 单流 60 KB/s < 64 KB/s → **全部 5 条流同时进入中断-重提取循环**。

   结论：**并发不但可能不加速，反而会把有效吞吐打回接近 0**（每 5 秒有效传输 + 一次完整 extract 重启开销）。

   注意区分两类表象：节流循环表现为**永久 running**（生产库中有 3 条 item 停留在 `running`，与该行为相符；但这两者是否有因果关系无法从现有数据确认——进程可能是被强杀而非卡死，此处标记为**推断**）；而 141 条 `failed` 是 403 / 连接重置，是另一条独立的失败路径（见 P1-3）。

3. **总带宽与 YouTube/CDN 侧上限。**
   这是物理上限，应用侧无法突破；但必须先把前两条修掉，否则永远测不出真实上限。

---

## 4. 修复方案（按优先级）

总体原则：**最小改动、默认不启用高风险项、全部可用环境变量回退。**

### P0-1　关闭默认节流守卫（`throttledratelimit`）

- **改动**（1 行）：`backend/app/config.py:36`

  ```python
  throttled_rate_kbps: int = Field(default=0, ge=0)   # 0 = 关闭；> 0 才写入 yt-dlp
  ```

  `ytdlp_service.py:272` 已有 `if self.throttled_rate_kbps > 0:` 守卫，**无需改调用侧**。
- **配套**：`backend/tests/test_ytdlp_service.py` 增加两条断言——默认构建出的 opts 不含 `throttledratelimit`；`YTDL_THROTTLED_RATE_KBPS=64` 时包含且等于 `65536`。
- **预期收益**：直接消除「每约 5 秒中断一次 + 重新 extract」的循环。本地对照实验：25s 内 HTTP 请求数从 10 次降到 2 次，下载连续推进。并发 5 场景下不再出现「所有流同时被判定为节流」。
- **潜在风险**：失去「检测到被 YouTube 限速（典型 ~50 KB/s）就换 URL 重试」的自愈能力。若某条流真的被限速到 64 KB/s 以下，现在会一直慢慢下完而不是重启找新 URL。
- **缓解**：保留 env 开关 `YTDL_THROTTLED_RATE_KBPS`（> 0 即恢复旧行为，建议回归值 32 而非 64），并在 `docs/technical.md` 说明适用场景（**仅建议在并发 = 1 时开启**）。
- **改动面**：1 行生产代码 + 2 条测试 + 1 处文档。不触碰任何现有行为分支。

### P0-2　停滞看门狗：让「卡住」可见、可终止

- **改动**：新增 `backend/app/stall_guard.py`（约 40 行），在 `ytdlp_service.py:374-379` 的 `guarded_hook` 中接入：

  ```python
  def guarded_hook(payload):
      if should_cancel():
          raise DownloadCancelled("Download was cancelled.")
      stall_guard.observe(payload, time.monotonic())   # 新增
      progress_hook(payload)
  ```

  规则（关键细节）：维护 `best_bytes = max(best_bytes, downloaded_bytes)` 及其达成时间；若**在 `YTDL_STALL_TIMEOUT_SECONDS`（默认 90）内 `downloaded_bytes` 始终没有超过 `best_bytes`**，抛出 `DownloadStalled`。

  之所以用「历史最大值是否刷新」而不是「本轮是否增长」：节流循环里字节是 0→56K→0→56K 反复（实测 §3.1 证据 6），本轮一直在增长，但**历史最大值从不刷新**；而正常的断点续传会在恢复后超过旧最大值。这个判据能准确区分二者。
- **异常语义**：
  - `download()`（`ytdlp_service.py:323-350`）的 `except Exception` 分支最前面加一行 `if isinstance(exc, DownloadStalled): raise`，使其**不进入下一个 profile**，直接向上抛；
  - 文案固定为 `下载停滞：N 秒内没有新增字节`（**不得包含 "timed out" / "reset" / "403" 等词**），以避免被 `is_media_stream_blocked_error()`（`ytdlp_service.py:457-484`，其中 `reset_hints` 含 "timed out"）误分类成媒体流阻塞；
  - `_run_item`（`job_manager.py:599-607`）落入通用 `except Exception` 分支 → item 标记 `failed` 并显示该文案，用户可一键重启。
- **预期收益**：任何形式的静默卡死（节流循环、403 循环、上游异常）都会在 90 秒内变成一条可读的失败原因，而不是永久 `running`。
- **潜在风险**：
  - 若 yt-dlp 完全不调用 progress hook（例如阻塞在 socket 读），看门狗无从触发 → 兜底仍是 `socket_timeout=30` 与 `retries`；这条限制要写进文档，不能声称「解决所有卡死」。
  - 极慢但仍在推进的连接理论上可能误判（90 秒零字节 ≈ 已断流，实际风险极低），阈值可配，`0` 表示关闭。
- **改动面**：1 个新模块 + 3 行接入 + 单测（用构造的 payload 序列驱动）。

### P1-1　并发语义澄清（零功能风险）

- **改动**：`frontend/src/App.tsx:1037` 标签改为（2026-10-02 重构后该标签位于 `frontend/src/components/SettingsPanel.tsx`）

  ```
  并发（同时下载的视频数；单个视频无效）
  ```

  并在单视频任务（`total_items === 1`）的任务行提示一次「单视频任务不受并发设置影响」。
- **预期收益**：消除「调了并发没变化」的误解，把用户引导到正确的加速手段（多视频批量下载）。
- **风险**：纯文案，无行为变更。需同步 `frontend/src/App.test.tsx:331` 的标签断言。

### P1-2　aria2c 多连接（可选，默认关闭）

- **现状**：aria2c 未安装；`aria2c_enabled=False`（`config.py:37`）；`_aria2c_args()`（`ytdlp_service.py:540-559`）虽已支持 `-x/-s`，但连接数来自 `aria2c_connections`，默认 **1** → 即使装上也不会加速。
- **方案**：
  1. 安装系统依赖 `aria2c`（见 §5）；
  2. 把 `aria2c_connections`（`config.py:39`，取值 1–4）暴露到设置 UI 与 `/api/diagnostics`（诊断响应已有该字段，仅前端未展示）；
  3. 默认保持 `aria2c_enabled=False`；用户显式开启后，连接数默认 **2**（保守），上限 4。
- **预期收益**：单条 HTTP 流多连接下载，是**单视频任务唯一真实的提速手段**（yt-dlp 内建 http 下载器是单连接）。CDN 条件允许时通常 2–4×。
- **潜在风险**：多连接是 YouTube 侧最敏感的触发条件，**403 / 限速概率显著上升**。因此默认关闭、连接数上限 4、且必须可一键关回。
- **改动面**：仅配置项 + UI 展示；下载代码已具备能力。

### P1-3　降低 403 失败率（稳定性主因）

- **证据**：141/213 已结束项是媒体流 403/重置（占 66%）；当前 `data/cookies.txt` 不存在、无 PO token、无代理配置。
- **方案（仅增强提示，不改下载行为）**：
  1. `/api/diagnostics` 已返回 `cookies_enabled`（`main.py:125`）；前端在 cookies 未配置时，于解析面板显示明确提示：「未配置 cookies 时 YouTube 媒体流 403 概率显著上升，建议先导入」；
  2. 任务失败原因为媒体流阻塞时，在现有文案前追加「当前 cookies 状态：未配置 / 已配置」。
- **预期收益**：把 403 从「莫名其妙失败」变成「有明确前置条件可自查」。
- **风险**：无行为变更。

### P2-1　收紧重试等待预算

- **改动**：`ytdlp_service.py:611-613`

  ```python
  return min(30.0, max(1, n) * 2.0)   →   min(10.0, max(1, n) * 2.0)
  ```

  保留 `retries=10` 不变（次数不动，只压单次等待上限）。
- **预期收益**：单 profile 的最长静默等待从约 110s（`2+4+…+20`）降到约 80s（`2+4+6+8+10+10×5`），失败更快浮出水面。
- **风险**：弱网下「多试几次也许能成」的概率略降。建议与 P0-2 看门狗一起上线，由看门狗兜底。

### P2-2　WAL 文件维护

- **证据**：`data/app.sqlite3` 26.7 MB，遗留 `app.sqlite3-wal` 4.2 MB（上次进程被强杀未 checkpoint）。
- **改动**：在 `main.py` 的 `lifespan` 启动与停止时各执行一次 `PRAGMA wal_checkpoint(TRUNCATE)`。
- **收益**：避免 WAL 无界增长拖慢读；停止时清理 sidecar。
- **风险**：极低（`TRUNCATE` 是标准维护操作）。

### P2-3　profile 重试链的 pacing（**先观测、后决定**）

- 现状：`sleep_interval_requests=1.0` 同时作用于 extract 与 download（`ytdlp_service.py:259`），5 个 profile 重试 = 5 次完整 extract 的 pacing 开销。
- 建议：**本次不做**。先由 P0-2 看门狗记录每个 profile 的耗时，拿到数据后再决定是否对「非首个 profile」降到 0.3。贸然降低 pacing 会推高 403 率，与 P1-3 目标相悖。

---

## 5. 新增依赖

| 依赖 | 类型 | 是否必需 | 理由 | 安装方式 |
| --- | --- | --- | --- | --- |
| `aria2c` | 系统二进制 | **可选**（默认关闭） | yt-dlp 内建 http 下载器为单连接，无法在单条 URL 上并行；aria2c 是当前唯一能给**单视频**提速的手段 | `winget install aria2.aria2` / `scoop install aria2` / `choco install aria2`；或手动下载加入 PATH |

**不新增任何 Python 依赖。** 另建议常规维护：`python -m pip install -U yt-dlp`（当前 2026.07.04），以获取上游对 403 / PO token 的最新修复——这属于版本升级，不是新增依赖。

---

## 6. 验证方式

### 6.1 自动回归（每个 commit 前必跑）

```powershell
python -m compileall backend\app
python -m pytest backend\tests -q
cd frontend; npm test; npm run build
git diff --check
```

### 6.2 新增单元测试

- `test_ytdlp_service.py`：默认 opts 不含 `throttledratelimit`；`YTDL_THROTTLED_RATE_KBPS=64` 时为 `65536`；
- `test_stall_guard.py`：字节增长的 payload 序列不触发；90s 零增长触发 `DownloadStalled`；
- 错误分类测试：`DownloadStalled` 的文案**不**被 `is_media_stream_blocked_error()` 命中；
- `App.test.tsx`：并发标签文案更新后的断言。

### 6.3 并发基准（回归基线，离线可跑）

```powershell
python scripts\bench_concurrency.py <temp_dir>
```

判定标准（当前实测基线，修复后不得劣化）：

```
concurrency=1: wall≈8.2s  peak=1
concurrency=2: wall≈4.3s  peak=2
concurrency=4: wall≈2.3s  peak=4
concurrency=8: wall≈1.3s  peak=8
```

`peak == concurrency` 且 `wall ≈ 8 / concurrency` → item 级并发仍线性生效，未引入串行或锁竞争。

### 6.4 节流守卫回归（离线可跑）

```powershell
python scripts\bench_throttle_guard.py <temp_dir> 64   # 关闭前：25s 内 GET 10 次，线程仍在运行
python scripts\bench_throttle_guard.py <temp_dir> 0    # 关闭后：GET 2 次，连续下载
```

判定标准：`throttled_rate_kbps=0` 时 GET 次数 ≤ 3 且 `downloaded_bytes` 单调递增（不再出现回落到 1024 的重启）。

### 6.5 真实下载验收（**必须先确认连通性**）

> ✅ 连通性已于 2026-10-01 解决：本机 WinINet 开启系统代理 `127.0.0.1:7890`，浏览器与
> `scripts/acceptance_real.py`（自动探测该代理）均可访问 YouTube；此前的「直连超时」是脚本未走代理的测量错误。
> ⚠️ 但**媒体流仍无法验收**：无 cookies 时全部 403（见 §9.1）。本节完整验收需要 `data/cookies.txt`，
> 而本机的 Edge cookies 为 v20 app-bound 加密，无法自动导入（见 §9.2-B）。

前置检查（已固化在 `scripts/acceptance_real.py` 中）：

1. `data/cookies.txt` 存在，且 `GET /api/diagnostics` 返回 `cookies_enabled=true`；
2. `GET /api/diagnostics` 中 `yt_dlp_version`、`ffmpeg`、`js_runtime`、`aria2c_available`；
3. 到 `www.youtube.com` 与 `*.googlevideo.com` 的 HTTPS 连通性（超时 10s），**必须经系统代理**；
4. 本地基准（127.0.0.1）必须设置 `NO_PROXY`，否则被 `<-loopback>` 规则拦成 502（见 §9.2-C）。

验收步骤：

1. 选一个 ≥ 20 条目的公开 playlist，取前 6 项；
2. 分别在 `concurrency = 1 / 3 / 5` 下各跑一轮（每轮开始前清空 `downloads/` 目标目录，保证无 `skip_existing` 干扰）；
3. 每轮记录（从 `/api/jobs/{id}` 与 `jobitem` 表读取）：每项字节数、耗时、平均速度、profile 重试次数、失败原因；
4. 输出 CSV 并对比。

判定标准（全部满足才算通过）：

- 5 并发总耗时 ≤ 1 并发总耗时的 **45%**（线性加速应接近 20%，考虑开销放宽到 45%）；
- 失败率 **0**，且没有任何 item 停留 `running` 超过 5 分钟；
- 修复前后对比：中断-重提取次数（可用 `jobitem.error` 与日志中 `yt-dlp profile failed` 计数衡量）显著下降。

### 6.6 效果归因（避免把网速波动当成修复效果）

- 同一 playlist、同一时段、同一 cookies 状态做 A/B（改 `YTDL_THROTTLED_RATE_KBPS` 环境变量即可回退，无需改代码）；
- 每轮至少 3 次取中位数；
- 若某轮 403 率 > 20%，该轮作废（说明网络/cookies 状态异常，不是应用问题）。

---

## 7. 回滚方案

1. **基线标记**：开始实施前对当时 HEAD 打 `git tag perf-baseline-2026-09-30`（本计划提交时 HEAD 为 `fbb9b28`，实施时以实际 HEAD 为准）。
2. **一修复一 commit**：任一修复出问题可单独 `git revert <sha>`，互不影响：
   - `perf: disable throttled rate guard by default`
   - `feat: add download stall watchdog`
   - `docs: clarify concurrency applies per video`
   - `feat: expose aria2c connection setting`
   - `perf: cap retry sleep at 10s`
   - `chore: checkpoint SQLite WAL on startup and shutdown`
3. **运行时回退（无需改代码/重新部署）**：

   | 环境变量 | 作用 |
   | --- | --- |
   | `YTDL_THROTTLED_RATE_KBPS=64` | 恢复旧的节流守卫行为 |
   | `YTDL_STALL_TIMEOUT_SECONDS=0` | 关闭停滞看门狗 |
   | `YTDL_ARIA2C_ENABLED=false` | 关闭外部下载器（保持默认） |
   | `YTDL_YOUTUBE_MAX_PARALLEL_DOWNLOADS=1` | 并发降回 1 |

4. **整体回滚**：`git revert <range>` 或 `git checkout perf-baseline-2026-09-30 -- backend/ frontend/`；`PLAN.md` 与 `scripts/bench_*.py` 保留，便于二次分析。
5. **数据面**：所有改动只涉及配置项、异常分类与文案，**不修改数据库 schema、不修改 API wire 格式**，回滚无需数据迁移。

---

## 8. 范围外（本次明确不做）

- 提高 `concurrent_fragment_downloads`（> 1）：对 YouTube DASH 单文件流无效，且 403 风险高；
- 缓存 `prepare_download` 的 `info_dict` 给 `download()`：侵入 yt-dlp 调用契约，收益（省一次 extract）小于风险；
- 单视频内部「视频流 + 音频流并行下载」：需要绕过 yt-dlp 的合并流程自行调度，改动面大，与「最小改动」原则冲突；
- 修改 `http_chunk_size=16MiB`：当前取值是 anti-403 的合理折中（§3.2 根因 3），保持现状。

### 附带发现（建议另开计划）

`data/app.sqlite3` 中存在 **332 个 `queued` + 3 个 `running`** 的历史 item：进程退出后任务不会自动重新入队，重启后表现为「任务永远排队」。这是**可靠性问题而非性能问题**，且会干扰 §6.5 的验收统计，建议单开一个计划处理（启动时把孤儿 queued/running item 重新入队或标记为 `cancelled`）。

---

## 9. 实施记录（2026-09-30）

基线：`perf-baseline-2026-09-30`（计划提交 `676c157`）。每个修复一个 commit，可单独 `git revert`。

| 项 | 改动 | commit | 回归结果 |
| --- | --- | --- | --- |
| P0-1 关闭节流守卫 | `config.py` 默认 `throttled_rate_kbps=0`、`ytdlp_service.py` `DEFAULT_THROTTLED_RATE_KBPS=0` | `c5c71b1` | `bench_throttle_guard.py /tmp 0` → 25s 内 HTTP GET **2** 次、无错误、字节单调增长（对照 `64` 仍为 10 次） |
| P0-2 停滞看门狗 | 新增 `app/stall_guard.py`，接入 `_download_once` 的 progress hook；`DownloadStalled` 不进 profile 重试链 | `dfbf215` | 新增 `test_stall_guard.py` 9 例全绿；后端 142 → 147 例全绿 |
| P1-1 并发语义澄清 | 设置标签改为「并发（同时下载的视频数；单个视频无效）」；单视频任务行追加提示 | `bddfdef` | 前端 51 → 52 例全绿，`tsc && vite build` 通过 |
| P1-2 aria2c 连接数 | 安装 aria2 1.37.0（winget 用户级）；默认连接数 2（上限 4）；新增 `/api/settings` 字段与设置面板输入 | `869a444` | 后端 144 例、前端 52 例全绿 |
| P1-3 cookies 状态提示 | 媒体流失败文案前置「当前 cookies 状态：未配置 / 已配置」；解析面板在 cookies 缺失时提示 403 风险 | `d78a894` | 后端 145 例、前端 52 例全绿 |
| P2-1 重试等待上限 | `_bounded_retry_sleep` 上限 30s → 10s | `0882ac6` | 后端全绿 |
| P2-2 WAL 维护 | 新增 `checkpoint_wal()`，lifespan 启动与停止各执行一次 `TRUNCATE` | `86fce12` | 后端 147 例全绿 |

未实施：P2-3（profile pacing 降到 0.3）——按计划「先观测后决定」，等看门狗积累数据再评估。

并发基准（修复后，未劣化）：`concurrency=1/2/4/8 → wall 8.19/4.10/2.27/1.26s，peak = 并发数`。

### 9.1 真实验收结果（2026-10-01，真实网络 + 真实 yt-dlp）

**纠正上一版结论**：「本机到 youtube.com HTTPS 直连超时」是**测量错误**。本机 WinINet 开启了系统代理
`127.0.0.1:7890`，浏览器走代理可用，而验收脚本用 `urllib` 直连所以超时。经代理实测
`https://www.youtube.com/` → HTTP 200 / 0.44s，页面、元数据、字幕全部正常。
脚本 `scripts/acceptance_real.py` 已内置 WinINet 代理自动探测（`detect_system_proxy()`）。

**媒体流仍无法验收**：无 cookies 时，媒体流在**所有客户端 / 所有格式**上一律 403。

| 验证 | 结果 |
| --- | --- |
| `format=18` / `bestaudio` / `player_client=web_safari` / `player_client=tv` | 全部 403 或「Requested format is not available」 |
| 5 个不同视频（`dQw4w9WgXcQ` 等） | 5/5 媒体流 403 |
| `--impersonate chrome/safari/firefox` | 全部失败（curl_cffi 在该环境抛 `AssertionError`） |

而 cookies 在本机**无法自动获取**（见 §9.2-B）。因此 §6.5 中「6 条目视频」的完整验收仍不可执行，
改以**同一条代码路径、同一套真实网络**的字幕 / 元数据任务做验收（`mode=subtitles_only`，`subtitles=en`）：

| 并发 | 条目 | 总耗时 | 失败 | 滞留 > 5min | 总字节 |
| --- | --- | --- | --- | --- | --- |
| 1 | 12 | 63.24s | 1（媒体流 403） | 0 | 1.39 MiB |
| 3 | 12 | 25.34s | 0 | 0 | 1.46 MiB |
| 5 | 12 | 22.09s | 0 | 0 | 1.46 MiB |

- **并发阈值判定 PASS**：5 并发 / 1 并发 = **34.93%**（阈值 45%）；加速比 2.86×。
- **稳定性 PASS**（c=3 / c=5）：0 失败、无 item 停留 running 超过 5 分钟、看门狗无误报。
- c=1 的 1 例失败是 403 媒体流（无 cookies），**与应用性能改动无关**。
- 注意口径：12 条目总负载仅 1.46 MiB，单 item 3–5s，**固定元数据开销占主导**，因此 c=3→c=5 只有
  25.3→22.1s 的边际收益。6 条目时比值仅 55%（未达阈值），12 条目才降到 35%——说明 §6.5 的 45% 阈值
  是「视频级负载」口径，小负载任务不能直接套用。

### 9.2 新发现的缺陷（均非本次性能改动引入，建议另开计划）

**A. 字幕默认全语言 → HTTP 429 → 整个 item 失败（高优先级）**
`ytdlp_service.py:710` 的 `subtitle_languages or ["all"]`：前端未显式传语言时（默认 `[]`）被解释为
**下载全部字幕轨**。实测单视频会并发拉取 20+ 条 VTT（含 `ab-ar`、`aa-ar` 等自动轨），触发
`HTTP Error 429: Too Many Requests`，进而把**已经可以成功的任务判为 failed**，且视频本体根本没下载
（产物目录里只有 20 个 `.vtt`）。建议改为空列表即「不下载字幕」，或按 `AppSettings.default_subtitle_languages`（默认 `["en"]`）兜底。

**B. Edge cookies 导入在本机结构上不可用（且 CDP 路径有副作用）**
1. Edge 的 YouTube cookies 全部是 **v20 app-bound 加密**（53/53），`extract_cookies_from_browser`
   报 `Failed to decrypt with DPAPI`，yt-dlp/browser_cookie3 无法离线解密；
2. `_extract_edge_cookies_via_cdp()`（`browser_cookies.py:157-191`）用真实 profile 启动
   `--remote-debugging-port`，但 Chromium 对默认数据目录一律拒绝：
   `DevTools remote debugging requires a non-default data directory` → **该路径在任何机器上都必然超时失败**；
3. 用目录联接（junction）绕过目录限制后端点可连，但**会把真实 cookie 库清空**（53 → 0 条），且 CDP 仍返回 0 条 cookies。
   已用快照完整恢复（2458 条 / 53 条 youtube），但**该路线不可再用于生产**。
结论：本机只能通过「浏览器扩展导出 cookies.txt」获得登录态；应用的浏览器导入功能需要重新设计
（例如改用 `--user-data-dir` 指向带副本的临时目录 + 官方支持的提取方式，或明确提示用户手动导出）。

**C. 验收脚本需显式绕过系统代理的回环拦截**
本机 `ProxyOverride` 含 `<-loopback>`，本地 HTTP 基准（`bench_throttle_guard.py`）的 127.0.0.1 请求会被代理拦成 502，
表现为「GET 计数 0」的假故障。已在脚本顶部强制设置 `NO_PROXY` 修复。

## 10. 自查表（计划审校）

| 检查项 | 结果 |
| --- | --- |
| 三个现象是否各自有独立根因与代码/实验依据 | ✅ 中断→§3.1；速度→§3.2；并发→§3.3 |
| 「并发是否真正生效」是否有直接证据 | ✅ §3.3 基准：peak = 并发数，wall 线性下降 |
| 是否逐项排除了串行 / 锁 / 线程池 / 连接池 | ✅ §3.3 表格 |
| 每个修复是否给出收益 + 风险 + 缓解 | ✅ §4 每项均含 |
| 是否优先最小改动 | ✅ P0-1 为 1 行配置改动；P0-2 为新增模块 + 3 行接入 |
| `DownloadStalled` 文案是否会与现有错误分类冲突 | ✅ §P0-2 已明确规避 "timed out"/"reset" 等关键词 |
| 停滞判据能否区分「节流循环」与「正常断点续传」 | ✅ §P0-2 采用「历史最大值是否刷新」而非「本轮是否增长」，前者会持续振荡不刷新、后者恢复后会刷新 |
| 新增依赖是否说明且默认不启用高风险项 | ✅ §5，aria2c 默认关闭 |
| 验证方式是否覆盖单元 / 离线基准 / 真实验收 | ✅ §6.1–6.5，含离线可跑的两个基准脚本 + `scripts/acceptance_real.py` |
| 真实验收是否有前置条件与判定阈值 | ✅ §6.5（含代理前置条件与量化阈值）；结果见 §9.1 |
| 真实验收结论是否与实际口径一致 | ⚠️ §9.1：媒体流因 403 未验收；字幕路径验收通过（34.93% / 0 失败），并已注明「小负载不适用 45% 阈值」 |
| 新发现缺陷是否已记录 | ✅ §9.2：字幕 429、Edge cookies 导入不可用、回环代理拦截 |
| 回滚是否可单条 / 全部 / 运行时三档 | ✅ §7 |
| 与上一轮性能计划是否冲突 | ✅ §2.4 已确认 WAL、进度节流、单次 extract、去 sleep 均已落地，本计划不重复 |
