# 004 · 字幕语言为空时退化成 ["all"]，触发 429 并拖垮整个条目

- 状态：已修复
- 提交：`fix(ytdlp): stop expanding empty subtitle languages to [\"all\"]`
- 影响面：`video_subtitles` / `subtitles_only` 两种模式，且请求未带 `subtitle_languages`
- 严重度：中高（**429 会让整个 item 判失败，连视频本体都不会被下载**）

## 问题

真实验收（上一轮，12 条目、字幕路径）里发现：某些条目整体失败，而它们**根本没在下载视频**。
根因是字幕语言为空时，yt-dlp 收到的 `subtitleslangs` 是 `["all"]`。

"all" 不是「默认字幕」，而是**该视频的全部字幕轨**——人工 + 自动、所有语言，
实测一个视频就有 20+ 条 VTT。密集请求立刻把 YouTube 打成 HTTP 429；
而 429 抛在整个 item 上，任务被判失败，视频本体也一起没了。

### 实测输出（修复前的探针）

```
[debug] params: {..., 'writesubtitles': True, 'writeautomaticsub': True,
                 'subtitleslangs': ['all'], ...}
...
ERROR: ... HTTP Error 429 ...
```

## 原因

`backend/app/ytdlp_service.py` 的 `_subtitle_options()`：

```python
languages = options.subtitle_languages or ["all"]
```

而 `DownloadOptions.subtitle_languages` 的默认值是**空列表**（`schemas.py:60`）：

```python
subtitle_languages: list[str] = Field(default_factory=list)
```

并且 `job_manager.py` **不会**用设置里的 `default_subtitle_languages` 去填充它
（`grep subtitle job_manager.py` 为空）。于是只要客户端不带语言字段
（直接调 API、或用脚本跑验收、或用户在 UI 里把语言全部取消勾选），
就静默落到 `["all"]`。

设置里其实一直有一个合适的默认值——`AppSettings.default_subtitle_languages = ["en"]`，
但它在下载路径上从未被用作兜底。

## 修复方案

1. 新增模块常量 `FALLBACK_SUBTITLE_LANGUAGES = ("en",)`，并写明「**不能**用 `["all"]`」的原因。
2. `YtDlpService` 新增构造参数 `default_subtitle_languages`，默认取该常量；
   新增 `_subtitle_languages(options)` 集中决定语言列表，优先级为
   **请求显式指定 → 设置里的默认 → 模块兜底**，**任何一条路径都保证非空且有界**。
3. `create_app()` 把 `app_settings.default_subtitle_languages` 透传给服务；
   `/api/settings` 的 PUT 与 `_apply_stored_settings()` 都同步给 `service`
   （与 `aria2c_connections` 的做法一致），否则改设置不会影响兜底行为。

用户如果**真的**想要 `["all"]`，显式传 `subtitle_languages: ["all"]` 仍然生效——
本修复只改「空列表」的语义，不剥夺这个能力。

## 效果

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 请求不带语言，设置默认 `["en"]` | `['all']` | `['en']` |
| 请求不带语言，设置默认 `["en","zh-Hans"]` | `['all']` | `['en','zh-Hans']` |
| 请求不带语言，设置默认也为空 | `['all']` | `['en']`（模块兜底） |
| 请求显式 `["ja"]` | `['ja']` | `['ja']`（不变） |

实测：

```
修复后 subtitleslangs = ['en']
跟随设置             = ['en', 'zh-Hans']
```

## 验证

`backend/tests/test_ytdlp_service.py` 新增 4 例：

| 用例 | 断言 |
|---|---|
| `test_empty_subtitle_languages_fall_back_to_bounded_default` | `['en']`，且 `"all" not in` 结果 |
| `test_empty_subtitle_languages_honour_configured_default` | 跟随 `default_subtitle_languages` |
| `test_empty_subtitle_languages_still_bounded_when_default_is_blank` | 默认也为空时仍回退 `['en']` |
| `test_explicit_subtitle_languages_win_over_default` | 显式值优先 |

回归：`pytest -q` → **163 passed**（本修复前 159）。

## 风险与回滚

- 风险：低。唯一的行为变化是「不带语言」的请求从「拉全部」变成「拉设置里的语言」，
  这正是绝大多数用户想要的语义。若有人依赖 `["all"]` 的旧行为，显式传 `["all"]` 即可。
- 回滚：`git revert <sha>`。

## 关联

- 该缺陷是在修复 `P0-1`（关闭节流守卫）之后、跑真实验收时暴露出来的存量问题，
  与 `001`~`003` 属于同一轮「让下载真的能跑通」的工作。
- 未在本轮处理：即便语言有界，同一 item 内多条字幕轨仍是串行请求；若 YouTube
  收紧限流，可能还需要请求间隔或按语言并发上限。当前先保证「不炸」。
