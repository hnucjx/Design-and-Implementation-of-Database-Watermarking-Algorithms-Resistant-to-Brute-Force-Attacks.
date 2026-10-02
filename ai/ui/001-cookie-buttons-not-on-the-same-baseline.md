# 001 · 「选择 cookies」与「清除 cookies」不在同一基线

- **提交**：随本记录同一次提交。commit hash 按 `ai/bug-fix/` 的同样做法在下次触碰本目录时回填
  （写在同一次提交里必然自我指涉，只能这样）。
  查本次改动：`git log --oneline -- ai/ui/001-cookie-buttons-not-on-the-same-baseline.md`
- **范围**：`frontend/src/styles.css`、`frontend/src/App.test.tsx`、`frontend/vite.config.ts`、`frontend/src/vite-env.d.ts`

## 问题

解析面板的 cookies 操作行里，「选择 cookies」与「清除 cookies」两个按钮**等宽、同高，却整体不在同一水平线上**：
前者比后者低，看起来像是没对齐、也像是两个不相干的控件被凑到一起。

用户实拍（红框内即该行）：`选择 cookies` 的上下边缘都比 `清除 cookies` 低一截。

两个控件的尺寸本身没有问题——都是 **130 × 44**，字号、边框、内边距完全一致。
也就是说，这不是「两个按钮样式不同」，而是**同一个东西被整体推下去了**。

## 原因

`frontend/src/styles.css` 里，同一个控件的**基类声明在变体之后**，两者优先级相同，于是变体的重置被静默吃掉。

| 位置（修复前） | 规则 | 关键声明 |
|---|---|---|
| 442 行 | `.compact-file-button`（变体） | `width: auto; margin-top: 0;` |
| 607 行 | `.file-button`（基类，**在后面**） | `width: 100%; margin-top: 16px;` |

两条规则的特异性都是 `0,1,0`，基类因为排在后面而胜出 —— 变体那两条重置**从未生效**。
于是 `选择 cookies` 这个 `<label>` 一直带着 `margin-top: 16px`。

而它所在的容器 `.cookie-inline-actions` 是 `display: flex; align-items: center`。
**居中作用在 margin box 上**：多出来的 16px 上外边距被平均分到上下两侧，元素因此下沉 8px，
同时把整条 flex 行从 44px 撑到 60px：

```text
修复前：变体想表达的样子            实际渲染            修复后
┌────────────┐                 ┌────────────┐        ┌────────────┐
├────────────┤ 44px            │  ↑ 8px     │        ├────────────┤
│ 选择cookies │                 │  选择      │        │ 选择cookies │
└────────────┘                 │  cookies   │        └────────────┘
   mt=0                        └────────────┘        （与右侧同顶同底）
                                mt=16px，居中后下沉 8px
```

补充一个让这处更值得修的事实：`width: 100%` 与 `margin-top: 16px` 是为**全宽表单行**写的版式，
但 `.file-button` 在整个前端只有一处调用（`App.tsx` 的解析面板），且始终带着 `compact-file-button`。
**那两条声明没有任何调用方需要，是纯粹的死代码——却足以把变体打坏。**

### 实测证据

探针：`tmp_acceptance/ui_cookie_button_align.py`（headless Edge + CDP，量盒模型与计算样式）

```text
选择 cookies： <LABEL>  top=312.19 bottom=356.19 w=130.00 h=44.00 mt=16px mb=0px
清除 cookies： <BUTTON> top=304.19 bottom=348.19 w=130.00 h=44.00 mt=0px  mb=0px
Δtop=8.0  Δbottom=8.0  Δheight=0      父容器 align-items=center
actions 行高=60.00   primary 行高=60.00
```

`Δheight=0` 且 `Δtop=Δbottom=8.0` 正是「整体平移」的签名，而不是「尺寸不同」——
这一条把问题从「两个按钮样式不一致」（错误方向）拨回到「基类版式外泄」。

## 修复方案

1. **把版式从基类里拿掉。** `.file-button` 基类只保留控件机制（`position: relative; overflow: hidden`，
   以及那条让原生 `<input type="file">` 铺满并透明的规则）。基类不再声明任何宽度与外边距，
   于是**没有任何东西可以去覆盖变体**——这比调整顺序更彻底。
2. **变体收敛为一条声明**（`min-width: 130px`），并放到基类之后；注释写明它为什么必须在那里。
3. `.cookie-clear-button` 的 `min-width: 130px` 保留，补一句说明它与「选择 cookies」是成对动作、需要等宽。
4. 加一条**单测级别的样式不变式**，盯住根因而不是结果（见下）。

### 为什么不选别的改法

- **只把变体挪到基类之后**：能修好这一处，但基类里那套「全宽 + 上外边距」的死代码还在，
  下一个用到 `.file-button` 的地方会以同样的方式中招。顺序依赖本身就是这个缺陷的成因，
  不该靠再记一条顺序约定来维持。
- **给基类加 `!important` 或提高变体特异性**（`.cookie-inline .compact-file-button`）：
  把「谁赢」变成一场军备竞赛，读者无法从声明本身看出意图。基类不该声明它并不需要的东西。
- **改动 TSX**：不需要。两个 class 名都还在，语义也没变；问题完全在 CSS 一侧。

## 效果

| | 修复前 | 修复后 |
|---|---|---|
| `选择 cookies` 的 `margin-top` | 16px | 0px |
| `选择 cookies` 上下边（1440×980） | 312.19 / 356.19 | 296.19 / 340.19 |
| `清除 cookies` 上下边 | 304.19 / 348.19 | 296.19 / 340.19 |
| **Δtop / Δbottom / Δcenter** | **8.0** | **0.0** |
| 该 flex 行高度 | 60.00（44 + 多出来的 16） | 44.00 |
| 右半组（下拉框 / 从浏览器导入） | 与左侧不同顶 | 与左侧同顶同底（Δ=0.00） |

