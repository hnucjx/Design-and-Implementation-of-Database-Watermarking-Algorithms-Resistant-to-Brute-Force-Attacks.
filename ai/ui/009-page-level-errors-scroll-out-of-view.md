# 009 页面级报错落在视口上方，滚在任务中心的用户看不到

## 问题

任务中心一滚过一屏，页面上任何一次操作失败都只改**页面顶端**的那一行红字 —— 而那一行
此时在视口**上方**，用户什么都看不到。

复现（1440×980，6 个任务卡片，滚到底 `scrollY=1073`）：让一次「暂停」失败，然后量
「如果错误还排在原来那个位置，它会在哪儿」——把旧实现（`.alert` 在页头下方）按原样
注入回 DOM，量到：

| 量什么 | 旧位置（回退注入 `.alert`） | 修复后（`.error-banner`） |
|---|---|---|
| `getBoundingClientRect().top` | **-1019px** | **12px** |
| `bottom` | -972px（高 47px） | 59px |
| 在 980px 高的视口里吗 | 否（整条在上方一屏之外） | 是 |

用户看到的因此不是「报错」，而是「点了暂停，什么都没有发生」——这正是
[007](007-retry-grouping-looks-dead.md) 那类「一次点击至少要改变一个像素」的同一族问题，
只不过这次的范围是**所有**页面级错误，而不是归集那一条。

## 原因

`frontend/src/App.tsx` 把页面级错误渲染成 `.workspace` 里的普通块，位置在 `.topbar`
之后：

```tsx
{error && (
  <div className="alert" role="alert">
    <XCircle size={18} />
    {error}
  </div>
)}
```

`.alert` 只有 `margin-bottom` / 边框 / 底色，**没有任何定位声明**（`styles.css` 里那条规则
通篇是 `display: flex` 与配色），所以它跟着文档流走。滚动的是整个页面（`.app-shell`
没有自己的滚动容器），于是「错误在页面顶端」= 「错误在文档顶端」。

这不是某一处调用点写错：**错误写进 `error` 状态的有 11 类动作**（见下表），
它们全都落到同一个块上，所以一处排版就把它们一起埋掉了。

## 修复方案

1. 新增 [ErrorBanner.tsx](../../frontend/src/shared/ui/ErrorBanner.tsx) 取代那段内联块，
   `.error-banner` 用 **`position: sticky; top: 12px; z-index: 30`**：
   - 不滚动时它仍在页头下方（读起来还是「这一页上的操作失败了」）；
   - 一旦滚过就贴住视口顶边 —— **任何滚动位置都在视野里**。
2. **不选 `position: fixed`**：固定定位从出现那一刻就盖住内容，用户得先关掉它才能看被压住
   的东西；而且它的宽度应当跟内容列对齐（这条说的是页面级失败，不是浏览器级通知），
   固定定位在宽屏上要么与内容列错位、要么需要额外算宽度。sticky 静止时不遮任何东西。
3. **层级插在 30**：要压过语言下拉（20）与面板阴影，但必须低于上传弹窗遮罩（50）和
   右下角完成提醒（60）—— 弹窗里的错误在弹窗内就近显示，页面级回执不该浮到遮罩上面。
4. **不自动消失**：错误里常带着要照着做的信息（路径、建议、该重试还是先改设置）。
   消失只有三条路：点「关闭」、焦点在条内时按 `Esc`、**下一次动作开始时被收回**
   （`App.clearError()`，11 类动作的入口都调它 —— 旧报错描述的那次操作已经不成立，
   留着它比没有更坏）。
5. `role="alert"` + `aria-atomic="true"`，出现时不 `focus()`：读屏会立刻播报，
   但正在输入框里改地址的人不会被打断。

### 哪些报错走这条回执条（以及哪些刻意不走）

