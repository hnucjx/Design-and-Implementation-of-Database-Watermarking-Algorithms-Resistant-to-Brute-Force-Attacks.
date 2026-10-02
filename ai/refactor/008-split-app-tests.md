# 008 - 拆分 App 集成测试：一个文件一个功能面

| 项 | 值 |
| --- | --- |
| 计划时间 | 2026-10-02 17:20 +08:00 |
| 实施时间 | 2026-10-02 17:20 ~ 17:42 +08:00 |
| 依据 | [refactor.md](refactor.md) §4.1 第 5 项「`frontend/src/App.test.tsx`（1521 行 / 69 用例）未按功能拆分」 |
| 起点 commit | `bbe2392`（R7 提交后） |
| 本轮提交 | 待回填（见文末「提交与回滚」） |
| 结论 | **完成**。1521 行的单文件拆成 11 个按功能面的文件 + 3 个共享件；**69 个用例名逐字不变**，且从「只能整包绿」变成「每个文件都能单独冷启动绿」 |

---

## 1. 计划

### 1.1 目标

[refactor.md §4.1](refactor.md#41-后续候选清单登记为本轮三轮不做此后逐项单独一轮) 第 5 项：

> **`frontend/src/App.test.tsx`（1521 行 / 69 用例）未按功能拆分。**

它同时出现在 §2「规模最大的 8 个文件」表里（1521 行，仅次于 `ytdlp_service.py` 与 `job_manager.py`）。
单文件 69 个用例意味着：想改「cookies 导入」的断言，要先在 1500 行里定位；想只看「任务中心」的
覆盖面，得通读全文。

### 1.2 边界：本轮**不做**的事

1. **不改任何用例的断言内容与用例名。** 唯一的例外是 §4.3 那一条 —— 它不是「顺手改」，而是
   被拆分暴露出的**顺序依赖**，改法与取证都在 §4.3 写清（断言一条没减）。
2. **不动前端应用代码**：`App.tsx`、`components/**`、`styles.css`、`quality.ts` 等一行未动。
   本轮只动测试文件与文档。
3. **不新增依赖**（npm 不加包；不引入 `@testing-library/react-hooks` 之类的辅助库）。
4. **不顺手修无关遗留**：本轮顺带发现 `docs/safety-review.md` 有 2 处指向 R5 已删除的
   `_is_under_allowed_root`，与前端测试拆分无关，**不修**（见 §5 第 5 条）。
5. **不动后端**：`backend/**` 一行未动。

### 1.3 风险与对策

| 风险 | 对策 |
| --- | --- |
| 搬运 69 个用例时漏掉 / 重名 / 改坏断言 | **机械对照**：从 HEAD 提取全部 `test("…")` 名，与拆分后 11 个文件的集合 `diff`（§4.2，结果为空）；再逐文件冷启动跑一遍（§4.1） |
| 共享的 `fetch` 替身被复制成多份 → 以后改一处要改 N 处（正是 §3 第 5 条要防的） | 抽出 `appHarness.ts`（唯一来源），11 个文件都从它取；`beforeEach` 复位 `mockState` |
| 拆完「整包绿」但某文件单独跑红 | 逐文件冷启动逐个跑（§4.1 第二张表）—— 这条判据**真的抓到了东西**，见 §4.3 |
| 拆完之后文档里指向 `App.test.tsx` 的链接断掉 | 删文件前后各跑 `docs.py check`；链接、PlantUML 标签与渲染出的 SVG 一起更新（§7） |

---

## 2. 实施方案

### 2.1 为什么是 11 个文件，而不是 3 个

「按功能拆分」有多种粒度。选**功能面**（界面上的一个区域 / 一类交互）而不是**大分类**（「解析相关」「任务相关」），
理由是 `App` 的界面本身就是按这个粒度组织的（`components/` 下就是 `UrlAnalyzer` / `CookieSection` /
`SettingsPanel` / `ProxySection` / `JobQueue`）。这样「改哪块看哪个文件」是**一对一**的，不需要二次判断。

唯一的例外是 `layout.test.tsx`（3 例）：它断言的是跨区域的文案与版面不变式（面板标题不冗余、顶栏不显示
`ffprobe`、任务计数在标题行），不属于任何一个功能面，所以单独成一个文件。`styles.test.ts`（3 例）同理 ——
它读的是样式源文，不 render。

**最大的文件是 `analyzer.test.tsx`（254 行 / 11 例），比原来的 1521 行小一个数量级。**

### 2.2 共享件：三处，都在 `frontend/src/test/`

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| [appHarness.ts](../../frontend/src/test/appHarness.ts) | 256 | `mockState`（后端替身的状态）、`installAppHarness()`（前后置钩子 + 全局替身）、`ensureExpanded` / `exactRow` |
| [appFixtures.ts](../../frontend/src/test/appFixtures.ts) | 317 | 既有夹具，**原样保留**（唯一改动：删掉一行未被任何地方读取的模块级 `let currentAnalyzePayload`） |
| [cssRules.ts](../../frontend/src/test/cssRules.ts) | 23 | `cssRuleBodies()`：把 CSS 源文拆成「选择器 → 规则体列表」 |

两个设计决定值得写下来：

1. **`mockState` 是对象，不是导出的 `let`。** 原来每个用例直接给模块级 `let` 赋值（`currentJobsPayload = […]`）。
   拆成多文件后，各文件都会 `import` 这些变量 —— 而 ES module 的 `import` 绑定是**只读**的，
   给导入的 `let` 赋值会直接报错。用 `mockState.jobs = […]` 既绕开这个限制，也把「测试驱动了后端的哪一部分」
   在调用点显式化了（`mockState.settings` 比 `currentSettingsPayload` 更说得出它是谁的状态）。
2. **`installAppHarness()` 是显式函数，不是模块顶层副作用。** 顶层直接调 `beforeEach` 也能生效，
   但那是"导入即生效"的隐式契约；写成函数后，每个测试文件顶层那一行 `installAppHarness();` 就是
   它「挂上了共享替身」的**可见证据**，读文件时不必回翻 `test/` 目录。

`fetch` 替身（约 130 行）整体搬进 harness 而不是每文件带一份 —— 它模拟的是一整套后端（settings / cookies /
jobs / analyze / diagnostics / proxy 六组接口），按功能裁剪反而会让「某个文件里少了哪条路由」变成隐性差异。

### 2.3 分组

| 文件 | 功能面 | 例 | 行 |
| --- | --- | --- | --- |
| `src/analyzer.test.tsx` | 解析链接：链接输入、解析、清晰度选择、字幕选项、ffmpeg 警告、下载选项与提交队列 | 11 | 254 |
| `src/cookies.test.tsx` | cookies：上传 / 清除 / 浏览器导入 / Edge 锁库重试 + 校验结论（联网、离线、google-only） | 7 | 150 |
| `src/settings.test.tsx` | 设置面板：并发、aria2c、代理、下载目录、保存状态 + 代理检测与端口预设 | 10 | 206 |
| `src/task-center.test.tsx` | 任务中心：单项与批量控制、删除（含确认框）、展开折叠、子视频重启/删除 | 11 | 185 |
| `src/task-center-files.test.tsx` | 任务中心：播放、打开文件夹、复制链接、跳转页面、本地文件操作失败提示 | 9 | 189 |
| `src/task-center-progress.test.tsx` | 任务中心：进度、速度、时长、大小、实际分辨率与失败原因 | 6 | 96 |
| `src/resolution-fallback.test.tsx` | 清晰度降级提示与「以建议清晰度重启 / 重试」 | 4 | 108 |
| `src/diagnostics.test.tsx` | 自检：JS 运行时不可用的原始报错与刷新、日志文件路径 | 2 | 42 |
| `src/help-notes.test.tsx` | 帮助浮层：默认收起、Esc 关闭、跨刷新记忆 | 3 | 64 |
| `src/layout.test.tsx` | 跨区域的版面与文案不变式 | 3 | 38 |
| `src/styles.test.ts` | 样式源文不变式（不 render） | 3 | 75 |

用例数合计 **69**，与拆分前一致（§4.2 的机械对照）。

### 2.4 总行数变多了（1521 → 1686），这是预期的

拆分不是为了让代码变短，而是为了让**共享件只有一处**、**每个文件自解释**。净增的 ~165 行来自：

- 11 个文件的 import 头与 `describe` 包裹（约 6~8 行/文件）；
- `harness` 里为 `mockState` 每个字段补的用途注释。

反过来，原来挤在单文件里的 ~165 行共享件（`fetch` 替身、钩子、两个查询辅助、CSS 解析）现在**只存在一份**，
而且被 11 个文件共用 —— 这一条才是本轮的价值。

---

## 3. 实施情况

| 指标 | 值 |
| --- | --- |
| 删除 | `frontend/src/App.test.tsx`（1521 行） |
| 新增 | 11 个测试文件（`src/*.test.tsx` + `src/styles.test.ts`，合计 1407 行） |
| 新增 | 2 个共享件：`src/test/appHarness.ts`（256 行）、`src/test/cssRules.ts`（23 行） |
| 改动 | `src/test/appFixtures.ts`：删 1 行未被读取的模块级 `let` |
| 前端应用代码改动 | **零** |
| 后端改动 | **零** |
| 新增依赖 | **零** |
| 文档同步 | `docs/testing.md`（前端测试范围一节重写）、`docs/diagrams/four-plus-one-development-view.puml` 标签 + 重新渲染的 SVG |

`docs/testing.md` 里补了一句给后人的话：每个文件都必须能**单独**跑绿，拆分前个别断言是靠同文件里
先跑过的用例才成立的（指向本记录 §4.3）。

---

## 4. 验证

### 4.1 门槛（[refactor.md §5](refactor.md#5-每轮的固定流程dod) 第 3 步）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 前端测试（全量） | `npx vitest run --environment jsdom` | **69 passed / 11 files**（10.0 s） |
| 前端测试（**逐文件冷启动**） | `for f in src/*.test.tsx src/*.test.ts; do npx vitest run … "$f"; done` | **11/11 文件全绿**：11+7+2+3+3+4+10+9+6+11+3 = 69 |
| 类型 | `npx tsc --noEmit` | 通过 |
| 构建 | `npx vite build` | 通过（1597 modules，1.55 s） |
| 代码锚点 | `python scripts/check_doc_anchors.py` | **无漂移**（156 处「待人工复核」为正常项） |
| 分层校验 | `python scripts/check_layers.py` | **EXIT=0**（34 已登记 / 40 实际） |
| 文档链接与 UML | `python scripts/docs.py check` | 通过（§4.5） |
| 空白错误 | `git diff --check` | 无输出 |

「逐文件冷启动」这一条**不是形式**：正是它抓到了 §4.3 的问题。

### 4.2 用例名集合逐字对照（机械证据）

```bash
git show HEAD:frontend/src/App.test.tsx | grep -o 'test("[^"]*"' | sort > tmp_acceptance/t008_old_names.txt
grep -ho 'test("[^"]*"' frontend/src/*.test.tsx frontend/src/*.test.ts | sort > tmp_acceptance/t008_new_names.txt
diff tmp_acceptance/t008_old_names.txt tmp_acceptance/t008_new_names.txt   # → 无输出
```

两边都是 **69** 行、`diff` 为空。即：没有漏搬、没有重名、没有为了让某个文件"干净"而悄悄删掉用例。

（这条对照的盲区见 §5 第 7 条。）

### 4.3 顺带发现：一条断言的**顺序依赖**（本轮最重要的一节）

**现象。** 拆分后 `diagnostics.test.tsx` 的第一条失败：

```text
App · 自检信息 > surfaces a broken JS runtime with the raw reason and a refresh button
→ Unable to find an element with the text: /ERR_ACCESS_DENIED/.
```

**机制。** 那条用例原来是这么写的：

```ts
expect(await screen.findByText(/JS 运行时（解析 YouTube 的 n 参数，登录状态下必须）：不可用/)).toBeInTheDocument();
expect(screen.getByText(/ERR_ACCESS_DENIED/)).toBeInTheDocument();   // ← 同步，没有等待
```

问题在第一句：`ProxySection.tsx` 里

```ts
const jsRuntimeReady = runtime?.dependencies?.js_runtime === true;   // runtime 为 null ⇒ 也是 false
…
JS 运行时（解析 YouTube 的 n 参数，登录状态下必须）：{jsRuntimeReady ? "可用" : "不可用"}
```

**`：不可用` 这个文案在「自检还没回来」时就已经成立**（`runtime` 为 `null`，`jsRuntimeReady` 同样是 `false`）。
所以 `findByText` 会在**首帧**就命中并返回 —— 它证明不了"数据已到"。真正需要数据的第二句没有等待，
于是变成赌「`fetch` 的微任务已经排空」。

**对照取证（三组，都是实测）：**

| # | 跑法 | 结果 |
| --- | --- | --- |
| 1 | **HEAD 原文件全量**（`npx vitest run … src/App.test.tsx`） | **69 passed** |
| 2 | **HEAD 原文件只跑这一条**（加 `-t "surfaces a broken JS runtime"`） | **失败**，同一条报错 |
| 3 | 把这一对断言原样复制成 **12 个独立用例**（临时探针文件，跑完即删） | **12 failed（12/12）** |

第 2、3 组说明：**这不是拆分引起的**。它在单文件时代就是顺序依赖 —— 前面 58 个用例把调度「跑热」了，
所以 `getByText` 之前 `fetch` 已经落地；拆完后它成了所在文件的第 1 例（冷启动），必然失败。

> 这解释了那 58 个"邻居"一直在替它兜底。反过来也说明一件反直觉的事：
> **「删掉几个慢用例」这种看起来无害的改动，可能让剩下的用例变红。**
> 拆测试文件之后必须逐文件冷启动跑一遍，只按整包绿是看不出来的。

**修法**（断言一条没减，只把"等待"放到只可能来自 payload 的文本上）：

```ts
// 先等**只可能来自 payload** 的那段文本（ProxySection.tsx 只在 js_runtime_error 是字符串时渲染它）。
expect(await screen.findByText(/自检报错：/)).toBeInTheDocument();
expect(screen.getByText(/JS 运行时（解析 YouTube 的 n 参数，登录状态下必须）：不可用/)).toBeInTheDocument();
expect(screen.getByText(/ERR_ACCESS_DENIED/)).toBeInTheDocument();
expect(screen.getByText(/The page needs to be reloaded\./)).toBeInTheDocument();
```

修后：该文件单独跑 2 passed；全量 69 passed。

### 4.4 与 HEAD 的行为等价性

对「应用行为」而言，本轮**没有可观测变化**：改动只出现在测试文件与文档里，`frontend/src` 下
非测试代码一行未动（`git diff --stat` 可证）。等价性的真正证据是 11 个文件、69 个用例逐条通过 ——
它们断言的正是应用的全部可观测界面行为。

### 4.5 PlantUML 与 SVG

`four-plus-one-development-view.puml` 里 `[frontend/src/App.test.tsx] as FrontendTests` 已过期，
改为 `[frontend/src/*.test.tsx] as FrontendTests`，并用仓库既有的 PlantUML 工具链**只重渲染这一张**：

```bash
java -jar .tools/docs/plantuml-mit-1.2026.5.jar -tsvg docs/diagrams/four-plus-one-development-view.puml -o ../assets/diagrams
```

SVG 的 diff 只有 1 行（标签文本），说明渲染环境与已提交版本一致，没有把无关的图拖进来。
之后 `python scripts/docs.py check` 通过（它会把全部 15 张图重渲染进临时目录逐字节比对）。

---

## 5. 未覆盖 / 如实说明

1. **jsdom 不做布局 —— 这条边界没有因为拆分而改变。** `styles.test.ts` 的三条断言是
   **样式源文的不变式**（根因断言），不是「按钮真的对齐了」「标签真的折行了」。真判据仍是
   浏览器级 CDP 探针量像素 + 截图（见 [ai/ui/001](../ui/001-cookie-buttons-not-on-the-same-baseline.md)~[003](../ui/003-language-trigger-label-overflows-the-page.md)）。
2. **拆分是搬位置，不提升覆盖率。** `subtitles.ts` / `cookieLock.ts` / `quality.ts` 仍没有独立单测，
   只由这批集成用例间接覆盖（这一条在 [001](001-frontend-view-layer.md) §5 就已登记，本轮没有变化）。
3. **`appFixtures.ts` 的 `sanitized_environment: []` 等分支仍未被覆盖** —— 搬动没有改变这一点。
4. **本轮只验证了"本机逐文件冷启动"。** CI（[007](007-ci-and-layers.md) 建的）里 Vitest 默认按**文件并行**，
   若将来又出现一条顺序依赖的断言，CI 的调度与"逐文件冷启动"并不等价 —— 也就是说**这条防线在本机、不在 CI**。
5. **顺带发现、本轮未处理**：`docs/safety-review.md` 有 2 处指向 `_is_under_allowed_root`
   （R5 已把该函数删除、判定搬到 `safe_delete.is_deletion_allowed`）。`check_doc_anchors.py` 把它列进
   「待人工复核」而**不会失败** —— 这正是那个脚本的已知盲区（标签是符号名但解析不出唯一行时，它只提示不报错）。
   与前端测试拆分无关，**故意不修**，避免把无关改动混进本轮 commit。
6. **历史记录里的 `App.test.tsx` 指称没有回改。** 见 §7 的清单：`ai/ui/001~003`、`ai/perf/PLAN.md`
   写的是"当时"的事实（那几条断言当时确实在 `App.test.tsx` 里），属历史记录，保留原文。
   `ai/docs/docs-prompt.md` 的基线数（仍写 289/320 与 `App.test.tsx` 69 例）**留给本轮之后的收尾一轮**统一刷新。
7. **§4.2 的用例名对照不是自动化门槛。** 它靠 `grep -o 'test("[^"]*"'` 提取，若将来有人用单引号写用例名、
   或用 `test.skip` / `it()`，这条对照会**静默漏计**（不会报错，只是数目对不上）。没有把它做成入库脚本。
8. **`mockState` 的字段是"可写"的**：某个用例忘了复位（比如新加字段却没写进 `resetMockState`），
   污染会跨用例传播。当前的对策是"初始值与复位值写在同一个文件里、肉眼可对照"，没有运行时护栏。

---

## 6. 风险与回滚

| 项 | 值 |
| --- | --- |
| 风险等级 | 低（只动测试文件与文档；应用代码零改动、契约零改动） |
| 回滚命令 | `git revert <hash>` |
| 回滚影响 | 无。回到 1521 行单文件的 `App.test.tsx`（测试覆盖面不变） |

---

## 7. 关联

- **上游**：[refactor.md](refactor.md) §4.1 第 5 项。
- **文档同步**（删文件会立刻断链，两处都必须改）：
  - `docs/testing.md` 的「前端测试范围」一节：链接从 `[App.test.tsx]` 改为 `frontend/src/*.test.tsx` +
    三个共享件，并写明「每个文件必须能单独跑绿」。
  - `docs/diagrams/four-plus-one-development-view.puml` + 重渲染的 `.svg`（§4.5）。
- **波及但保留原文的引用**（都是"当时"的事实，非链接，不影响任何检查）：

  | 位置 | 内容 | 处置 |
  | --- | --- | --- |
  | `ai/ui/001`~`003` | 「新增断言（`App.test.tsx` → 某条不变式）」 | 保留。断言本体已原文搬入 `src/styles.test.ts`，用例名未变，按名可搜到 |
  | `ai/perf/PLAN.md` | `frontend/src/App.test.tsx:331` 等行号指称 | 保留（历史计划） |
  | `ai/docs/docs-prompt.md` | 前端测试 69 passed / 基线 289、320 | **留给收尾一轮**统一刷新（§5 第 6 条） |
  | `ai/refactor/001` | 「`App.test.tsx` 未拆分，仍是 1521 行（候选 R4）」 | 保留 —— 那正是本项被登记的由来 |

- **与 [007](007-ci-and-layers.md) 的关系**：本轮的 11 个文件都由 007 建的 CI 前端 job 覆盖
  （`vitest run --environment jsdom` 自动收集全部 `*.test.tsx`），无需改 `ci.yml`。

---

## 8. 提交与回滚

| 项 | 值 |
| --- | --- |
| 提交 hash | 待回填（记录与改动在同一次提交里，hash 无法写进自身） |
| 回滚命令 | `git revert <hash>` |
| 记录约定 | 与 [bug-fix](../bug-fix/README.md) / [ui](../ui/README.md) 一致：下次触碰 `ai/refactor/` 时回填 hash |
