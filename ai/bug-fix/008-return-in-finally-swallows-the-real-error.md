# 008 · 收尾阶段出的错被静默吞掉（`finally` 里的 `return`，以及没人接住它）

- 状态：已修复
- 提交：`fix(job_manager): 收尾阶段的异常不再被吞掉，也不再带走 worker`
- 影响面：`JobManager._run_item`（下载成功/失败/取消后的状态回写、进度计数刷新、SSE 推送）与 `JobManager._worker`（队列消费）
- 严重度：中（命中需要「删除/改设置」与「收尾出错」同时发生，但一旦命中，界面和日志**都不会**告诉你出了什么事；另外启动时必然打印两条 SyntaxWarning）

## 问题

启动后端时终端先蹦两行：

```text
D:\code-repo\cascade\backend\app\job_manager.py:630: SyntaxWarning: 'return' in a 'finally' block
return
D:\code-repo\cascade\backend\app\job_manager.py:647: SyntaxWarning: 'return' in a 'finally' block
return
```

两条警告只是**症状**。真正的毛病在语义：`finally` 里的 `return` 会**丢弃正在传播的异常**。
用户能观察到的后果有两层：

1. **条目永远停在「下载中」**：收尾代码自己出错时异常被吞掉，状态既没变成失败，日志里也没有一行提到出错。
2. **下载并发静默变少**：如果异常没被吞掉，它会让 `_worker` 那个 asyncio 任务直接结束 ——
   队列少一个消费口，后面的条目全部堆在 `queued`，界面上表现为「并发数莫名其妙对不上」，
   而没有任何报错指到这里（实测证据见下）。

## 原因

### 1) `finally` 里的 `return`（`backend/app/job_manager.py:628` 起，函数 `JobManager._run_item`）

收尾逻辑写在 `finally` 里，并用 `return` 提前退出两个分支：

```python
finally:
    if job.id in self._deleted or item.id in self._deleted_items:
        return                      # ← 630
    if runtime_restart_requested:
        ...
        return                      # ← 647
    ...收尾（回写状态、刷计数、推 SSE）...
```

**伤害窗口要说准，不夸大**：异常来自 `try` 主体时，`except Exception` 会先接住它（不 re-raise），
所以「下载失败」这条常规路径不受影响。被吞掉的是**收尾之前已经在传播的异常** ——
即 `except` 分支或 `else`（成功）分支自己抛出的异常，例如 `_log_item_failure` 内部出错、
`session.refresh(item)` 撞上写锁 —— **并且**此时命中 `_deleted` / `_deleted_items` / `runtime_restart_requested`。

也就是说：需要「用户删掉这个任务/条目」或「下载中途改了限速/重试次数（触发运行期重启）」，
与「收尾阶段出错」同时发生。频率不高，但发生时完全无痕。

### 2) 为什么这条警告时有时无（实测）

SyntaxWarning 只在**源码被重新编译**时打印；`__pycache__` 命中时 CPython 直接读 `.pyc`，连解析都省了。本机实测：

```text
$ python -m pytest -q                     # .pyc 是新的 → 一条警告都没有
255 passed

$ git stash push -- backend/app/job_manager.py    # 让 .pyc 失效
$ python -m pytest -q tests/test_job_manager_finally.py
app\job_manager.py:630: SyntaxWarning: 'return' in a 'finally' block
app\job_manager.py:647: SyntaxWarning: 'return' in a 'finally' block
```

所以「终端里没看到警告」不能当作「源码里没有这个写法」的判据。

### 3) `_worker` 没有兜底（同一件事的另一半）

```python
async def _worker(self, worker_index: int) -> None:
    while True:
        item_id = await self._queue.get()
        try:
            ...
            await asyncio.to_thread(self._run_item_work, item_id)
        finally:
            self._queue.task_done()      # 只有 task_done，没有 except
```

`_run_item_work` 抛出的异常会穿透 `while True`，把这个 worker 的 asyncio 任务结束掉。
`task_done()` 仍会执行，所以队列不会卡在 `join()` 上 —— 表现就是「静默少一个消费口」。

实测（回退到修复前的 `job_manager.py`，跑只包含这一个用例的测试，完整输出见
`tmp_acceptance/008-old-worker.txt`）：

