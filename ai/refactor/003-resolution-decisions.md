# 003 - 降级决策纯化：从「跑一次下载才知道」到「可直接单测」

| 项 | 值 |
| --- | --- |
| 计划时间 | 2026-10-02 16:41 +08:00 |
| 实施时间 | 2026-10-02 16:41 ~ 16:55 +08:00 |
| 依据 | [refactor.md](refactor.md) §4 路线图 R3、§3 判定准则、§5 DoD |
| 起点 commit | `046e4de`（R2 提交后） |
| 本轮提交 | 待回填（见文末「提交与回滚」） |
| 结论 | **完成**。把 `job_manager.py` 里散落的降级**判定**抽成纯模块 `resolution_decisions.py`；后端 **320 passed**（+31）、前端 69 passed、`tsc` 退出码 0；**8 个降级场景在 HEAD 与重构后的终态输出逐字段相同**；用户可见中文串 197 → 197（丢失 0 / 新增 0） |

---

## 1. 计划

### 1.1 目标

把 `job_manager.py` 里「探测不可选之后怎么办」的**判定**抽成纯函数模块，使 `JobManager`
只剩 IO（探测、取元数据、写状态、提交）与时序。

判据（[refactor.md §3](refactor.md#3-判定准则) 第 1 条「单一变化原因」+ 第 6 条「行为不变必须给出
可执行对照取证」）：**降级是本项目历史上唯一反复出缺陷的策略，它的判据必须能脱离整条下载链路被验证。**

现状（起点 `046e4de`，`job_manager.py` **1174 行**）：降级的「候选计算 / 原因 / 用户文案」
散在 9 个方法里，其中 `_prepare_download` 一个方法同时承担了探测、取元数据、算候选、定原因、
写状态、提交六件事；而「这条判定对不对」过去只能靠跑一次真实下载才能验。

### 1.2 边界：本轮**不做**的事

1. **不改任何判定结果**（[refactor.md §3.1](refactor.md#31-目标与非目标) 第 5 条）：四种降级原因
   的触发条件、每种条件下降到哪个清晰度、两句失败文案的用词，全部原样。
2. **不改 IO 次数与时序**：`extract_metadata` 的调用时机与次数不变（早退条件逐字保留）。
3. **不动 `schemas.py`**（线上契约）与 **`ytdlp_formats` / `fallback_policy`**：
   本轮只是「谁来组装这些纯函数」变了，不是「纯函数本身」变了。
4. **不碰路由层与前端**（R2/R1 的范围）。
5. **不重建「产物路径两条链」的收敛**（[refactor.md §4.1](refactor.md#41-明确列为后续候选的本轮三轮不做) 第 1 条）。
6. **不引入新依赖、不改 `pytest` 配置**。

### 1.3 风险与对策

| 风险 | 对策 |
| --- | --- |
| 搬迁时把某个分支的判据改掉（**这是本轮最大的风险**：判定散在条件里，读起来都"差不多"） | **差分取证**：把同一组场景分别跑在 HEAD 树与重构后的树上，逐字段比对终态输出（见 §4.2）。不是"重新推理一遍旧逻辑"，是真的把旧代码跑起来 |
| 文案在搬迁中被顺手改了一句 | AST 级取证：`backend/app/**` 里全部**含中文的字符串常量**（排除 docstring）做多重集合比对，要求丢失 0 / 新增 0 |
| 抽纯函数时把 `None`（没取到元数据）与 `[]`（没有格式）混成一个 | 类型上分开；**并如实承认当前两者结论相同**，用一条测试把这份等价性钉住，见 §5 第 1 条 |
| 抽取后 `job_manager` 里留下没人用的导入 / 常量 | 抽完立刻 grep 逐个确认归零（`fallback_policy` 四个常量、`MIN_AUTO_FALLBACK_HEIGHT` 均已不再被 `job_manager` 使用） |
| 行号位移导致文档锚点成片失效 | `check_doc_anchors.py --fix` 重算符号名锚点（33 处），非符号名的 4 处手工核对行内容后改写 |

---

## 2. 实施方案

### 2.1 新模块：`backend/app/resolution_decisions.py`（L3 领域，247 行）

只做四件事，全是纯函数，全部可用 0.1 秒量级的单测覆盖：

| 导出 | 回答的问题 |
| --- | --- |
| `should_look_for_fallback(options)` | 这种情况**值不值得**再取一次元数据去找降级候选？（`format_id` 已指定、`resolution` 解析不出高度 → 不值得） |
| `decide_probe_fallback(requested, formats)` | 预检确认目标清晰度选不出组合时：降到哪、为什么、降不了时报哪句话 |
| `decide_media_stream_fallback(requested, formats)` | 媒体流 403 / 连接重置后：要不要标注可重启的清晰度（**允许**降到 720p 以下，**不改** `item.error`） |
| `decide_unavailable_format_fallback(requested, formats)` | `Requested format is not available` 失败后：要不要标注（**不允许**跌破 720p，**要**改写 `item.error`） |

配套：`ResolutionDecisionKind`（`skip` / `fallback` / `fail` 三态）、`ResolutionDecision`
（`frozen` dataclass）、以及四句文案的纯函数（`resolution_fallback_error_message` /
`no_supported_fallback_message` / `unselectable_resolution_message` /
`media_stream_failure_message(cookie_state)`）。

**依赖方向**：L3 → L1（`schemas`）+ L3（`ytdlp_formats` / `fallback_policy`）。不导入 yt-dlp，
不导入 `job_manager`。`MIN_AUTO_FALLBACK_HEIGHT` 以 `ytdlp_formats.DEFAULT_MIN_AUTO_FALLBACK_HEIGHT`
的别名形式暴露，避免新模块自己再写一遍 720。

**为什么把"值不值得找"也放进纯模块**：它是降级策略的一部分，但必须是纯判断 —— 否则调用方
就没法在取元数据**之前**问这一句，而"先取再判断"会白付一次网络开销（那正是重构前早退条件守住的东西）。

### 2.2 `job_manager.py` 的对应改动（1174 → 1142 行）

| 旧（都在 `job_manager.py`） | 新 |
| --- | --- |
| `_prepare_download` 里的内联判定（约 40 行） | `should_look_for_fallback` + `decide_probe_fallback`，调用方只剩「探测 → 取元数据 → 按决策写状态」 |
| `_prepare_download` 里内联的 `try: extract_metadata …` | `_extract_formats_or_none(item)`（IO 单独一个方法，返回 `None` 而不是 `[]`） |
| `_fallback_resolution_for_item`（取元数据 + 算候选，两件事混在一起） | 拆成 `_extract_formats_or_none`（IO）+ 两条 `decide_*_fallback`（判定） |
| `_annotate_media_stream_fallback` 里的判定 | `decide_media_stream_fallback` |
| `_annotate_resolution_fallback` 里的判定 | `decide_unavailable_format_fallback` |
| `_resolution_fallback_message` / `_no_supported_fallback_message` / `_unselectable_resolution_message` | 同名纯函数，整段搬进新模块 |
| `_media_stream_failure_message`（查 cookies 文件 + 组文案） | 保留一个壳方法查文件系统，文案由 `media_stream_failure_message(cookie_state)` 产出 |
| `_set_resolution_fallback(item, requested, fallback, reason)` | `_set_resolution_fallback(item, decision, requested)`：直接接受决策，少一次手工拆字段 |

改完 `job_manager.py` 不再导入 `fallback_policy` 的任何常量，也不再导入 `MIN_AUTO_FALLBACK_HEIGHT`
（逐个 grep 确认过，没有残留）。

### 2.3 新增 `backend/tests/test_resolution_decisions.py`（31 例）

这一轮**该**加测试 —— [refactor.md §3.1](refactor.md#31-目标与非目标) 第 7 条「只做结构，新增测试
仅用于给新抽出的纯逻辑兜底」说的正是这种情形（R1/R2 没有新纯逻辑，因此 0 新增用例）。

覆盖：四种降级原因各自的输入条件、`best` / 非法 `resolution` 的早退、`height` 为空的格式不参与
候选计算、"取最高的安全档"、两句失败文案不可互换、`media_stream` 不改写错误文案、
`unavailable_format` 不许跌破 720，以及两条**接缝用例**：

- 每个 `reason` 常量都能被 `fallback_policy.build_resolution_fallback` 翻成非空文案
  （防「加了常量忘了加文案表分支」）；
- 四个 `reason` 的「文案 + 重启建议」组合两两不同。

---

## 3. 实施情况

| 指标 | 重构前 | 重构后 |
| --- | --- | --- |
| `backend/app/job_manager.py` | **1174** | **1142**（−32） |
| `backend/app/resolution_decisions.py` | — | **247**（新增） |
| `backend/app/*.py` 模块数 | 32 | **33** |
| `job_manager` 里降级相关的私有方法 | **9** | **4**（其中 2 个是 IO / 写状态） |
| 后端测试用例 | 289 | **320**（+31，全在新模块） |
| 前端测试用例 | 69 | 69（未动） |

`job_manager` 剩下的 4 个降级相关方法：`_extract_formats_or_none`（IO）、
`_set_resolution_fallback`（写状态）、`_annotate_media_stream_fallback` /
`_annotate_resolution_fallback`（两个调用点：守卫 + 调纯函数 + 写状态）、
`_media_stream_failure_message`（查 cookies 文件后转调纯函数）。

---

## 4. 验证

### 4.1 门槛（[refactor.md §5](refactor.md#5-每轮的固定流程dod) 第 3 步）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 后端测试 | `./.venv/Scripts/python.exe -m pytest backend/tests -q -p no:cacheprovider --basetemp=tmp_pytest/run206` | **320 passed** |
| 新模块单测 | `./.venv/Scripts/python.exe -m pytest backend/tests/test_resolution_decisions.py -q …` | **31 passed**（0.12 s） |
| 前端测试 | `cd frontend && npx vitest run --environment jsdom` | **69 passed** |
| 类型检查 | `cd frontend && npx tsc --noEmit` | 退出码 0 |
| 文档锚点 | `python scripts/check_doc_anchors.py` | **没有漂移**（`--fix` 重算 33 处；另有非符号名 4 处手工核对） |
| 文档链接 + UML | `python scripts/docs.py check` | 通过 |
| 空白检查 | `git diff --check` | 干净 |

### 4.2 「行为不变」的取证之一：HEAD 与重构后的 **差分**（本轮重点）

脚本：`tmp_acceptance/refactor_r3_differential.py`（一次性，gitignore，不进 CI）。

做法不是"把旧逻辑重写一遍再比"，而是**真的把旧代码跑起来**：

1. `git worktree add --detach tmp_acceptance/r3-head c385646` 取出起点树；
2. 同一份场景脚本以子进程方式在**两棵树各自**的 `backend` 包上执行
   （`PYTHONPATH=<tree>/backend` + `cwd=<tree>`，脚本里打印 `app.job_manager.__file__` 自证解析到了哪棵树）；
3. 场景用两棵树共有的 `backend/tests/fakes.py`（本轮未改），每个场景 =
   `create_app(settings, ytdlp_service=<fake>)` + `POST /api/jobs` + 等终态；
4. 比对任务与条目的 9 个字段：`status` / `error` / `requested_resolution` /
   `fallback_resolution` / `fallback_reason` / `resolution_fallback` / `actual_height` /
   `actual_format` / `progress`。

结果：**8 个场景全部 OK**，逐字段相同。

| 场景 | 终态 | 请求 | 降级到 | reason | error |
| --- | --- | --- | --- | --- | --- |
| `probe-returns-unselectable`（探针返回 `is_selectable=False`） | succeeded | 1080p | 720p | `requested_resolution_missing` | — |
| `probe-raises-unavailable`（探针抛 `Requested format is not available`，**010 的形态**） | succeeded | 1080p | 720p | `requested_resolution_missing` | — |
| `source-below-720-only` | succeeded | 1080p | 360p | `source_below_720_only` | — |
| `unselectable-high-with-safe-lower` | succeeded | 1080p | 720p | `requested_resolution_unselectable` | — |
| `unselectable-high-without-safe-lower` | failed | — | — | — | `检测到 1080p 清晰度，但该清晰度当前没有可下载的视频/音频组合…` |
| `media-stream-blocked` | failed | 1080p | 720p | `media_stream_blocked` | `当前 cookies 状态：未配置。…` |
| `http403-no-cookies` | failed | 1080p | 720p | `media_stream_blocked` | `当前 cookies 状态：未配置。…` |
| `http403-with-cookies` | failed | 1080p | 720p | `media_stream_blocked` | `当前 cookies 状态：已配置。…` |

四种 reason 全部出现；两句失败文案各出现一次；探针的两种失败表现（返回 / 抛）各出现一次。

### 4.3 「行为不变」的取证之二：用户可见字符串的整树多重集合

脚本：`tmp_acceptance/refactor_r3_strings.py`。用 `ast` 取出两棵树里**含中文的字符串常量**
（排除 docstring，因为本轮新增了注释与 docstring —— 那是给人看的，不是给用户看的），做多重集合比对。

```
HEAD (c385646) 用户可见中文串：197 条（去重后 176 种）
当前                用户可见中文串：197 条（去重后 176 种）
丢失 0 条 / 新增 0 条
```

为什么不用 `git diff` 看：字符串在重构里**换了文件**（`job_manager.py` → `resolution_decisions.py`），
diff 会显示成"删了一段、加了一段"，看不出到底是"搬家"还是"改写"。

### 4.4 同步的文档

| 文件 | 改了什么 |
| --- | --- |
| `docs/design.md` | 模块矩阵新增 `resolution_decisions.py` 一行；`job_manager.py` 的「不负责」补"不做降级判定"；`fallback_policy.py` 的「不负责」改为"决策在 `resolution_decisions`"；扩展点表「新增一个降级原因」改写；错误分类表前导句补判定归属 |
| `docs/technical.md` | 「分辨率降级原因」一节说明常量与决策分家、并指出判定位置与它的可测性 |
| `docs/implementation.md` | 「清晰度与降级」整节改写（新增模块、`_extract_formats_or_none`、`None` vs `[]` 的如实说明） |
| `docs/architecture.md` | 组件关系的后端模块枚举：「降级策略」→「降级决策与降级文案」 |
| `docs/4-plus-1-view.md` | 逻辑视图与开发视图两处补 `resolution_decisions.py` |
| `docs/requirements.md` | FR-6 的代码依据补 `resolution_decisions.py` |
| `docs/testing.md` | 基线 289 → **320**；测试文件表新增 `test_resolution_decisions.py` 一行 |
| `docs/api.md` | `openapi.yaml` 的编写来源由 `main.py` 改为 `routers/`（R2 遗留） |
| `docs/safety-review.md` | 4 处 `job_manager.py` 锚点手工核对（`L53→L57` ×2、`L365→L369`） |
| `docs/diagrams/*.puml` + 3 张 SVG | `module-dependencies` / `component-overview` / `four-plus-one-development-view` 三张图加入 `resolution_decisions.py` 节点与 `Decisions → Fallback / Formats` 边 |
| `ai/docs/docs-prompt.md` | 模块清单补 4 个 R2 新模块 + `resolution_decisions.py`，基线 289 → 320、17 → 18 个测试文件 |
| `ai/refactor/refactor.md` | §1 的 `create_app` 行数/路由数写准（458 / 28）；§2.1 刷新表扩成三列并加入 R3 列；§5 DoD 基线 320；§6 回填 R2 的 `046e4de` |

---

## 5. 未覆盖 / 如实说明

1. **我自己先写错了一处"如实说明"，又改回来了。** `resolution_decisions.py` 的第一版文档声称
   "`None`（元数据不可得）与 `[]`（零个格式）对判定是不同输入"。实测**不成立**：三条判定路径对
   两者的结论**完全相同**（`probe` 都走 `fail`，另两条都走 `skip`）。这与
   [ai/ui/003](../ui/003-language-trigger-label-overflows-the-page.md) 的 `text-overflow: ellipsis`
   是同一类盲区 —— **代码看起来在区分，实际没有区分**。
   处理：把模块文档、`decide_probe_fallback` 文档、`_extract_formats_or_none` 文档、
   `docs/implementation.md` 的对应句子全部改成事实描述（类型上分开是**接口约定**，不是行为差异），
   并加一条参数化用例 `test_missing_metadata_and_empty_format_list_currently_agree` 把这份等价性
   **钉住**：以后谁要让它们分道扬镳，这条测试会先失败，逼他回来改文档，而不是让语义悄悄漂移。
   本轮 31 个新用例里有 3 个属于这一类"栅栏"，不是新功能覆盖。
2. **差分取证只覆盖 8 个场景**，全部由 `fakes.py` 造出。它**不覆盖**真实 yt-dlp 返回的格式列表形状
   （同一高度多个 codec、只有音频的条目、`filesize` 缺失等）——那部分由 `test_ytdlp_service.py` 里
   `suggest_lower_resolution` 的既有用例覆盖，本轮没有增补。
3. **差分判据是"终态 payload 逐字段相同"，不覆盖中间过程**。IO 调用次数是**分析得出**的
   （早退条件与守卫逐字保留，所以 `extract_metadata` 的次数与时机不变），不是量出来的。
   可量化的部分只有一个：`test_api.py` 的 happy-path 用例断言"不二次 `extract_metadata`"仍然通过。
4. **不联网**：差分跑的是 fake service，没有验证真实 yt-dlp 的异常文本。真实环境下
   `is_requested_format_unavailable_error` 的判定属 `YtDlpService`，本轮没动它。
5. **`decode_*` 的命名一致性**：模块内四个导出分别是
   `should_look_for_fallback` / `decide_probe_fallback` / `decide_media_stream_fallback` /
   `decide_unavailable_format_fallback`，前两个动词不同（"该不该找" vs "决定了怎么降"）是刻意的，
   但它们不在同一个调用阶段，容易被误读成并列关系。已在 `should_look_for_fallback` 的文档里
   写明"它是纯判断，供调用方在取元数据**之前**问"。
6. **`extract_metadata` 的异常仍被吞掉**（`except Exception: return None`）：这是重构前的既有行为，
   本轮逐字保留。它意味着"网络故障"与"视频不存在"在降级路径上不可区分 —— 属**未修的既有缺陷**，
   不在本轮范围；登记在 [refactor.md §4.1](refactor.md#41-明确列为后续候选的本轮三轮不做) 之外，
   因为修复它需要先决定"哪种异常值得冒泡"。

---

## 6. 风险与回滚

- **回滚方式**：`git revert <本轮提交 hash>`。本轮是「新增 2 个文件 + `job_manager.py` 局部改写 + 文档同步」，
  revert 无冲突预期。
- **回滚后需要重跑的**：`python scripts/check_doc_anchors.py --fix`（33 处锚点会漂回）、
  `python scripts/docs.py render`（3 张 UML 要退回旧版）。
- **已知副作用**：`job_manager.py` 里"降级逻辑在哪"的答案从"翻 9 个方法"变成"先看
  `resolution_decisions.py`"；代价是多一层调用栈（对性能无可测量影响，判定本身是纯计算）。

---

## 7. 关联

- 上级：[refactor.md](refactor.md) §4 路线图 R3。
- 前两轮：[001 前端视图层解耦](001-frontend-view-layer.md)（`c385646`）、
  [002 后端 API 层解耦](002-backend-api-layer.md)（`046e4de`）。
- 背景缺陷：[010-unselectable-probe-raise-skips-the-fallback](../bug-fix/010-unselectable-probe-raise-skips-the-fallback.md)
  —— 降级整段曾是死代码（yt-dlp 用抛异常表达"选不出来"，当时只认 `is_selectable=False`）。
  本轮的差分取证**专门包含这两种表现**，就是为了让这类失效再次发生时能被自动发现。
- 测试：[test_resolution_decisions.py](../../backend/tests/test_resolution_decisions.py)。

---

## 8. 提交与回滚

| 项 | 值 |
| --- | --- |
| 提交 hash | 待回填（记录与改动在同一次提交里，hash 无法写进自身） |
| 回滚命令 | `git revert <hash>` |
| 记录约定 | 与 [bug-fix](../bug-fix/README.md) / [ui](../ui/README.md) 一致：下次触碰 `ai/refactor/` 时回填 hash |
