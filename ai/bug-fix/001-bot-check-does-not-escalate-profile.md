# 001 · bot 校验错误不触发 anti403 profile 阶梯

- 状态：已修复
- 提交：`fix(ytdlp): escalate to anti403 profiles on YouTube auth/bot-check errors`
- 影响面：所有走 `YtDlpService.download()` 的下载（含分析阶段的 profile 选择）
- 严重度：高（有意的 anti403 设计完全够不着，等于白装 PO token 依赖）

## 问题

YouTube 返回 `Sign in to confirm you're not a bot.` 时，下载**不会**尝试 `mweb_pot_chrome` /
`safari_hls` / `chrome_default` 这些 anti403 profile，而是在 `default` 档直接抛出。
表现为：明明装了 `yt-dlp-getpot-wpc`（PO token provider）并声明在 `pyproject.toml` 里，
却永远用不上它。

## 原因

`backend/app/ytdlp_service.py` 的 profile 循环里有一道「是否值得换 profile」的闸门：

```python
if youtube_profile == "default" and not self.is_media_stream_blocked_error(exc):
    raise
```

`is_media_stream_blocked_error()` 只认两类错误：

- `is_http_403_error()` —— 文案含 `HTTP Error 403` / `403 ... Forbidden`
- `is_connection_reset_error()` —— 连接重置 / 超时 / TLS 相关

而 `Sign in to confirm you're not a bot.` 与 playability status `LOGIN_REQUIRED`
**两条都不匹配**（它既不是 403，也不是连接问题）。于是 `default` 档抛出的异常被判定为
「换 profile 也没用」，直接 `raise`，anti403 阶梯一次都不会被执行。

判定链：`download()` → `default` 失败 → `should_try_next_profile(exc)` 为假 → `raise`。
`mweb_pot_chrome` 这个 profile 存在的唯一目的就是给 `player_client=mweb` 挂 PO token，
而它被这道闸门挡住，属于「设计了但够不着」。

## 修复方案

1. 新增 `is_youtube_auth_blocked_error(exc)`：用既有的 `COOKIE_REQUIRED_AUTH_HINTS`
   在整条异常链上匹配「提取阶段被要求登录 / 人机校验 / 年龄门槛」。
2. 新增 `should_try_next_profile(exc) = is_media_stream_blocked_error(exc) or is_youtube_auth_blocked_error(exc)`，
   并在 `download()` 里用它替换原判据。语义：**只有「换 profile 可能自愈」的失败才继续，
   其余（格式不可用、参数非法、写盘失败）依旧快速失败**。
3. 补 `COOKIE_REQUIRED_AUTH_HINTS` 里的 `"login_required"`：yt-dlp 部分路径直接透出
   playability status 原文 `LOGIN_REQUIRED`，小写化后是 `login_required`，
   与已有的 `"login required"`（带空格）并不等价。

## 效果

- bot 校验失败后，下载会依次尝试 `mweb_pot_chrome` → `safari_hls` → `chrome_default`，
  第一次让 PO token provider 真正进入执行路径。
- 非自愈类错误的行为不变（仍在 `default` 档快速失败），不会拖长失败路径。

## 验证

`backend/tests/test_ytdlp_service.py` 新增 4 例：

| 用例 | 断言 |
|---|---|
| `test_youtube_auth_blocked_detection_handles_bot_challenge` | 直引号/弯引号 bot 文案、年龄门槛、`LOGIN_REQUIRED` 均命中；`Requested format is not available.` 不命中 |
| `test_should_try_next_profile_covers_blocked_stream_and_bot_challenge` | 403 与 bot 校验为真；格式不可用、写盘失败为假 |
| `test_download_escalates_to_anti403_profile_on_bot_challenge` | 断言 profile 序列为 `["default", "mweb_pot_chrome"]` |
| `test_download_still_fails_fast_on_unrelated_error` | 断言 `attempts == ["default"]`（未回归为「什么都重试」） |

回归：`pytest -q` → **151 passed**（修复前 147）。

## 风险与回滚

- 风险：低。只放宽了「何时换 profile」的判据，未改动单次下载的行为。
  代价是 bot 校验场景下多跑 1~3 个 profile，失败耗时略增（已被 profile 级
  retry sleep 上限 10s 与停滞看门狗约束）。
- 回滚：`git revert <sha>`。

## 关联

- 依赖同一套机制的下一个缺陷见 `002`（PO token provider 拿不到浏览器路径，
  即使走到 `mweb_pot_chrome` 也仍不可用）。