```text
>           assert handled == [crashing_item_id, healthy_item_id]
E           AssertionError: assert ['d2a626c6-...'] == ['d2a626c6-...', '665058e6-...']
E             Right contains one more item: '665058e6-1e85-401f-bae9-0c76c914bd2a'
```

**第二个条目永远没被处理，而整份输出里没有任何 Traceback、没有 `Task exception was never retrieved`、
也没有 `bookkeeping exploded`** —— 那个异常就这么消失了（`self._workers` 一直持有 Task 引用，
回收期的兜底日志也打不出来）。这是本条记录最想修掉的东西：一个**不可诊断**的失败。

这个兜底缺失是**既有缺陷**（`_run_item_work` 里 `_mark_job_running`、`session.refresh(job)`、
`_maybe_finish_job` 都在 `try` 之外，本来就能把 worker 带走），但「不再吞异常」会**扩大**它的可达面。

## 修复方案

### a. `finally` 里只做分支收尾，不再 `return`

```python
finally:
    if job.id in self._deleted or item.id in self._deleted_items:
        pass                       # 删除竞态：不回写，也不提前退出
    elif runtime_restart_requested:
        ...
    else:
        ...收尾...
```

判断条件、顺序、三个分支各自做什么**一个都没动**，只是把「提前退出函数」换成「提前退出这个分支」。

为什么不做别的：

- **抽出 `_finalize_item()` 方法、在里面继续用 `return`**：能消掉警告，但要把 37 行收尾逻辑搬进一个
  需要 6 个参数的方法，diff 更大、读起来更绕，换不来额外好处。
- **外层再包一层 `try/finally`**：只是把同一个问题挪个位置，语义更难读。
- **不管它**：警告可以忽略，但「收尾出错 = 什么都没发生」不能接受 ——
  这正是 [007](007-js-challenge-fails-only-when-cookies-are-on.md) 用一整轮去消灭的那类失败。

### b. worker 兜底，并把崩掉的条目变成可见失败

异常不再被吞之后必须有东西接住它，否则只是把「静默卡住一个条目」换成「静默少一个并发」：

```python
except Exception:  # noqa: BLE001
    logger.exception("item worker crashed: worker=%s item_id=%s", worker_index, item_id)
    self._mark_item_failed_after_crash(item_id)
```

`_mark_item_failed_after_crash()` 把 `queued` / `running` 的条目改成 `failed`（文案 `CRASHED_ITEM_ERROR`），
刷新计数、推进 job 终态、推一条 `item_finished` 事件；**已经落定**（succeeded / failed）的条目不动；
未知 id 安静返回；整个过程再被一层 `try/except` 包住 —— 兜底自己出错也只能记日志，
它唯一的职责是不让 worker 死。

**为什么和 a 放在同一个 commit**：a 与 b 合起来才是「收尾阶段的异常变得可见、且不伤调度器」这**一个**行为变化。
只做 a，「异常可见」是假承诺（变成 worker 静默死掉）；只做 b，异常仍然被吞。
按「一处修复 = 一个 commit」的本意（每个 commit 可独立 revert、各自通过全量测试），
它们属于同一处修复，而不是两处彼此独立的缺陷。

## 效果

### 1) 启动输出

```text
修复前                                             修复后
app/job_manager.py:630: SyntaxWarning: ...          （无）
app/job_manager.py:647: SyntaxWarning: ...          （无）
```

### 2) 行为 A/B（同一批测试，只把 `job_manager.py` 回退到修复前）

```text
$ git stash push -- backend/app/job_manager.py
$ python -m pytest -q tests/test_job_manager_finally.py tests/test_finally_guards.py
FAILED ...::test_run_item_propagates_error_raised_while_the_item_is_deleted
FAILED ...::test_worker_survives_an_item_that_crashes_while_finishing
FAILED ...::test_crashed_item_is_marked_failed_instead_of_stuck_running
FAILED ...::test_crashed_item_marker_leaves_finished_items_alone[succeeded]
FAILED ...::test_crashed_item_marker_leaves_finished_items_alone[failed]
FAILED ...::test_crashed_item_marker_ignores_an_unknown_item
FAILED tests/test_finally_guards.py::test_app_sources_have_no_syntax_warnings
FAILED tests/test_finally_guards.py::test_no_return_inside_finally
8 failed, 4 passed

$ git stash pop
$ python -m pytest -q tests/test_job_manager_finally.py tests/test_finally_guards.py
12 passed
```