| 场景 | 入口 | 走回执条？ |
|---|---|---|
| 首屏读设置失败 | `getSettings()` | 是 |
| 解析链接失败（含浏览器 cookies 被锁的 409、锁库确认后重试失败） | `handleAnalyze` / `handleLockedBrowserCookieImport` | 是 |
| 创建任务失败 | `handleCreateJob` | 是 |
| cookies 上传 / 清除 / 从浏览器导入失败 | `handleCookieUpload` / `handleCookieDelete` / `handleBrowserCookieImport` | 是 |
| 任务中心：暂停、重启、子视频重启、仅删任务、删任务并删文件、删子视频、批量操作 | `handlePauseJob` / `handleRestartJob*` / `handleDeleteJob*` / `handleBatchAction` | 是 |
| 复制链接失败（剪贴板不可用） | `handleCopySourceLink` | 是 |
| 下载选项里的限速 / 重试保存失败 | `updateRuntimeDownloadOption` → `handleAppError` | 是（面板里另有「保存失败」逐项回执） |
| B站：打不开上传窗口、单条重试、批量重试、重试归集 | `handleBiliUpload` / `runBiliRetry` / `handleBiliRetry*` | 是 |
| 解析面板里的锁库提示、cookies 状态 | `UrlAnalyzer`（`role="status"`） | 否：**就地**显示，它解释的是输入框旁边那件事 |
| 播放 / 打开文件夹 / 打开字幕失败 | `JobQueue` 的 `.local-action-error` | 否：就近显示在**被点的那一行** |
| 上传弹窗里的表单错误 | `BiliUploadDialog`（弹窗内 `.alert`） | 否：用户要一边看自己填的内容一边改 |
| 设置项保存的「保存中 / 已保存 / 保存失败」 | `SettingsPanel` 的 `saveMessage` | 否：逐项回执，挂在对应输入框旁 |

判据是「**这条错误在说谁的事**」：说「这一页上的某个动作失败了」→ 回执条；
说「这个输入框 / 这一行 / 这个弹窗」→ 就地。

## 效果

真实浏览器（CDP，`tmp_acceptance/ui_error_banner.py`，17 项断言全过）：

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 桌面 1440×980，滚到 `scrollY=1073` 后失败 | 回执在 `top=-1019px`（视口外） | `top=12px`、`bottom=59px`，**在视口内** |
| 出现时用户正在读的那张卡片 | — | `-431px → -431px`（Δ=0；`scrollY` 由浏览器滚动锚定 1073→1138，内容不跳） |
| 连续两次失败 | — | 只有 **1** 条，内容替换成最新那条 |
| 窄屏 390×844，滚到 `scrollY≈900` | — | `top=8px`、`bottom=51px`、`left=18`、`right=372`，横向溢出 0 |
| 静止（`scrollY=0`） | 页头下方 | 仍在页头下方：`banner.top=153px ≥ topbar.bottom=129px` |

