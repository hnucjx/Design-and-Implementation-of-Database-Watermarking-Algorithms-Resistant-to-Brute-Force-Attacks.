# 001 - 前端视图层解耦：`App.tsx` 只留状态编排

| 项 | 值 |
| --- | --- |
| 计划时间 | 2026-10-02 16:08 +08:00 |
| 实施时间 | 2026-10-02 16:08 ~ 16:16 +08:00 |
| 依据 | [refactor.md](refactor.md) §4 路线图 R1、§3 判定准则、§5 DoD |
| 起点 commit | `caa4fbc`（重构总纲提交后） |
| 本轮提交 | 待回填（见文末「提交与回滚」） |
| 结论 | **完成**。文案 / 函数体 / JSX 类名三项不变性取证全过，前端 69 测试 + `tsc` + 构建全绿 |

---

## 1. 计划

### 1.1 目标

把 `frontend/src/App.tsx`（1128 行）里的**展示职责**外移，使它只剩「状态编排 + 请求发起 + 布局」。
判据（[refactor.md §3](refactor.md#3-判定准则) 第 1 条「单一变化原因」）：
**展示改版不该碰状态容器，状态改动不该要求通读 1100 行。**

### 1.2 边界：本轮**不做**的事

1. **不动后端一行**（后端模块与文档锚点本轮无关）。
2. **不改任何 JSX 结构与类名** —— 搬迁必须逐字，包括 `ai/ui/001~003` 修复所依赖的 DOM 形状。
3. **不改任何用户可见文案**（[refactor.md §3.1](refactor.md#31-目标与非目标) 第 5 条）。
4. **不拆 `App.test.tsx`**（1521 行）：它是对 `App` 整体的集成测试，拆分属独立议题，已登记在
   [refactor.md §4.1](refactor.md#41-明确列为后续候选的本轮三轮不做) 第 5 条。
5. **不引入任何 npm 依赖**，不引入状态管理库，不动 Vite/Vitest 配置。
6. **不抽 hook**：本轮只做「展示 vs 编排」的切分。抽 `useJobs` / `useAnalyze` 会同时改变
   状态所有权，属另一类改动（会动到竞态与时序），不放进纯搬迁轮。

### 1.3 风险与对策

| 风险 | 对策 |
| --- | --- |
| 搬迁时手滑改动 JSX / 文案（历史上最常见的重构事故） | 三轮自动化取证：CJK 字符串多重集合、函数体逐字比对、`className` 多重集合（见 §4） |
| 抽出组件后 props 方向搞反，出现「面板自己存一份状态」 | 全部新组件**无内部业务状态**：只有 `browserCookieSource` / `isImportingCookies` / `isOpen` / `query` / `draft` 这类纯界面状态 |
| 循环依赖 | 依赖只允许 `App → components → (纯模块 / api) → types`；`cookieLock.ts` 依赖 `api.ts`（`ApiError`），组件依赖 `cookieLock.ts`，无回边 |

---

## 2. 实施方案

`App.tsx` 里的每一段按「谁需要它」重新归属（[refactor.md §3](refactor.md#3-判定准则) 第 1、4 条）：

| 新位置 | 来源（重构前 `App.tsx` 的行段） | 类型 |
| --- | --- | --- |
| `components/UrlAnalyzer.tsx` (147) | `UrlAnalyzer` L502-620 + `BROWSER_COOKIE_OPTIONS` L63-72 | 展示组件 |
| `components/AnalysisPanel.tsx` (91) | `AnalysisPanel` L622-701 | 展示组件 |
| `components/DownloadOptionsPanel.tsx` (170) | `DownloadOptionsPanel` L703-854 | 展示组件 |
| `components/SearchableLanguageSelect.tsx` (83) | `SearchableLanguageSelect` L902-969 | 展示组件 |
| `components/Toggle.tsx` (21) | `Toggle` L971-988 | 基础件 |
| `components/SettingsPanel.tsx` (156) | `SettingsPanel` L990-1128 | 展示组件 |
| `components/StatusPill.tsx` (11) | `StatusPill` L479-486 | 基础件 |
| `subtitles.ts` (62) | `formatSubtitleInfo` / `subtitleSourceDescription` / `effectiveSubtitleSourceForAnalysis` / `subtitleFormatLabel` L856-900 | 纯函数 |
| `cookieLock.ts` (32) | `browserCookieLockFromError` L488-500 + `BrowserCookieLock` 类型 L90-94 | 纯函数 + 类型 |

`App.tsx` 保留：`INITIAL_OPTIONS`、13 个业务状态、4 个 `useEffect`（初始化 / SSE / 定时器清理 / 选中项收敛）、
`subtitleLanguages` 的 `useMemo`、14 个处理函数、整页布局。

**两处必须说明的判断：**

1. **`BrowserCookieLock` 类型与映射函数没有放进 `UrlAnalyzer.tsx`**，而是独立成 `cookieLock.ts`。
   理由：它消费的是 `ApiError.detail.code`（HTTP 契约），属于「错误 → 应用状态」的映射，
   `App` 与 `UrlAnalyzer` **都要用**；放进任一组件都会让另一个反向依赖组件。
2. **`Toggle` 只有 18 行也单独成文件**。理由：它是被重复使用 4 次的交互原语，
   且与「勾选行」的样式类 `toggle-row` 绑定；留在 `DownloadOptionsPanel` 里会让下一个需要它的面板复制一遍。

`subtitles.ts` 只导出**外部真正用到**的两个函数（`formatSubtitleInfo`、`effectiveSubtitleSourceForAnalysis`），
`subtitleSourceDescription` / `subtitleFormatLabel` 保持模块私有 —— 公共面越小，越不会被别处顺手复用成第二处实现。

注释随代码走：`ai/ui/001/002/003` 的样式约束、并发语义、代理留空语义等「改之前先读」的内容，
已作为文件头注释写进对应的新组件（原先只存在于 `docs/design.md`）。

---

## 3. 实施情况

| 指标 | 重构前 | 重构后 |
| --- | --- | --- |
| `App.tsx` 行数 | **1128** | **446**（−682） |
| 前端源文件数 | 13 | 23 |
| 前端源码总行数 | 4699 | 4790（+91，全部是新增的文件头注释） |
| `App.tsx` 里的 `function` 定义 | 11 | 1（`App` 本身） |
| 前端测试用例 | 69 | 69（未增未减） |

新组件全部为**命名导出**（与既有 `components/*.tsx` 一致），全部无默认导出。

---

## 4. 验证

### 4.1 门槛（[refactor.md §5](refactor.md#5-每轮的固定流程dod) 第 3 步）

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 前端测试 | `cd frontend && npx vitest run --environment jsdom` | **69 passed**（12.23s；基线 16:04 亦为 69 passed） |
| 类型检查 | `cd frontend && npx tsc --noEmit` | 退出码 0 |
| 构建 | `cd frontend && npx vite build --outDir ../tmp_acceptance/dist_r1` | 通过（1597 modules，`index-*.js` 203.57 kB / gzip 65.43 kB） |
| 文档链接与 UML | `python scripts/docs.py check` | 通过（仅 `component-overview` / `four-plus-one-development-view` 两张 SVG 变动，与 `.puml` 一致） |
| 代码锚点 | `python scripts/check_doc_anchors.py` | **没有漂移**（「待人工复核」111 → 123，新增的 12 条全是本轮新加的文件名标签锚点，属该脚本定义的正常项） |
| 空白错误 | `git diff --check` | 无输出 |

### 4.2 「行为不变」的取证（本轮重点）

[refactor.md §3](refactor.md#3-判定准则) 第 6 条要求：**凡声称「行为不变」必须给出可执行对照，不能只写「已确认无变化」**。
本轮为此写了一次性脚本 `tmp_acceptance/refactor_r1_invariance.py`（被 gitignore，不进 CI），
以 **HEAD 的 `frontend/src` 全树**为基线，对重构后的树做三项比对：

```text
[1] 含 CJK 的字符串字面量：基线全树 356 条，重构后全树 356 条
    丢失 0 条 / 新增 0 条
[2] 逐字比对 12 个被搬走的函数（基线 = HEAD 的 App.tsx）：
    OK  StatusPill -> frontend\src\components\StatusPill.tsx
    OK  UrlAnalyzer -> frontend\src\components\UrlAnalyzer.tsx
    OK  AnalysisPanel -> frontend\src\components\AnalysisPanel.tsx
    OK  DownloadOptionsPanel -> frontend\src\components\DownloadOptionsPanel.tsx
    OK  SearchableLanguageSelect -> frontend\src\components\SearchableLanguageSelect.tsx
    OK  Toggle -> frontend\src\components\Toggle.tsx
    OK  SettingsPanel -> frontend\src\components\SettingsPanel.tsx
    OK  formatSubtitleInfo -> frontend\src\subtitles.ts
    OK  subtitleSourceDescription -> frontend\src\subtitles.ts
    OK  effectiveSubtitleSourceForAnalysis -> frontend\src\subtitles.ts
    OK  subtitleFormatLabel -> frontend\src\subtitles.ts
    OK  browserCookieLockFromError -> frontend\src\cookieLock.ts
[3] className 多重集合（全树）：
    丢失 无 / 新增 无
全部通过：本轮是纯搬迁，文案、函数体、JSX 类名三项都没有变化。
```

三条断言的设计意图：

1. **CJK 字符串多重集合** —— 界面文案散落在 JSX 里，靠 review 一定会漏；多重集合比对能同时抓住
   「丢了一句」和「顺手改了一句」，这是本轮最不能出事的地方。
2. **函数体逐字相等** —— 只允许两个规范化差异：`React.FormEvent` → `FormEvent`（改成显式类型导入）、
   以及 `export ` 前缀；文件头注释不计入函数体。另外要求**每个函数在整棵树里恰好命中一处**，
   防止「搬走了但原文还在」。
3. **`className` 多重集合** —— `ai/ui/001~003` 三个界面缺陷的修复都建立在 DOM 形状上。
   类名集合不变，等于这三处修复的复现条件没有被本轮改动。

### 4.3 同步的文档

| 文档 | 改动 |
| --- | --- |
| `docs/design.md` | 「前端组件边界」整节重写：4 个组件 → 12 个组件/模块卡片，补「判断新代码放哪」的落位规则 |
| `docs/implementation.md` | 「前端实现」段落改为按面板列点，补 `subtitles.ts` / `cookieLock.ts` |
| `docs/4-plus-1-view.md` | 开发视图段落同步组件清单与纯模块 |
| `docs/requirements.md` | FR-8 的锚点 `App.tsx#L703` → `components/DownloadOptionsPanel.tsx#L19` |
| `docs/diagrams/component-overview.puml` + `.svg` | 前端包补 7 个组件/模块与 2 个基础件，边按实际 import 重写 |
| `docs/diagrams/four-plus-one-development-view.puml` + `.svg` | 同上 |
| `ai/docs/docs-prompt.md` | §3.3 前端清单重写（原写「面板函数在 App.tsx 里」已失效） |
| `ai/ui/README.md` | 已知未处理第 1 条的 `App.tsx:163` 改为按符号引用（行号会漂，符号不会） |
| `ai/perf/PLAN.md` | 两处 `frontend/src/App.tsx:1037` 标注重构后位置 |

---

## 5. 未覆盖 / 如实说明

1. **没有在真实浏览器里回归。** 本轮结论全部来自单测 + 静态取证。理由：本轮不产生任何
   CSS/DOM 变化（§4.2 第 3 条已证），而 jsdom 不做布局，跑 CDP 探针（`tmp_acceptance/ui_*.py`）
   所能量到的差异面本轮为零。**代价要说清**：若将来有人把「搬迁」误当成「可以顺手调样式」，
   这条取值方式就失效了 —— 判定标准仍是「className 与 CSS 源文是否零变化」。
2. **不变性脚本是一次性资产**（`tmp_acceptance/`，被 gitignore）。它不进 CI，所以**不能**防止未来的
   重构回退；写它只为这一轮取证。若要长期防回退，应把「CJK 文案多重集合」这类断言移植成
   `frontend/src/*.test.tsx` 里的用例（本轮未做，登记为候选）。
3. **`subtitles.ts` / `cookieLock.ts` 目前没有独立单测。** 它们的行为由 `App.test.tsx` 的集成用例
   间接覆盖（69 条里的字幕来源、字幕 fallback、锁库导入重试等）。本轮不新增测试——
   [refactor.md §3.1](refactor.md#31-目标与非目标) 第 7 条「只做结构，新增测试仅用于给新抽出的纯逻辑兜底」，
   而这两个模块是**逐字搬迁**，行为不变已由 §4.2 证明，为它们补测属于「提高覆盖」而非「本轮必要」。
4. **`App.test.tsx` 未拆分**，仍是 1521 行单文件（候选 R4）。
5. **`App.tsx` 仍有 446 行**，其中约 220 行是 14 个请求处理函数。进一步瘦身需要抽 hook
   （见 §1.2 第 6 条：那会改变状态所有权与竞态时序），不在本轮范围。
6. **`docs.py check` 的 `module-dependencies.svg`「渲染不确定」问题本轮未触发**（本轮不涉及后端模块），
   但它是已知的抖动项，见 [refactor.md §5](refactor.md#5-每轮的固定流程dod)。

---

## 6. 风险与回滚

- **线上/数据影响：无。** 本轮不动后端、不动数据库、不动 HTTP 契约、不动环境变量。
- **回滚方式**：`git revert <本轮提交 hash>`（纯新增文件 + 一处文件重写，revert 无冲突预期）。
- **已知副作用**：`frontend/src` 文件数 13 → 23。文件粒度更细，代价是「找一个组件」需要知道它属于哪个面板；
  缓解手段是 `docs/design.md` 的组件边界表（含「不负责」一列）。

## 7. 关联

- 上游依据：[refactor.md](refactor.md) §3 判定准则第 1/4/6 条、§4 路线图 R1。
- 相邻记录：[ai/ui/001](../ui/001-cookie-buttons-not-on-the-same-baseline.md)、
  [ai/ui/002](../ui/002-side-column-overflow-breaks-the-page.md)、
  [ai/ui/003](../ui/003-language-trigger-label-overflows-the-page.md) —— 三条样式约束的宿主元素
  随本轮搬进 `UrlAnalyzer.tsx` / `SearchableLanguageSelect.tsx`，约束本身未变（§4.2 已证）。
- 后续候选：`App.test.tsx` 拆分、`App.tsx` 抽 hook、把文案不变性断言移植进测试（见 §5）。

## 8. 提交与回滚

- 提交：本轮改动与本文档同在**一个 commit**，hash 按本目录约定**下次触碰时回填**
  （自我指涉，见 [refactor.md §6](refactor.md#6-轮次索引)）。查询：`git log --oneline -- ai/refactor/001-frontend-view-layer.md`。
- 回滚：`git revert <hash>`。