其中「修复前后都通过」的 4 个用例是**对照面**（删除竞态下收尾确实不回写状态、
嵌套作用域里的 `return` 不应误报），用来钉住「去掉 `return` 没有顺手改掉原有语义」。

### 3) 全量回归

后端 **255 → 267 passed**（+12）。

## 未覆盖 / 如实说明

- **没有在真实界面上复现「条目永远停在下载中」。** 本轮用直接调用 `_run_item` + monkeypatch
  `_log_item_failure` 复现了「异常被吞 / 不被吞」两种结果，但真实场景还需要「删除恰好发生在收尾那几毫秒」
  这个时序 —— 没有在浏览器里真的走通一遍。
- **`runtime_restart_requested` 那个分支（647 行的 `return`）没有单独的行为测试**：
  该分支体内没有任何可被 monkeypatch 的外部调用点，同一个位置注入不了异常。
  它与 630 行由同一次重构一起去掉，目前只由静态守卫（`test_no_return_inside_finally`）覆盖。
- **只验证了「异常不再被吞」，没有验证「这些异常真的会发生」。** 本机没有复现出
  `session.commit()` 撞锁或 `_publish_threadsafe` 失败；`_mark_item_failed_after_crash` 的真实触发路径
  没有在生产式运行里出现过一次。
- **`_mark_item_failed_after_crash` 的错误文案是固定的一句**，不区分「哪一步崩的」。
  这是有意的（那时连崩在哪都还不知道），代价是用户从界面看不出差异，必须看日志。
- **worker 兜底只覆盖 `_run_item_work`。** `_worker` 循环里其它可能抛异常的语句
  （`await self._queue.get()`、`task_done()`）没有兜底。

## 验证

| 测试 | 覆盖 |
|---|---|
| `tests/test_finally_guards.py`（新，5 例） | 扫描 `app/*.py`：编译不产生 `SyntaxWarning`、AST 里 `finally` 内没有 `return`；并自检规则本身能抓到违规、不会误报嵌套作用域（防止规则被「改松」而不是修代码） |
| `tests/test_job_manager_finally.py`（新，7 例） | 删除竞态下异常向上传播；删除竞态下收尾仍不回写状态（对照面）；worker 崩溃后仍继续消费队列；崩掉的条目被标记为 `failed` 而不是停在 `running`；已落定条目不被改写；未知 id 安静返回 |

回归：后端 `255 → 267 passed`。

## 风险与回滚

- 判断逻辑零改动，只去掉「提前退出」；有对照测试钉住。
- **行为变化（有意）**：收尾阶段的异常现在会浮到 worker 并被记成 `logger.exception`。
  日志会多出 `item worker crashed:` 这一类行 —— 这是想要的，不是噪音。
- **行为变化（有意）**：条目的失败文案多了一种 `内部错误：任务在收尾阶段异常退出，详情见日志。`
- 回滚：`git revert <sha>`。该 commit 只碰 `backend/app/job_manager.py` 与两个新增测试文件，回滚不影响下载链路。

## 关联

- **与 [007](007-js-challenge-fails-only-when-cookies-are-on.md) 同一条线**：007 让「原本被丢弃的异常」有地方可查（补日志），
  本条让「异常不再被吞」。两条合起来才成立 —— 007 之后日志有了，本条之后异常才有机会走到日志。
- **把 `_worker` 无兜底这个既有缺陷一并显性化**：它本来就在（`_run_item_work` 里 `try` 之外的任何一步出错都能带走 worker），
  本条补上它，因为「不再吞异常」会扩大它的可达面。这条链是：
  `finally return 吞异常` → `worker 无兜底` → `队列静默少一个消费口`，**任一环不补都还是查不出来**。
- 与 [004](004-empty-subtitle-languages-expand-to-all.md) 都会导致「条目整体失败」，但根因无关。
