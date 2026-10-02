# 002 · 右栏把整页撑出横向滚动条（≤1600px 时约 44px）

- **提交**：随本记录同一次提交。commit hash 与 `ai/ui/001` 同样处理，下次触碰本目录时回填。
  查本次改动：`git log --oneline -- ai/ui/002-side-column-overflow-breaks-the-page.md`
- **范围**：`frontend/src/styles.css`、`frontend/src/App.test.tsx`
- **性质**：是缺陷。判定依据不是「不好看」，而是**页面多了一条横向滚动条**：
  内容超出一屏、可被横向拖动，属于版式失效，不是审美偏好。

## 问题

窗口宽度 ≤1600px 时，页面底部出现横向滚动条，右栏（下载选项 / 设置）被推出视口右侧。
1728px 及以上正常。

实测（`tmp_acceptance/ui_layout_overflow.py`，headless Edge + CDP）：

| 视口宽 | `documentElement.clientWidth` | `scrollWidth` | 横向溢出 |
|---|---|---|---|
| 1280 | 1265 | 1309 | **44.00** |
| 1366 | 1351 | 1395 | **44.00** |
| 1440 | 1425 | 1469 | **44.00** |
| 1520 | 1505 | 1548 | **43.00** |
| 1600 | 1585 | 1588 | **3.00** |
| 1728 | 1713 | 1713 | 0.00 |

溢出量随窗口变宽而收敛到 0，说明这是一个**固定宽度的东西**在顶，而不是百分比计算错。

## 原因

**右栏是写死 390px 的网格轨道，但它里面的文本没有断行机会 —— 面板的 min-content 超过轨道宽度，
而 grid item 不允许被压窄，于是溢出被算进整页。**

三步实测（1440px）：

```text
① 轨道： .grid 的列 = "951px 390px"，gap 20px，内容区 [32 → 1393]
          .side-column  [1003 → 1393]  w=390        ← 轨道本身是对的
② 面板： .options-panel [1003 → 1468.53] w=465.53    ← 右缘越过轨道 75.53px
③ 溢出： 1468.53 - 1425(clientWidth) = 43.53 ≈ 44px  ← 恰好是那 44px
```

为什么面板是 465.53 而不是 390？把右栏的每个后代替换成 `position:absolute; width:min-content`
量「无论如何换行都不可能更窄」的宽度（`tmp_acceptance/ui_overflow_culprit.py`），得到一条清晰的链：

```text
465.53  <section class="panel compact-panel">   设置面板
427.53  └─ <div class="proxy-section">
427.53     └─ <div class="runtime-block is-ok">
401.53        └─ <p class="hint">               ← 元凶在这里
              'node　C:\Users\hfwei\.workbuddy\binaries\node\versions\22.22.2-5\node.EXE…'
```

算术闭合：`401.53 + 2×12(padding) + 2×1(border) = 427.53` → `427.53 + 2×18 + 2×1 = 465.53`。
面板的内容区只有 352px，而这一行的 min-content 是 401.53px —— **它自己就比面板还宽。**

这一行是 `ProxySection.tsx` 里 JS 运行时自检的路径回显：

```tsx
<p className="hint">
  {String(runtime?.dependencies?.js_runtime_name ?? "")}　{runtime?.dependencies?.js_runtime_path as string}　
  v{runtime?.dependencies?.js_runtime_version as string}
</p>
```

`.hint` 的计算样式是 `overflow-wrap: normal`（实测），而 Windows 路径 `C:\Users\…\node.EXE`
**没有任何换行机会**（反斜杠不是断行点），所以它的 min-content 就是整条路径的宽度。
同一份 `ProxySection.tsx` 里紧邻的「日志文件：`<code>…</code>`」反而没这个问题 —— 因为它被包在
`<code>` 里，而仓库给代码块配了 `word-break: break-all`。**这行路径是唯一漏掉的裸文本。**

### 为什么它只在 ≤1600 宽发作

