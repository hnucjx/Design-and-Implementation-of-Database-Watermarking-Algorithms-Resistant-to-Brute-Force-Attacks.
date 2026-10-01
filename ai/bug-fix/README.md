# 修复记录（bug-fix）

本目录记录**每一处修复**：一个问题 = 一个文件 = 一个 commit。
目的是让后来的人（包括未来的我们）能回答三个问题：**当时坏在哪、为什么坏、凭什么说修好了**。

## 记录约定

每个文件按固定小节写，宁可写「未验证」也不要写猜测：

| 小节 | 要求 |
|---|---|
| 问题 | 用户能观察到的现象，不是代码现象 |
| 原因 | 具体到文件/行/函数，附**实测**证据（命令与输出），不写「可能是」 |
| 修复方案 | 改了什么、为什么这么改、为什么不做别的 |
| 效果 | 修复前后的可对照结果（数字/日志） |
| 验证 | 新增/修改的测试用例表 + 回归结果 |
| 风险与回滚 | 副作用、回滚命令；有破坏性的必须标红说明 |
| 关联 | 指向相邻缺陷，说明它们是否是同一条失败链 |

`未覆盖 / 如实说明` 小节是**必填的诚实项**：本轮没验到的地方要写清楚，
不能用「已修复」掩盖「没验证」。

## 索引

| # | 标题 | 提交 | 一句话 |
|---|---|---|---|
| [001](001-bot-check-does-not-escalate-profile.md) | bot 校验错误不触发 anti403 profile 阶梯 | `545c846` | `Sign in to confirm you're not a bot.` 既非 403 也非连接重置，被当成「换 profile 也没用」，anti403 阶梯一次都没跑过 |
| [002](002-po-token-provider-has-no-browser-path.md) | PO token provider 拿不到浏览器路径 | `f3f7e50` | 插件要求 `browser_path` 存在，配置项默认 `None` → `wpc-1.1.2 (external, unavailable)` |
| [003](003-js-runtime-invisible-outside-path.md) | JS 运行时只查 PATH | `21ebd9d` | 服务进程 PATH 里没有 node → `JS Challenge Providers: node (unavailable)` → nsig 解不出来 |
| [004](004-empty-subtitle-languages-expand-to-all.md) | 字幕语言为空退化成 `["all"]` | `a579a93` | 空列表变成「拉全部字幕轨」→ HTTP 429 → **整个条目判失败，视频本体也下不到** |
| [005](005-edge-cdp-fallback-touches-the-live-profile.md) | Edge CDP 回退挂载真实 profile | `a482a9e` | 既必然失败（Chromium 拒绝默认数据目录），又曾**清空真实 cookie 库（53 → 0）** |

## 本轮（2026-10-01）背景

起点是上一轮的性能修复与真实验收：验收时发现「部分条目整体失败，且它们根本没在下载视频」。
顺着这条线挖下去，发现 001~003 是**同一条失败链**上的三个环节——

```
提取 YouTube 页面
  ├─ 需要 JS 运行时解 nsig        ← 003（探测不到 node）
  ├─ 需要过 bot 校验              ← 001（过不了时不会去试 anti403 profile）
  └─ 需要 PO token               ← 002（provider 因缺 browser_path 而 unavailable）
```

**任何一环断掉，「下载 YouTube 视频」就整体不可用。** 三个修复必须同时生效才有意义，
所以它们虽然各自独立提交，效果要合起来看。

004 与 005 是验收过程中暴露的存量缺陷，与上面三条链无直接关系，
但都会让「下载」这件事对用户失效（一个炸 429、一个破坏用户数据）。

## 已知但**未处理**的问题（下一轮候选）

1. **应用没有代理配置项。** `ydl_opts` 里不含 `proxy`，完全依赖宿主进程的
   `HTTP_PROXY` / `HTTPS_PROXY`；而 Windows 的系统代理是 WinINet 设置，yt-dlp / curl
   **读不到**。若出口必须走代理，整条链路仍然不通。
   （排查中发现：沙箱会注入只在本机存在的 `HTTPS_PROXY=127.0.0.1:54109`，
   使子进程拿到一个不可用的代理 —— 这也是「环境变量式代理」不可靠的旁证。）
2. **`_close_browser_for_cookie_import()` 用 `taskkill /IM msedge.exe /F /T`**，
   会杀掉用户全部 Edge 进程（含未保存标签页）。虽属用户显式同意，破坏面仍过大。见 005 遗留风险。
3. **端到端仍未验收通过。** 本轮把 provider 从「不可用」推进到「可用」，
   但「PO token 真能铸出来 + 视频真能下下来」需要在干净的代理环境下单独验收。
4. 同一 item 内多条字幕轨仍是串行请求，若 YouTube 收紧限流可能还需请求间隔控制（见 004 关联）。
