# 004 - 产物路径收敛：一条候选链只有一个计算处

| 项 | 值 |
| --- | --- |
| 计划时间 | 2026-10-02 17:00 +08:00 |
| 实施时间 | 2026-10-02 17:00 ~ 17:08 +08:00 |
| 依据 | [refactor.md](refactor.md) §4.1 第 1 项「产物路径有两条计算链」；§3 判定准则第 5 条 |
| 起点 commit | `23467aa`（R3 提交后） |
| 本轮提交 | 待回填（见文末「提交与回滚」） |
| 结论 | **完成**。「这个条目的文件在哪」从两处各算一次收敛为 `output_paths.py` 一处；12 个磁盘场景 × 3 个入口的**旧/新逐项对照**全过；后端 **338 passed**（+18）、前端 69 passed |

---

## 1. 计划

### 1.1 目标

把「**这个条目的文件可能在哪**」从两处各算一次，收敛成一处。

[refactor.md §4.1](refactor.md#41-明确列为后续候选的本轮三轮不做) 第 1 项是这么记的：
`job_artifacts.py` 的 `output_file` / `item_folder` / `job_folder`（R2 从 `main.py` 原样搬出）
与 `job_manager.py` 的 `_item_output_paths` 各算一次「这个条目的文件在哪」。

两者都在拼同一条概念上的链（库里的 `output_path` → 按视频 id 在下载目录里发现 → 后缀/合并变体），
但用的是各自写的规则 —— 命中 [§3 第 5 条](refactor.md#3-判定准则)「同一概念只允许有一处计算」。
判据很直白：**明天要给候选链加一种变体（比如新的 sidecar 后缀），得同时记得改两处**，漏一处就分叉。

### 1.2 边界：本轮**不做**的事

1. **不改 HTTP 线上形状**，不改任何用户可见文案 —— 三句 409（`视频文件尚不可用。` / `视频文件不存在。` /
   `视频文件夹不存在。`）与合集两处（`合集文件夹尚不可用。` / `合集文件夹不存在。`）逐字保留。
2. **不改删除行为**。删除路径里的白名单校验与 `unlink` 是 §4.1 第 2 项的主题，下一轮单独做；
   本轮只把「候选怎么枚举」收敛掉，删除的**决策与执行**一行不动。
3. **不新增模块、不新增依赖、不动前端**。
4. **不合并两种退避策略**。见 §5 第 3 条：`output_file`（要求成品存在）与 `item_folder`（不要求）
   的规则**确实不同**，本轮把这条差异显式保留成两个命名函数，而不是假装它们是一个。

### 1.3 风险与对策

| 风险 | 对策 |
| --- | --- |
| 收敛候选链时改变「谁先被选中」的顺序 → 点「播放」打开的是另一个文件 | **保序**是硬要求：新入口逐函数复刻原顺序；测试内联重构前的 `_legacy_*`，对同一批磁盘场景断言新旧结果完全一致 |
| 把「第一个候选」当成「第一个成品」→ 打开文件夹时退到错误目录，或把 `.vtt` 当视频 | 两个语义分开命名（`existing_output_file` 要求成品 / `existing_item_folder` 不要求），各配直接语义断言 |
| 删除时少枚举一个候选 → 用户以为删干净了，文件还在 | `item_artifact_candidates` 的对照测试逐项比对新旧列表（而不是只比「长度」） |

---

## 2. 实施方案

### 2.1 落点：`output_paths.py` 成为「条目产物位置」的唯一计算处

新增三个入口 + 一个内部工具，全部是纯函数（除 `is_file()` / `iterdir()` 只读探测）：

| 新函数（`output_paths.py`） | 来自 | 语义 |
| --- | --- | --- |
| `item_artifact_candidates` | `job_manager._item_output_paths` 的**整个方法体** | 可能关联的全部路径（删除用） |
| `existing_output_file` | `job_artifacts.output_file` 的判定段 | 第一个**已存在**的成品视频 |
| `existing_item_folder` | `job_artifacts.item_folder` 的判定段 | 产物所在目录（**不要求**存在） |
| `_dedupe` | 三处手写的保序去重（`_path_variants` / `merged_output_path_candidates` / `output_file_candidates`） | 保序去重 |

### 2.2 调用方退化为「筛选」

| 调用方 | 之前 | 之后 |
| --- | --- | --- |
| `job_manager._item_output_paths` | 13 行自建候选链 + 手写去重 | 1 行转调 |
| `job_artifacts.output_file` | 6 行判定 + `Path(base_dir)` 转换 | 1 行转调 + 状态码翻译 |
| `job_artifacts.item_folder` | 9 行判定 | 1 行转调 + 「退到下载根」的回退 |
| `job_artifacts.job_folder` | 未变（本来就只做编排） | 未变 |

`job_manager` 的导入随之收窄：不再直接依赖 `discover_output_file_candidates`
（它现在只经由 `item_artifact_candidates` 被间接使用）。

### 2.3 顺带收敛掉的一处重复

`discover_existing_output_path` 里内联的「是成品媒体」判据
（`path.suffix.lower() in MEDIA_SUFFIXES and not _is_partial_path(path)`）
与 `_is_preferred_media_path` 是**同一个表达式**，改为调用后者。
这不是新增行为，是把已经在文件里定义了两次的东西变成一次。

---

## 3. 实施情况

| 指标 | 重构前 | 重构后 |
| --- | --- | --- |
| `backend/app/output_paths.py` | 119 行 | **182 行**（+63） |
| `backend/app/job_artifacts.py` | 98 行 | **94 行**（−4） |
| `backend/app/job_manager.py` | 1142 行 | **1132 行**（−10） |
| `backend/app/*.py` 模块数 | 33 | 33（无新增） |
| 后端测试用例 | 320 | **338**（+18，全在新文件 `test_output_paths.py`） |
| 前端测试用例 | 69 | 69（未动前端） |

净行数 +49，全部是文件头注释与三个新入口的签名/docstring —— 收敛的收益不在行数上，
而在「候选链的定义只有一处」（下一轮改删除逻辑时要动的地方少一半）。

---

## 4. 验证

### 4.1 门槛（[refactor.md §5](refactor.md#5-每轮的固定流程dod) 第 3 步）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 后端测试 | `./.venv/Scripts/python.exe -m pytest backend/tests -q -p no:cacheprovider --basetemp=tmp_pytest/run301` | **338 passed**（71.47s） |
| 前端测试 | `cd frontend && npx vitest run --environment jsdom` | **69 passed**（18.75s） |
| 代码锚点 | `python scripts/check_doc_anchors.py` | **没有漂移**（`--fix` 重算 6 篇文档的行锚点；153 处待人工复核属正常项） |
| 文档链接与 UML | `python scripts/docs.py check` | 通过 |
| 空白错误 | `git diff --check` | 无输出 |

### 4.2 「行为不变」的取证（本轮重点）

[refactor.md §3](refactor.md#3-判定准则) 第 6 条：凡声称「行为不变」必须给出**可执行**的对照。
本轮的对照直接写进测试（不是一次性脚本），因为它是可长期复用的回归护栏 ——
`backend/tests/test_output_paths.py::test_single_candidate_chain_matches_pre_refactor_logic`：

```text
把重构前的三处实现内联成 _legacy_output_file / _legacy_item_folder / _legacy_item_output_paths
（逐字复刻），对同一批 12 个磁盘场景，断言：

  item_artifact_candidates(...) == _legacy_item_output_paths(...)
  existing_output_file(...)     == _legacy_output_file(...)
  existing_item_folder(...)     == _legacy_item_folder(...)

12 个场景覆盖：
  empty-dir-no-record / record-missing-no-files / record-points-to-existing /
  record-is-fragment-merge-exists（记录是 .f137.mp4，成品是 .mp4）/
  no-record-discovered-media / media-and-sidecar / sidecar-only-is-not-a-product /
  partial-only-is-not-a-product（.part 不算成品）/ other-video-id-ignored /
  absolute-none / absolute-record-existing / absolute-record-missing
```

另外 5 个**直接语义**断言（不比对旧实现，而是钉住「为什么是这个结果」）：
候选链把库记录放首位、记录与发现同一文件时去重、空目录返回空、
**sidecar 永不被当成成品视频**、以及「产物缺失时打开文件夹退到发现到的父目录」。

> 「sidecar 永不被当成成品」这条是刻意留的：`existing_output_file` 与 `existing_item_folder`
> 的差别正是**要不要要求成品**，一旦有人图省事把两者合并成一个函数，这条会立刻红。

### 4.3 同步的文档

本轮只改了 `backend/app/**`，因此只动锚点（行号随重构漂移）：

| 文档 | 改动 |
| --- | --- |
| `docs/technical.md` / `docs/implementation.md` / `docs/design.md` / `docs/requirements.md` / `docs/safety-review.md` / `docs/architecture.md` | `check_doc_anchors.py --fix` 重算的代码行锚点（无文字改动） |

---

## 5. 未覆盖 / 如实说明

1. **对照场景是构造的，不是从真实下载目录抓的。** 12 个场景由测试自己 `tmp_path` 造文件，
   覆盖了 `.fNNN` 合并变体、sidecar、`.part`、多视频 id、相对/绝对记录路径，
   但**没有**覆盖真实目录里可能出现的组合（多语言 sidecar 与 `.fNNN` 同时存在、
   非 ASCII 文件名以外的大小写差异等）。这些组合的行为由底层原语（未改）决定，本轮不重复验证。

2. **`job_artifacts.output_file` 里 `if not path.is_file(): raise 409 "视频文件不存在。"` 这一分支
   在当前实现下不可达**：上游 `resolve_existing_output_path` 与 `discover_existing_output_path`
   都只在候选 `is_file()` 时才返回值。本轮**原样保留**了它 —— 按 [§3.1 第 5 条](refactor.md#31-目标与非目标)
   的精神，删掉一个分支属于「改动代码形状」，需要有独立的理由与验证，不该夹带在收敛候选链这一轮里。
   把它记在这里，是为了让「它现在不可达」这件事有据可查，而不是让下一个人重新推一遍。

3. **顺序等价是「逐函数复刻」保证的，不是「证明两种策略等价」推导的。**
   把两条链降为一条的方式，是把两边的实现都搬进同一个模块并让它们共用底层原语
   （`resolve_existing_output_path` / `discover_output_file_candidates` / `_is_preferred_media_path`），
   而**不是**断言「`output_file` 与 `item_folder` 的规则相同」—— 后者不成立：
   `output_file` 的退避要求「第一个**成品**」，`item_folder` 的退避只要求「第一个**候选**」（可以是 sidecar）。
   本轮把这个差异显式保留成两个函数名。

4. **`item_artifact_candidates` 返回的是「可能路径」，含不存在的。** 这是刻意的：
   删除要覆盖「记录里写的那个路径即使已经不在磁盘上，它的**变体**可能还在」。
   调用方不能假设返回值都存在 —— `existing_*` 两个入口正是为这个假设做的封装。

---

## 6. 风险与回滚

| 项 | 值 |
| --- | --- |
| 风险等级 | 低-中（纯函数搬迁 + 保序；无契约、无状态机、无 IO 副作用变化） |
| 回滚命令 | `git revert <hash>` |
| 回滚影响 | 无数据迁移、无 schema 变更；回滚即恢复两处各自的候选链实现 |

---

## 7. 关联

- **上游**：[refactor.md](refactor.md) §4.1 第 1 项（本轮的主题来源）、§3 第 5 条（判据）。
- **下游**：[§4.1 第 2 项](refactor.md#41-明确列为后续候选的本轮三轮不做)「安全删除白名单」
  会继续动 `job_manager` 的删除路径 —— 它现在能直接站在收敛后的 `item_artifact_candidates` 上，
  不必再关心候选是怎么来的。这是本轮先做的原因。
- **同类**：与 [003](003-resolution-decisions.md) 同属「把散落的判定收进一个纯模块」，
  区别是 003 收的是**判定**（要不要降级），本轮收的是**枚举**（有哪些路径）。

---

## 8. 提交与回滚

| 项 | 值 |
| --- | --- |
| 提交 hash | 待回填（记录与改动在同一次提交里，hash 无法写进自身） |
| 回滚命令 | `git revert <hash>` |
| 记录约定 | 与 [bug-fix](../bug-fix/README.md) / [ui](../ui/README.md) 一致：下次触碰 `ai/refactor/` 时回填 hash |
