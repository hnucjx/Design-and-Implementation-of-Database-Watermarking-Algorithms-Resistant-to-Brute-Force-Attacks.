# 005 - 安全删除纯化：白名单判定从「跑任务才知道」到可单测

| 项 | 值 |
| --- | --- |
| 计划时间 | 2026-10-02 17:12 +08:00 |
| 实施时间 | 2026-10-02 17:12 ~ 17:18 +08:00 |
| 依据 | [refactor.md](refactor.md) §4.1 第 2 项「安全删除白名单只能靠跑任务验」；§3 判定准则第 4 条 |
| 起点 commit | `03c94bd`（R4 提交后） |
| 本轮提交 | 待回填（见文末「提交与回滚」） |
| 结论 | **完成**。路径安全判定收进纯模块 `safe_delete.py`，16 例纯逻辑单测（含「字符串前缀」陷阱与 Windows 大小写）+ 8 处端到端删除回归全绿；后端 **354 passed** |

---

## 1. 计划

### 1.1 目标

[refactor.md §4.1](refactor.md#41-后续候选清单登记为本轮三轮不做此后逐项单独一轮) 第 2 项：

> `job_manager.py` 的「安全删除」白名单（`_delete_output_files` / `_is_under_allowed_root`）
> 是路径安全的承重逻辑，却只能靠跑任务来验 —— 它应该和 R3 同类地纯函数化。

删除是整个应用里**唯一会破坏用户数据**的动作。它的承重部分却是一句只有在真实删除时才会执行的
`any(path == root or root in path.parents for root in allowed_roots)` —— 想验证它「该拒的拒了、
该放的放了」，过去得端到端跑一遍下载 + 删除。

判据（[§3 第 4 条](refactor.md#3-判定准则)）：**能不能不起进程、不碰网络、不写库地单测它**。

### 1.2 边界：本轮**不做**的事

1. **不改删除的**可观察行为****：删哪些文件、删完是否收走空的任务目录，一律不变
   （护栏是 `test_api.py` 里已有的 8 处端到端删除测试）。
2. **不改 API 语义**：`delete_files` 的请求/响应形状、状态码、事件不变。
3. **不改候选枚举**：候选链刚在 [004](004-artifact-paths.md) 收敛完，本轮直接复用
   `output_file_candidates` / `item_artifact_candidates`，不动它们。
4. **不把 IO 搬进纯模块**：`unlink()` / `rmdir()` / `exists()` 全部留在 `job_manager`；
   纯模块里一行删除都没有。**「纯」的边界划在「判定」与「执行」之间，不是「逻辑」与「细节」之间。**
5. **不新增依赖、不动前端、不做性能优化**。

### 1.3 风险与对策

| 风险 | 对策 |
| --- | --- |
| 白名单判据写错 → 要么删到下载根之外（毁数据），要么该删的删不掉（用户以为删干净了） | 判据坚持**路径段**比较（`root in path.parents`），并专门测「共享字符串前缀的兄弟目录」 |
| 「收走目录」的边界：把用户选定的下载根本身、或它的上级目录 rmdir 掉 | `removable_job_dir` 只认下载根的**真**子目录，配三条边界测试（等于根 / 是根的父级 / 无任务目录） |
| 纯化时顺手改了容错语义（该抛的吞了、该吞的抛了） | 两个根的失败处理**刻意保持不一致**，见 §2.1，并各配一条断言 |

---

## 2. 实施方案

### 2.1 新建 `backend/app/safe_delete.py`（纯逻辑，82 行）

| 成员 | 职责 |
| --- | --- |
| `DeleteScope`（`@dataclass(frozen=True)`） | 一次删除的允许范围：`download_root` / `job_root` / `allowed_roots`（已去重、保序） |
| `delete_scope(download_dir, job_download_dir)` | 算范围。两个根的失败处理**不同**（见下） |
| `is_deletion_allowed(path, scope)` | 路径段比较，不是字符串前缀 |
| `removable_job_dir(scope)` | 只认下载根的**真**子目录 |

**两个根的容错刻意不一致**（本轮唯一的「设计判断」，也是原代码本来就有的语义，本轮把它显式化）：

- `download_dir` 来自**配置** —— 解析不了说明配置本身有问题，**让它抛**。启动时就炸出来，
  好过等到用户点「删除」才发现删不动。
- `job_download_dir` 来自**任务记录** —— 解析不了只说明这个任务目录不可用，**跳过**即可；
  此时允许范围退化成「只有下载根」。

### 2.2 调用方只留 IO

`job_manager._delete_output_files` 现在只有三件 IO：**解析候选 → `unlink` → `rmdir`**；
类方法 `_is_under_allowed_root` 被删除（它的唯一调用点就在这个方法里，已在 [004](004-artifact-paths.md)
之后确认无其它引用）。

```text
之前：allowed_roots 组装 + 白名单判定 + exists 判定 + unlink + rmdir 全在一个方法里（24 行）
之后：scope = delete_scope(...)；is_deletion_allowed(...)；removable_job_dir(scope) —— 三行判定调用的纯函数
```

---

## 3. 实施情况

| 指标 | 重构前 | 重构后 |
| --- | --- | --- |
| `backend/app/job_manager.py` | 1132 行 | **1128 行**（−4） |
| `backend/app/safe_delete.py` | — | **82 行**（新增） |
| `backend/app/*.py` 模块数 | 33 | **34** |
| `job_manager` 里的删除相关私有方法 | 2（`_delete_output_files` + `_is_under_allowed_root`） | 1（只剩 IO 的那个） |
| 后端测试用例 | 338 | **354**（+16） |

---

## 4. 验证

### 4.1 门槛（[refactor.md §5](refactor.md#5-每轮的固定流程dod) 第 3 步）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 后端测试 | `./.venv/Scripts/python.exe -m pytest backend/tests -q -p no:cacheprovider --basetemp=tmp_pytest/run306` | **354 passed** |
| 前端测试 | `cd frontend && npx vitest run --environment jsdom` | **69 passed**（未动前端） |
| 代码锚点 | `python scripts/check_doc_anchors.py` | 无漂移（`--fix` 重算行锚点） |
| 文档链接与 UML | `python scripts/docs.py check` | 通过 |
| 空白错误 | `git diff --check` | 无输出 |

### 4.2 「行为不变」的取证（本轮重点）

**两层证据，缺一不可：**

1. **纯逻辑层**（新增 `backend/tests/test_safe_delete.py`，16 例，全部 `tmp_path`，0.24s）：

   | 覆盖 | 用例 |
   | --- | --- |
   | 范围组装 | 无任务目录 / 任务目录在根下 / 任务目录 == 根（去重）/ 任务目录在根之外 / 相对路径归一 |
   | 白名单**放行** | 根本身、根的子孙、第二个根（任务目录）下的文件、大小写不同的同一个根（Windows） |
   | 白名单**拒绝** | 根的父级、**共享字符串前缀的兄弟目录**、无关绝对路径（`C:/Windows/...`） |
   | 收走目录 | 真子目录 → 返回；等于根 / 是根的父级 / 无任务目录 → None；**共享字符串前缀的兄弟** → None |

   其中「共享字符串前缀」那两条是刻意留的：它们先断言 `str(sibling).startswith(str(root))` 为真
   （前提成立），再断言判定为 False —— 证明这套白名单**不是**靠前缀匹配撑着。

2. **可观察行为层**（回归护栏，不新增，直接复用）：`backend/tests/test_api.py` 里已有 8 处
   端到端删除测试 —— 真建库、真写文件、真调 `DELETE /api/jobs/...`，覆盖
   `delete_files=true/false` 与单条/批量/末条删父任务/磁盘发现删除：

   ```text
   L1515  DELETE /api/jobs/{id}?delete_files=true
   L1575  items/delete  delete_files=false
   L1649  items/delete  delete_files=true
   L1698  items/delete  delete_files=true（删掉最后一个子项 → 父任务也被删）
   L1715  items/delete  不存在的 item
   L1736  items/delete  ...
   L1850  DELETE /api/jobs/{id}?delete_files=true（按磁盘发现删除）
   L2072  jobs/batch    action=delete  delete_files=true（批量）
   ```

   这 8 条全绿 = 「删哪些文件、删完收不收目录」没有变化。

### 4.3 同步的文档

| 文档 | 改动 |
| --- | --- |
| `docs/*.md`（`technical` / `implementation` / `design` / `requirements` / `safety-review` / `architecture`） | `check_doc_anchors.py --fix` 重算的代码行锚点（无文字改动） |

---

## 5. 未覆盖 / 如实说明

1. **符号链接路径未测。** `is_deletion_allowed` 的语义是「按 `resolve()` 之后的位置判」，
   所以指向下载根之外的符号链接会被拒绝 —— 结论由代码结构保证（先 resolve 再判），
   **不是**量出来的：Windows 上创建符号链接需要额外权限或开发者模式，本机测试没有覆盖这条路径。
   这是本轮最应该有、却没有的第二层证据。

2. **大小写行为只在 Windows 上被验证。** Windows 的 `Path.__eq__` 与 `parents` 比较都大小写不敏感
   （`PureWindowsPath` 归一化），所以 `.../Downloads` 与 `.../downloads` 被当成同一个根 ——
   本轮补了一条 `skipif(os.name != "nt")` 的断言钉住「不会因为大小写不同而误拒」。
   但**大小写敏感的文件系统（Linux）上没跑过**：本项目部署目标就是本机 Windows，
   所以这一条属于「在目标平台上被验证」，不是「跨平台已证明」。

3. **纯化只覆盖「判定」，没覆盖「枚举」。** 候选枚举（[004](004-artifact-paths.md) 收敛的
   `item_artifact_candidates` / `output_file_candidates`）里也含只读 IO 探测（`iterdir()` / `is_file()`），
   本轮没有动它 —— 它的「存在性判断」天然需要真实文件系统，纯化的收益不如判定部分大。

4. **删除仍是逐候选串行的**（每个候选一次 `resolve` + `is_file` + `unlink`），没有批量或并发优化。
   合集的 sidecar 可能很多，但这不是本轮主题（[§3.1 第 7 条](refactor.md#31-目标与非目标)：
   本轮只做结构）。

5. **`download_root` 的解析失败会抛**（`OSError`），这一点是**保留**原行为而非新引入；
   本轮把它写进了 docstring，但没有为它写测试 —— 要触发它得让配置里的下载目录无法 `resolve`，
   构造成本高于收益。

---

## 6. 风险与回滚

| 项 | 值 |
| --- | --- |
| 风险等级 | 中（涉及数据删除路径；但改动是「判定外移 + 逐行等价」，且有两层回归） |
| 回滚命令 | `git revert <hash>` |
| 回滚影响 | 无数据迁移、无 schema 变更；回滚即恢复内联的白名单方法 |

---

## 7. 关联

- **上游**：[refactor.md](refactor.md) §4.1 第 2 项、§3 第 4 条。
- **直接受益于**：[004](004-artifact-paths.md) —— 候选枚举刚收敛完，本轮才能只关心「判定」，
  不必同时改候选链。
- **同类**：[003](003-resolution-decisions.md)（收「降级**判定**」）、[004](004-artifact-paths.md)
  （收「候选**枚举**」）、本轮（收「删除**白名单判定**」）—— 三者都是把「必须跑整条链路才知道」
  的判断变成可单测的纯函数。

---

## 8. 提交与回滚

| 项 | 值 |
| --- | --- |
| 提交 hash | 待回填（记录与改动在同一次提交里，hash 无法写进自身） |
| 回滚命令 | `git revert <hash>` |
| 记录约定 | 与 [bug-fix](../bug-fix/README.md) / [ui](../ui/README.md) 一致：下次触碰 `ai/refactor/` 时回填 hash |