行高回到 44px 还顺带消掉了「整行比周围高 16px」的连带瑕疵——原先右半组的
「自动检测浏览器」与「从浏览器导入」是在一个 60px 高的行里居中，与左侧两个按钮并不共线。

## 验证

### 新增断言（`App.test.tsx` → `keeps layout out of the file-button base class`）

jsdom 不做布局，量不出「对齐」，所以断言的是**根因对应的不变式**：
`.file-button` 基类不得声明纵向外边距、也不得声明宽度。它先剥掉 CSS 注释再解析规则块
（否则紧邻规则上方的注释会被当成选择器的一部分）。

**先证明这条断言真的能抓住缺陷**：把 `margin-top: 16px; width: 100%` 临时加回基类 → 该用例立即失败：

```text
× App > keeps layout out of the file-button base class
AssertionError: expected '\r\n  position: relative;\r\n  margin…'
  not to match /(?<![-\w])margin(-top|-bottom|-block|…/
```

随后还原，用例恢复通过。**没有这一步，「加了测试」不能算验证。**

配套的两处基础设施改动：

- `vite.config.ts` 打开 `test.css: true`：Vitest 默认 `css: false` 会把 CSS 换成空模块，
  **连 `./styles.css?raw` 也一起吃掉**（实测拿到长度 0 的字符串），断言会以「找不到 `.file-button`」的形式失败。
- 新增 `src/vite-env.d.ts`（`/// <reference types="vite/client" />`），让 TS 认识 `?raw` 导入，
  避免为一条样式断言引入 `@types/node`。

### 真实浏览器实测

```text
NO_PROXY=127.0.0.1,localhost python tmp_acceptance/ui_cookie_button_align.py
→ 合计：15 passed / 0 failed
```

覆盖：两个控件同高/同字号/同边框、上下边与文字中心线重合、父容器 `align-items: center`、
`margin` 对称、动作行高度等于按钮高度、右半组（下拉框、从浏览器导入）同顶同底、
行内无子元素越出左右边界、控制台无报错。

### 回归

| 项 | 结果 |
|---|---|
| 前端单测 | **65 → 66 passed**（新增 1 例） |
| `npx tsc --noEmit` | 通过 |
| 后端 `pytest -q` | **289 passed**，未受影响 |
| 浮层探针 `ui_help_popover_check.py` | **42/42 全绿**（同一片区域，确认无连带回归） |
| 文档截图 | `home.png`、`help-collapsed.png`、`help-open-desktop.png`、`help-open-mobile.png` 已按新版重拍 |

## 风险与回滚

- 影响面：只有解析面板那一行的排版。已用 `grep -rn "file-button" frontend/src` 确认
  `.file-button` 全前端**只有一处调用**，`.compact-file-button` 与 `.cookie-clear-button` 各一处。
- `test.css: true` 会让 Vitest 真正处理 CSS 导入。本项目的测试不依赖任何 CSS 副作用
  （jsdom 不算布局），实测 66 例全绿、耗时无异常。
- 回滚：`git revert <本次提交>` 即可；无数据、无接口、无持久化影响。

## 关联

- **与上一轮「辅助说明浮层」是同一类缺陷：样式的作用域没有约束住。**
  上一轮是 **CSS 继承泄漏**（浮层挂在 `white-space: nowrap` 的状态文字里，继承到不换行与粗体，
  内容被整块截掉）；这一轮是 **CSS 层叠顺序泄漏**（基类的死代码盖掉变体的重置）。
  两次都满足同一个判据：**样式规则在「不该生效的地方」生效了，而代码本身看起来完全正常。**
- 与 [`ai/bug-fix/010`](../bug-fix/010-unselectable-probe-raise-skips-the-fallback.md) 同形：
  010 是「分支写对了、触发条件写错了」，本例是「声明写对了、优先级被吃掉了」——
  写下来的东西都在，只是从来没生效过。

## 未覆盖 / 如实说明

1. **「对齐」本身没有自动化覆盖。** jsdom 不做布局，`getBoundingClientRect()` 一律返回 0，
   单测只能断言「基类不得声明版式」这条不变式，**不能**断言两个盒子共线。
   真正的判据是 CDP 探针 + 真实浏览器，属于人工/半自动验证。
2. **探针不在 CI 里。** 它放在被 gitignore 的 `tmp_acceptance/`，且本仓库没有浏览器测试基础设施；
   本轮没有把它升级为仓库脚本（那是一个独立决定，涉及新增依赖与运行环境）。
3. **只在一个视口与 DPR 下量过**：1440×980、`deviceScaleFactor: 1`。
   未验证 Windows 缩放 125%/150%（DPR≠1）与窄屏（≤620px）换行后的表现——
   窄屏下两个按钮会换行成两行，此时纵向关系由行内 flex 决定，没有单独复测。
4. **顺带发现、但本次未处理**：页面在 ≤1600 宽时存在约 **44px 的横向溢出**（右栏被推出视口）。
   已定位：右栏是固定 `390px` 轨道，而「下载选项」面板的 min-content 是 **466px**，
   作为 grid item 溢出自己的网格区域（实测面板右边缘 `1469 = 1003 + 466`，视口 `clientWidth = 1425`）。
   已确认**与本次改动无关**——用 `git show HEAD:frontend/src/styles.css` 换回旧样式复测，
   溢出同样是 `1469 / 1425`；且上一轮提交的 `help-collapsed.png` 里已经带着这条滚动条。
   它属于独立缺陷，候选 `ai/ui/002`。