截图：[桌面（滚动后）](assets/009-desktop-scrolled.png)、
[窄屏（滚动后）](assets/009-mobile-scrolled.png)。
窄屏那张只裁了顶部一条：同一页在 390px 下还有一个**既有**缺陷（任务卡片标题被不换行的
操作按钮挤成一列单字），与本条无关，已记进 [README 的候选 3](README.md#已知但未处理的界面问题下一轮候选)。

## 验证

**自动化（jsdom，能守住的只是行为与样式源文）**

- [error-banner.test.tsx](../../frontend/src/shared/ui/error-banner.test.tsx)（7 例）：
  任务中心的失败进 `.error-banner` 且**不是**旧的 `.alert`；出现时不调 `window.scrollTo`；
  400ms 后仍在（不自动缩回）；点「关闭」消失；焦点在条内按 `Esc` 消失；
  第二次失败替换而不是叠加；**下一条动作开始时收回**；cookies 那条失败也走同一个容器。
- [styles.test.ts](../../frontend/src/styles.test.ts)（新增第 5 条不变式）：`.error-banner`
  必须是 `position: sticky` 且声明了 `top`，`z-index` 落在 (20, 50) 之间，
  `.error-banner-text` 必须 `overflow-wrap: anywhere`（错误文案里会出现 Windows 路径与接口原话，
  断不了行就会把内容列顶宽 —— 与 [002](002-side-column-overflow-breaks-the-page.md)/
  [003](003-language-trigger-label-overflows-the-page.md) 同一条判据）。

**这些断言先证明过能抓住缺陷**：把 `.error-banner` 的 `position: sticky` 临时改回
`relative`，`styles.test.ts` 立刻红（`expect(declaration).toMatch(/position:\s*sticky/)` 失败）；
旧的 `.alert` 实现下，「关闭按钮 / Esc / 替换 / 下一条动作收回」四条行为断言也都不成立。

**真实浏览器**：`tmp_acceptance/ui_error_banner.py` 17/17 通过（含回退注入的旧位置对照）。
`npx tsc --noEmit`、`npx vite build` 通过；前端全量 `22 个文件 / 299 passed`（串行，
`--no-file-parallelism`）。

## 风险与回滚

- **sticky 依赖祖先没有 `overflow` 裁切**：`.workspace` / `.app-shell` 都不设 `overflow`，
  所以它能贴到视口顶边。将来若给它们加 `overflow: hidden`（例如为了圆角），这条会**静默**
  退化成「跟着文档流走」——`styles.test.ts` 只看得见 CSS 源文，看不见这个祖先条件，
  真判据仍是探针第 [1] 段。
- **回流 47px**：条子出现时页面高度 +65px（47 高 + 18 下边距）。浏览器滚动锚定会把内容钉在
  原地（实测参照卡片 Δ=0），但**关掉锚定的浏览器**会看到内容整体下移一条的高度。
- 回滚：把 `App.tsx` 里 `<ErrorBanner …/>` 换回那段 `.alert` 内联块即可；
  `styles.css` 的 `.error-banner*` 与 `styles.test.ts` 的第 5 条不变式一并删除。

## 关联

- [007](007-retry-grouping-looks-dead.md)：同一个「回执在一千多像素之外」，那条只修了
  「重试归集」一处（就地补落款），本条修的是**所有**页面级错误的容器。
- [001](001-cookie-buttons-not-on-the-same-baseline.md) / [002](002-side-column-overflow-breaks-the-page.md) /
  [003](003-language-trigger-label-overflows-the-page.md)：同一套判据 —— 先在真实浏览器量到数字，
  再改；改完把根因写成样式源文不变式。
- [ai/bug-fix/025](../bug-fix/025-requests-without-a-deadline.md)：那条记录里已经写下
  「上一条别的动作遗留的红条会被读成我刚点的这个失败了」，本条的「动作开始时收回」是对它的回应。
- [FR-55](../../docs/requirements.md#功能需求)：需求侧的口径（页面级错误必须停在视口内）。

## 未覆盖 / 如实说明

- **sticky 在 jsdom 里量不出来**：单测只能证明「CSS 源文里写着 sticky」与「行为正确」，
  「滚到 1200 时它真的在顶部」只有 CDP 探针能证明，探针在 `tmp_acceptance/`（gitignore，不进 CI）。
- **只验了 1440×980 与 390×844**：平板竖屏（如 768×1024）、横屏矮视口（如 844×390）没跑过。
  按 CSS 推断都不会溢出（条子是 `flex` + 可断行文本，没有固定宽度），但**没有实测**。
- **没有做「长错误文案」的实测**：`overflow-wrap: anywhere` 是按 002/003 的判据加的，
  本轮没有塞一条 2000 字的错误进去看它换行后是否把视口顶高。窄屏那条实测的是正常长度文案。
- **没有覆盖读屏实播**：`role="alert"` + `aria-atomic` 是断言得到的存在性，NVDA / VoiceOver
  的实际播报未验。
