# 009 - 回收兼容代理：`ytdlp_service.py` 的 15 个转发层

| 项 | 值 |
| --- | --- |
| 计划时间 | 2026-10-02 17:44 +08:00 |
| 实施时间 | 2026-10-02 17:44 ~ 17:53 +08:00 |
| 依据 | [refactor.md](refactor.md) §4.1 第 6 项「`ytdlp_service.py` 里约 20 个上一轮留下的兼容代理方法」 |
| 起点 commit | `f270e7a`（R8 提交后） |
| 本轮提交 | `e98abda`（见文末「提交与回滚」） |
| 结论 | **完成**。删掉 15 个「只有一条转发语句」的私有方法，抽掉一层无逻辑间接；cookies 三处注入接缝从「service 的转发方法」搬到实现所在的 `browser_cookies`；`ytdlp_service.py` 1182 → **1130 行**，方法数 72 → **57** |

---

## 1. 计划

### 1.1 目标

[refactor.md §4.1](refactor.md#41-后续候选清单登记为本轮三轮不做此后逐项单独一轮) 第 6 项：

> **`ytdlp_service.py` 里约 20 个上一轮留下的兼容代理方法**（`_format_selector` 等），
> 现在只剩「保持测试与调用点不破」的作用，可评估回收。

「约 20 个」是上一轮凭印象写的。本轮**先把它变成一个可执行的判据**（§2.1），再按判据清单处置。

### 1.2 边界：本轮**不做**的事

1. **不动公开门面方法**：`import_browser_cookies` / `suggest_lower_resolution` / `has_resolution_at_or_above`
   也是「一句话转发」，但它们是 service 对外的能力入口（调用方是 `job_manager` / 路由 / 测试），
   删掉等于把调用方直接绑到 L5 纯函数上 —— 那是**另一个设计决定**，不是「回收遗留」。
2. **不动 `_proxy_options`**：它转发到 `self.proxy_resolution().to_ydl_options()`，是**组合**不是代理（§2.1）。
3. **不改任何对外行为**：HTTP 形状、DB 语义、环境变量、下载行为、用户文案全部保持（§4.2 取证）。
4. **不放宽测试**：改测试只是把注入点从「service 的私有方法」搬到「实现所在的模块」（§2.3），
   断言一条没减、覆盖的分支一个没少。
5. **不新增依赖**（本轮一行 pip / npm 都没动）。

### 1.3 风险与对策

| 风险 | 对策 |
| --- | --- |
| 「删掉的东西看起来没用，其实被测试当注入点用」 | 动手前先 grep 全部调用点（含 `backend/tests`），把「0 调用点」与「被测试注入」分开处置（§2.2 / §2.3） |
| 等价性只靠嘴说 | **三组机械取证**：函数体是否只有一条转发语句（§4.2）、三处接缝是否转发到同一实现（§4.3）、目标名字不存在时 `raising=False` 会不会静默（§4.4） |
| 删行导致文档锚点漂移 | 改完立刻跑 `check_doc_anchors.py --fix`，32 处一并重算（§4.5） |
| AST 判据自己写错 → 漏收或误收 | 探针首版真的漏了 `@staticmethod` 形态（§5 第 1 条），改为「按形参名去 `self`」后重测 |

---

## 2. 实施方案

### 2.1 判据：什么算「兼容代理」

用 AST 判定（探针 `tmp_acceptance/probe_forwards.py`，跑完即删）：函数体**只有一条语句**、且它是
`return f(...)` / `f(...)`，实参与形参**逐一对应、原样转交**（没有 `*args` / `**kwargs` 展开，没有常量）。

扫出 **19 个**，其中：

| 类别 | 数 | 处置 | 理由 |
| --- | --- | --- | --- |
| 转发到**模块级纯函数**的私有方法 | 15 | **删除 + 内联调用点** | 转发层没有逻辑，多一层就多一个「改坏它」的地方 |
| 公开门面方法（`import_browser_cookies` / `suggest_lower_resolution` / `has_resolution_at_or_above`） | 3 | **保留** | 它们是 service 的对外能力；「名字在 service 上」本身就是契约 |
| 组合式小工具（`_proxy_options` = `self.proxy_resolution().to_ydl_options()`） | 1 | **保留** | 转发的对象是**本类另一次调用的结果**，不是别处的实现 —— 这是组合，删了等于把这个组合散到两个调用点 |

### 2.2 被删的 15 个

| 方法 | 原来的转发目标 | 调用点 | 处置 |
| --- | --- | --- | --- |
| `_extract_browser_cookie_jar` | `extract_cookies_from_browser`（yt-dlp） | 只作为 `BrowserCookieImporter` 的构造参数（1 处） | 删，接缝搬到 `browser_cookies`（§2.3） |
| `_close_browser_for_cookie_import` | `BrowserCookieImporter()._close_browser_for_cookie_import`（**绕圈**） | 同上 | 同上 |
| `_extract_edge_cookies_via_cdp` | `BrowserCookieImporter()._extract_edge_cookies_via_cdp`（**绕圈**） | 同上 | 同上 |
| `_resolution_from_info_dict` | `ytdlp_formats.resolution_from_info_dict` | `prepare_download`、`resolution_from_progress_payload` | 内联 |
| `_actual_format_from_info_dict` | `ytdlp_formats.actual_format_from_info_dict` | `prepare_download`、`actual_format_from_progress_payload` | 内联 |
| `_filesize_from_info_dict` | `ytdlp_formats.filesize_from_info_dict` | `prepare_download` + **测试直接调用 1 处** | 内联（测试改调模块函数） |
| `_exception_chain` | `error_advice.exception_chain` | **6 处，且全是 `YtDlpService._exception_chain(exc)` 这种类级调用** | 内联为 `exception_chain(exc)` |
| `_format_selector` | `ytdlp_formats.format_selector` | `build_download_options` | 内联 |
| `_single_file_format_selector` | `ytdlp_formats.single_file_format_selector` | **0 处（死代码）** | 删 |
| `_requires_ffmpeg` | `ytdlp_formats.requires_ffmpeg` | `build_download_options` | 内联 |
| `_detect_chromium_executable` | 本模块的 `detect_chromium_executable` | `_po_token_browser_path` + **测试注入 3 处** | 内联（注入点改为模块级函数） |
| `_resolution_from_mapping` | `ytdlp_formats.resolution_from_mapping` | **0 处（死代码）** | 删 |
| `_positive_int` | `ytdlp_formats.positive_int` | **0 处（死代码）** | 删 |
| `_short_codec` | `ytdlp_formats.short_codec` | **0 处（死代码）** | 删 |
| `_resolution_height` | `ytdlp_formats.resolution_height`（`@staticmethod`） | **0 处（死代码）** | 删 |

清理后 `from .ytdlp_formats import ...` 少 5 个名字（`positive_int` / `resolution_from_mapping` /
`resolution_height` / `short_codec` / `single_file_format_selector`）；
`from yt_dlp.cookies import ...` **整行删除**（`YoutubeDLCookieJar` 与 `extract_cookies_from_browser`
都只被删掉的三个方法用）。

### 2.3 cookies 三处接缝：不能直接删，只能搬家

动手前 grep 发现：**测试是靠它们注入的** —— 7 个 cookie 用例用
`monkeypatch.setattr("app.ytdlp_service.extract_cookies_from_browser", fake_extract)` 驱动整个导入流程。
这三个方法不是「没人用的垃圾」，而是**刻意的可注入接缝**。直接删会让 7 个用例静默失效
（`raising=False` 会掩盖这一点，见 §4.4）。

正确的收法是**把接缝搬到实现所在的模块**，让 `BrowserCookieImporter` 用自己的默认实现：

```python
# 原来：把本类的三个转发方法塞回 importer；而 importer 的默认值就是它自己的同名方法
BrowserCookieImporter(
    candidates=AUTO_BROWSER_COOKIE_CANDIDATES,
    extract_browser_cookie_jar=self._extract_browser_cookie_jar,          # → importer 自己的实现
    close_browser_for_cookie_import=self._close_browser_for_cookie_import,  # → importer 自己的实现
    extract_edge_cookies_via_cdp=self._extract_edge_cookies_via_cdp,      # → importer 自己的实现
)

# 现在：用默认实现即可，接缝在 browser_cookies 里
BrowserCookieImporter(candidates=AUTO_BROWSER_COOKIE_CANDIDATES)
```

注入点的迁移（测试侧，一一对应，覆盖的分支不变）：

| 旧注入点 | 新注入点 | 处数 |
| --- | --- | --- |
| `"app.ytdlp_service.extract_cookies_from_browser"` | `"app.browser_cookies.extract_cookies_from_browser"` | 7 |
| `monkeypatch.setattr(service, "_close_browser_for_cookie_import", …)` | `"app.browser_cookies.BrowserCookieImporter._close_browser_for_cookie_import"` | 1 |
| `monkeypatch.setattr(service, "_extract_edge_cookies_via_cdp", …)` | `"app.browser_cookies.BrowserCookieImporter._extract_edge_cookies_via_cdp"` | 1 |
| `service._extract_edge_cookies_via_cdp()`（直接调用） | `BrowserCookieImporter()._extract_edge_cookies_via_cdp()` | 1 |
| `monkeypatch.setattr(service, "_detect_chromium_executable", …)` | `monkeypatch.setattr(ytdlp_service, "detect_chromium_executable", …)` | 3 |
| `service._filesize_from_info_dict(…)` | `ytdlp_service.filesize_from_info_dict(…)` | 1 |

两处 patch 打到**类**上是必须的：`BrowserCookieImporter.__init__` 会把接缝
**绑定到实例**（`close_browser_for_cookie_import or self._close_browser_for_cookie_import`），
而实例是在 `service.import_browser_cookies()` 调用时才 new 的 —— 测试的 patch 发生在调用之前，
所以类属性替换会被构造时读到。这一条写进了测试的注释里，避免后人「顺手」改成 patch 实例。

### 2.4 顺带修掉的一个隐患：`raising=False`

那 7 处 patch 原来都带 `raising=False`。它的语义是「目标名字不存在也不报错」——
也就是说，**如果注入点改名了，patch 会静默装在一个没人读的名字上，测试仍然"通过"（只是没测到东西）**。
本轮的目标模块确实有这个名字，所以 `raising=False` 已无必要，一并去掉（§4.4 有演示）。

---

## 3. 实施情况

| 指标 | 值 |
| --- | --- |
| `backend/app/ytdlp_service.py` | 1182 → **1130 行**（−52）；方法数 72 → **57**（正好 −15） |
| `backend/tests/test_ytdlp_service.py` | 1342 → 1353 行（注入点展开 + 1 个 import） |
| 删除的 import | `from yt_dlp.cookies import …`（整行）、`ytdlp_formats` 里的 5 个名字 |
| 文档锚点 | 32 处重算（全因 `ytdlp_service.py` 行号下移） |
| 应用行为改动 | **零**（§4.2 / §4.3 取证） |
| 新增依赖 | **零** |

残留引用检查：全仓库 grep 被删的 15 个名字，**在 `backend/app` 里 0 处残留**
（`browser_cookies.py` 与 `download_progress.py` 里的同名方法是它们**自己的**实现，不是被删的代理）。

---

## 4. 验证

### 4.1 门槛（[refactor.md §5](refactor.md#5-每轮的固定流程dod) 第 3 步）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 后端测试 | `pytest backend/tests -q --basetemp=tmp_pytest/run400` | **354 passed**（一次通过，未改断言） |
| 前端测试 | `npx vitest run --environment jsdom` | **69 passed**（未改动，确认未受影响） |
| 代码锚点 | `python scripts/check_doc_anchors.py` | 首跑 **32 处漂移**（预期）→ `--fix` 重算 → 复跑**无漂移** |
| 分层校验 | `python scripts/check_layers.py` | **EXIT=0**（34 已登记 / 40 实际） |
| 契约校验 | `python scripts/check_api_contract.py` | **EXIT=0**（types.ts 15 接口 + openapi.yaml 48 条目） |
| 文档链接与 UML | `python scripts/docs.py check` | 通过 |
| 空白错误 | `git diff --check` | 无输出 |

### 4.2 取证一：被删的方法**只有一条转发语句**（无分支 / 无计算 / 无 IO）

探针从 `git show HEAD:backend/app/ytdlp_service.py` 取出每个方法的 AST 体，逐条断言：

```text
  OK _extract_browser_cookie_jar        语句数=1  已删除=True  return extract_cookies_from_browser(browser)
  OK _close_browser_for_cookie_import   语句数=1  已删除=True  BrowserCookieImporter()._close_browser_for_cookie_import(browser)
  OK _extract_edge_cookies_via_cdp      语句数=1  已删除=True  return BrowserCookieImporter()._extract_edge_cookies_via_cdp()
  OK _resolution_from_info_dict         语句数=1  已删除=True  return resolution_from_info_dict(info)
  ...
  OK _resolution_height                 语句数=1  已删除=True  return resolution_height(resolution)
```

15/15 通过（`语句数=1`、`已删除=True`）。**转发层不含逻辑 ⇒ 内联不改变行为** —— 这是「等价」的
结构性理由，而不是「我读了一遍觉得一样」。

### 4.3 取证二：cookies 三处接缝转发到的**就是** importer 自己的实现

```text
  OK _extract_browser_cookie_jar
       旧转发体 : return extract_cookies_from_browser(browser)
       importer 实现体 : return extract_cookies_from_browser(browser)      ← 逐字相同
  OK _close_browser_for_cookie_import
       旧转发体 : BrowserCookieImporter()._close_browser_for_cookie_import(browser)   ← 调的就是它自己
  OK _extract_edge_cookies_via_cdp
       旧转发体 : return BrowserCookieImporter()._extract_edge_cookies_via_cdp()      ← 同上
```

即：旧代码是「new 一个 importer，再调它的私有方法」，而 importer 的默认值本来就是那个方法。
删掉后 importer 直接用默认实现 —— 到达的是**同一个函数对象**。

### 4.4 取证三：`raising=False` 确实会掩盖注入点失效

```text
  默认（raising=True）：AttributeError -> 'module' object at app.ytdlp_service has no attribute 'extract_cookies_from_browser'
  raising=False      ：静默通过 —— patch 看起来装上了，其实没有调用方会读它
```

（该名字在本轮之后确实不存在了 —— 测试若还写旧目标，去掉 `raising=False` 会当场报错，
而不是「7 个用例都绿、但其实一个字节都没被假实现过」。）

### 4.5 与 HEAD 的行为等价性

本轮**没有做** R4 那种「12 场景 × 3 入口逐字段对照」，理由是形态不同：R4 处理的是**两条各自
持有的实现**（两处都在计算同一个量），必须逐字段证明它们给出同一结果；本轮处理的是**转发层**
（自己不计算，只把参数原样递出去），等价性的判据是「转发层无逻辑」（§4.2）+「到达同一实现」（§4.3）
+「354 个用例全过」。**这个差别是判据差异，不是宽严差异** —— 写在这里避免后人误以为漏做了。

---

## 5. 未覆盖 / 如实说明

1. **探针自身先错了一次。** 首版判据里计算「形参名」时写的是
   `[a.arg for a in fn.args.args if a.arg != "self"][: len(fn.args.args) - 1]` ——
   对 `@staticmethod` 会**多切掉一个参数**，于是 `_resolution_height` 这类静态转发**整个漏掉**
   （首轮报 15 个，修正后 19 个）。教训：**「工具说没有」和「真的没有」是两件事**，
   而这一条恰好是本仓库 §3 第 6 条的老朋友。修正后探针输出的 19 = 15 删 + 3 公开门面 + 1 组合。
2. **`_proxy_options` 是刻意保留的。** 它出现在探针的 19 个名单里，但转发的是
   `self.proxy_resolution().to_ydl_options()`（本类另一次调用的结果）—— 那是组合。若后人也按
   「名字带下划线 + 一句话」来收，会误删它。**判据是「转发到别处的实现」而不是「方法很短」。**
3. **3 个公开门面方法仍是转发**（`import_browser_cookies` / `suggest_lower_resolution` /
   `has_resolution_at_or_above`）。它们不是「上一轮留下的兼容代理」，而是 service 的能力入口。
   若将来要收，属于「service 还该不该暴露这些能力」的设计决定，且要同步改 `job_manager` 等调用点。
4. **新发现（本轮未处理）：`BrowserCookieImporter.__init__` 的三个可注入参数现在没有生产调用方传参了。**
   收掉 service 的转发后，全仓库只剩 2 处 `BrowserCookieImporter(...)`（生产 1、测试 1），
   且都不传那三个参数 —— 也就是说 `extract_browser_cookie_jar=` / `close_browser_for_cookie_import=` /
   `extract_edge_cookies_via_cdp=` 三个分支**永远不会被走到**（测试改为 patch 类方法，走的是默认值）。
   它们是「保留的扩展点」，删不删是 `browser_cookies.py` 的设计问题，与「回收 ytdlp_service 的代理」
   不是同一件事，**故意不顺手改**。已登记进 [refactor.md §4.1](refactor.md#41-后续候选清单登记为本轮三轮不做此后逐项单独一轮) 作为新候选。
5. **`_filesize_from_info_dict` 的测试调用点改成了 `ytdlp_service.filesize_from_info_dict`。**
   即测试仍然「借 `ytdlp_service` 模块的名字」去调 `ytdlp_formats` 的函数，而不是直接
   `from app.ytdlp_formats import filesize_from_info_dict`。理由是与该文件既有风格一致
   （它已经用 `ytdlp_service.shutil` / `ytdlp_service.detect_chromium_executable` 这种写法）；
   代价是这层间接仍在测试里保留。
6. **`test_edge_cdp_fallback_never_launches_a_browser` 里现在有一句不带赋值的 `YtDlpService(...)`。**
   它的作用是「先把服务构造完再打桩」（构造时会合法地跑一次 `node --version`），
   注释里写明了；但读起来像笔误，若后人删掉它，测试不会失败（因为现在打桩的是另一个模块）。
7. **删掉的 5 个死代码方法（0 调用点）本来就不被任何测试覆盖**，删除不减少覆盖到的分支；
   同时也说明「它们在 HEAD 上就已经是死代码」这件事，在之前几轮里没有被发现 ——
   因为它不报错、不影响行为，只是没人用。
8. **本轮的「调用点」判定是静态的**（grep + AST）。若某处用 `getattr(service, "_format_selector")`
   这类动态方式调用，会漏判。全仓库 grep 过 `getattr(service` / `setattr(service` 没有这类用法，
   但这是「当前没有」，不是「不可能有」。

---

## 6. 风险与回滚

| 项 | 值 |
| --- | --- |
| 风险等级 | 中低（动了下载服务的内部结构；但转发层无逻辑、且 354 个用例覆盖了全部调用点） |
| 回滚命令 | `git revert <hash>` |
| 回滚影响 | 无。恢复 15 个转发方法与旧的注入点；`browser_cookies.py` 一行未动 |

---

## 7. 关联

- **上游**：[refactor.md](refactor.md) §4.1 第 6 项。
- **与 [008](008-split-app-tests.md) 的关系**：008 是前端测试拆分，009 是后端内部层回收；
  两者共用同一条教训 —— 「判据要能自己抓到东西，而不是只跟着改动跑」。
- **文档**：`docs/*.md` 里 9 个文件的 32 处代码锚点因 `ytdlp_service.py` 行号下移而重算
  （`check_doc_anchors.py --fix` 自动完成，与本章改动同一次提交）。
- **下游候选**：§5 第 4 条已登记为 [§4.1](refactor.md#41-后续候选清单登记为本轮三轮不做此后逐项单独一轮) 的新一项。

---

## 8. 提交与回滚

| 项 | 值 |
| --- | --- |
| 提交 hash | `e98abda`（2026-10-02 收尾一轮回填） |
| 回滚命令 | `git revert e98abda` |
| 记录约定 | 与 [bug-fix](../bug-fix/README.md) / [ui](../ui/README.md) 一致：下次触碰 `ai/refactor/` 时回填 hash |