`.workspace` 是 `width: min(1440px, 100%)`，居中。窗口 ≥1728 时两侧留白足够，
面板越出的那 75.53px 还落在视口内；窗口一窄，右缘就被推出视口，溢出才**可见**。
缺陷一直在（面板宽度恒为 465.53），只是宽度够大时被留白吸收掉了。

## 修复方案

```css
/* 右栏的轨道宽度是写死的 390px（内容区只剩 352px），但它是「内容说了算」的一列：
   面板里只要出现一个不能断行的 token（最典型的是 Windows 路径，例如自检里的
   `...\node\versions\22.22.2-5\node.exe`），面板的 min-content 就会超过轨道宽度，
   而 grid item 的 `min-width: auto` 不允许它被压窄，于是面板连同整页一起被撑宽……
   唯一能同时满足「轨道不变」与「内容不丢」的写法是让这条轨道里的文本可断行。
   `overflow-wrap` 会继承，所以声明在列上即可覆盖全部后代，不必逐个容器去补。
   注意必须是 `anywhere` 而不是 `break-word`：后者不改变 min-content，修不了这个病。 */
.side-column {
  overflow-wrap: anywhere;
}
```

一条声明。理由逐条对应上面的诊断：

- **修在列上、而不是修在 `.hint` 上**：`overflow-wrap` 是**继承属性**，声明在 `.side-column`
  即覆盖右栏全部后代。这条轨道里任何一个容器（`.hint`、`.diag-facts li`、`.runtime-head`、
  裸 `<code>`、`.panel h2`）都是同一处境 —— 逐个补等于把「固定轨道里的文本必须可断行」
  这条规则拆成五六处去维护。见下面 [验证](#验证) 里的毒性测试：注入一个 120 字符不可断 token 进
  这四类容器，全部不再溢出。
- **必须是 `anywhere`，不能是 `break-word`**：`overflow-wrap: break-word` **不改变 min-content**
  （它只在排版阶段断，不参与内在尺寸计算），而本缺陷恰恰是 min-content 问题。这是实测出来的差别，
  不是风格偏好 —— 见 [为什么没选别的改法](#为什么没选别的改法) 的候选 B。
- **不改轨道宽度**：把 390px 加宽到 466px 也能消掉溢出，但那只对**这一台机器**的路径长度有效 ——
  路径长度由用户装在哪决定，换台机器或换个 Node 版本又会溢出一次。而且右栏变宽意味着
  主栏（唯一的 1fr）被压缩，等于让一个偶发的内容问题永久收窄主功能区的版面。

### 为什么不选别的改法

三个候选都在真实浏览器里量过（`tmp_acceptance/ui_overflow_experiment.py`、`ui_candidate_d.py`）。

| 候选 | 内容 | 1440px 实测 | 结论 |
|---|---|---|---|
| **A（采纳）** | `.side-column { overflow-wrap: anywhere }` | `overflowX = 0`，面板 390 | 采纳 |
| B | 结构护栏：`.grid > *` 与 `.side-column > *` 都加 `min-width: 0` | 1280/1440 残留 **12px**，且**面板内出现 3 处溢出**（`scrollWidth 433 > clientWidth 388`） | 弃 |
| C | A + B | 与本例同为 0 | 冗余，无收益 |
| D | A + 只给右栏加 `min-width: 0` | 长路径场景 0；但 nowrap 场景仍 **452px**，且内容被画到面板之外 | 弃 |

**B 为什么要不得**：`min-width: 0` 让盒子「能比内容窄」，但它不改变内容的 min-content。
结果是溢出没有被修掉，只是从「整页横向滚动」（看得见）变成「内容画在面板外/面板内滚动」
（看不见）——而且实测还额外在**左栏**引入了 12px 的新溢出。
**把可见的缺陷换成不可见的缺陷不算修好**，这也是本轮探针专门加了一条
「侧栏内不许有 `scrollWidth > clientWidth`」断言的原因。

**D 为什么也不选**：它能把面板压回 390px，但 nowrap 文本的盒子**仍然宽 777px**
（`overflow: hidden` 只裁绘制、不改几何），于是整页照样溢出 431px，只是内容被裁掉看不全。

## 效果

| | 修复前 | 修复后 |
|---|---|---|
| 1280 / 1366 / 1440 / 1520 / 1600 的横向溢出 | 44 / 44 / 44 / 43 / 3 | **0 / 0 / 0 / 0 / 0** |
| `.options-panel` 宽度 | 465.53 | 390.00 |
| 面板右缘 vs grid 右缘（1440px） | 1468.53 vs 1393.00（越出 75.53） | 1393.00 vs 1393.00 |
| 自检路径那一行 | 单行 401.53px，撑破面板 | 折成 2 行，高 48px，右缘 1372（在面板内） |
| 面板内溢出（`scrollWidth > clientWidth`） | 0 | 0（未用「藏起来」换「不溢出」） |
| 1020 / 900 / 640 / 480 / 390 / 360 窄屏 | 1024 时溢出 44 | 全部 0 |
| 内容（文字）是否丢失 | 不丢，但需要横向拖动才看得到 | 不丢，就地折行 |

## 验证

### 新增断言（`App.test.tsx` → `keeps the fixed-width side column able to wrap its text`）

jsdom 不做布局，量不出「页面有没有横向滚动条」，所以断言的是**根因对应的不变式**：
`.side-column` 必须声明 `overflow-wrap: anywhere`（而不是 `break-word`），
且样式表里任何地方都不得出现 `overflow-wrap: normal`（那等于把洞重新打开）。
它复用本次一并提取出来的 `cssRuleBodies()` 解析辅助函数。

**先证明这条断言真的能抓住缺陷**：把 `anywhere` 临时改回 `normal` → 该用例立即失败：

```text
× App > keeps the fixed-width side column able to wrap its text
AssertionError: expected '\r\n  display: grid;\r\n  gap: 18px;\r\n\r\n\r\n  overflow-wrap: normal;\r\n'
  to match /overflow-wrap:\s*anywhere/
```

### 真实浏览器实测（`tmp_acceptance/ui_layout_overflow.py`）

```text
共 26 项断言：通过 26，失败 0
```

覆盖：6 个桌面宽度 + 6 个窄屏宽度的无横向溢出；面板不越过 grid 右缘；
侧栏内无内部溢出（表单控件除外，理由见下）；**毒性测试**（往 `.hint` / `.runtime-head` /
`.field > span` / 临时构造的 `.diag-facts li` 里各塞一个 120 字符不可断 token，1280/1440/1600 仍为 0）；
以及**承重测试**：把 `overflow-wrap` 临时改回 `normal`，1440px 的溢出错确重现为 `44.00` ——
这证明前面 24 项通过**确实来自这一条声明**，不是别的什么在兜着。

> 表单控件被排除在「内部溢出」检查之外：`<input>` 的 `scrollWidth` 量的是**值文字在输入框里滚动了多少**，
> 不是版式溢出。下载目录那一行的长路径就量到 `scrollWidth 284 > clientWidth 216`，
> 那是输入框的正常行为，不该被算成缺陷。

### 回归

| 项 | 结果 |
|---|---|
| 前端单测 | **66 → 67 passed**（新增 1 例） |
| 后端 `pytest -q` | **289 passed**，未受影响 |
| `npx tsc --noEmit` | 通过 |
| 浮层探针 `ui_help_popover_check.py` | **42/42 全绿**（`overflow-wrap` 会继承进浮层子树，专门复测） |

## 风险与回滚

- 影响面：`.side-column` 的全部后代文本。`overflow-wrap: anywhere` 只在**某个词单独放不进一行时**
  才把它断开，因此对现状只有两类可见变化：
  1. 原本撑破版面的长 token（路径、URL、错误原文）就地折行；
  2. 本来就不长于一行的文本 —— **零变化**。
- 唯一被这条声明改变外观的是自检那一行路径（从撑破面板变成折成两行），以及所有同类长 token。
- 不涉及数据、接口与持久化。
- 回滚：`git revert <本次提交>`。

## 关联

- **与 `ai/ui/001` 是同一类失败：固定盒子里「本该生效的东西没生效」。**
  001 是基类版式盖掉了变体（层叠顺序），本例是让内容能缩进轨道的属性根本没写（缺声明）。
  共同判据：**盒子看起来是约束好的，但约束并没有真正作用在内容上。**
- 与 [`ai/bug-fix/007`](../bug-fix/007-js-challenge-fails-only-when-cookies-are-on.md) 提到的浮层横向溢出同族：
  两处都是「窄容器 + 内容不能换行」。007 的结论是「浮层不能假设触发点附近的排版是干净的」，
  本条的结论是「**固定宽度的轨道不能假设里面的文本是短的**」。

## 未覆盖 / 如实说明

1. **「整页有没有横向滚动条」没有自动化覆盖。** jsdom 不做布局，单测只能盯住那条声明本身；
   真正的判据是 CDP 探针（放在被 gitignore 的 `tmp_acceptance/`，不在 CI 里）。
2. **只在 `deviceScaleFactor: 1` 下量过**，未验证 Windows 缩放 125%/150%（DPR ≠ 1）。
   折行位置会变，但 min-content 的性质与 DPR 无关。
3. **只测了这台机器的路径长度**（`C:\Users\hfwei\.workbuddy\binaries\node\versions\22.22.2-5\node.EXE`，401.53px）。
   换一个更长的路径（例如 `C:\Program Files\…`）修复后同样不会溢出，因为 `anywhere` 把 min-content
   压到了 1 个字符量级；但**我没有在别的机器上复测**。
4. **右栏里还有一个同成因、本轮不修的缺口**：语言选择器的触发文字是 `white-space: nowrap`
   （`.select-trigger span`）。nowrap 之下没有任何断行机会，`overflow-wrap: anywhere` 对它无效，
   `min-width: 0` 也无效（实测触发器 min-content 不变）。实测：选中 **4 种**字幕语言时正常（min-content 298.13），
   **6 种**时溢出 **49px**，8 种 173px，12 种 **452px**。
   修它必须动设计（`white-space: normal` 让触发器变多行，或在 JS 侧提前截断字符串），
   属于独立的决定，已登记在 [`README`](README.md) 的「已知但未处理」里，候选 `ai/ui/003`。
   本轮探针把它作为**已知项单独打印、不断言**，不混进「已修复」。
5. **左侧主栏没有做同样的检查。** 它的轨道是 `minmax(0, 1fr)`（本来就允许被压窄），
   且 `.job-main h3 / p` 已经声明了 `overflow-wrap: anywhere`，本轮未系统排查其余容器。
6. **验证门槛里的一项检查本身是抖的（与本改动无关，本轮不修）。**
   `python scripts/docs.py check` 会以约 50% 的概率报
   `UML SVG 需要重新渲染：docs\assets\diagrams\module-dependencies.svg`。
   实测证据：同一份源码连续 6 次独立渲染，该图产出**两种**字节（43914 / 43988，各 3 次），
   其中 43988 正是已提交的版本；而同批抽样的 `component-overview` 与
   `four-plus-one-development-view` 6 次全同。连续跑三次 `docs.py check`，结果是
   **通过 / 失败 / 通过**。也就是说这张图的渲染**不确定**，「SVG 与源一致」这条断言对它无效，
   只能算掷硬币 —— 这也是它此前偶尔「通过」的原因。本轮未触碰任何 `.puml` / `.svg`，
   且工作区里这两个文件与 `HEAD` 完全一致（`git status` 为空），可确认与本改动无关。
   修它（把比较改成规范化后比、或给该图换确定性布局）是独立决定，未纳入本次提交。
