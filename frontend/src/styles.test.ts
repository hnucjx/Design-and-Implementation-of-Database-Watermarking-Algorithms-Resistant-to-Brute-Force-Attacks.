import { describe, expect, test } from "vitest";
import stylesCss from "./styles.css?raw";
import { cssRuleBodies } from "./test/cssRules";

/**
 * 样式源文的不变式。
 *
 * 为什么单独一个文件、而且不 render：jsdom **不做布局**，`getBoundingClientRect()` 一律 0，
 * 「两个按钮对齐没有」在这里量不出来。但 `ai/ui/001`~`003` 三个界面缺陷的根因都在 CSS 里，
 * 所以能进单测的上限是**盯住根因对应的那条声明**；真判据是浏览器级 CDP 探针量像素 + 截图。
 */
describe("styles.css · 源文不变式", () => {
  // jsdom 不做布局，「两个按钮对齐没有」在这里量不出来；但那一处的根因就在 CSS 里，
  // 所以直接盯住根因对应的不变式：文件上传控件的**基类**不得声明版式。
  // 「选择 cookies」与「清除 cookies」同排而该行是 align-items: center，基类只要带上
  // 纵向外边距，就会把它从兄弟的基线上推开一半（实测 8px，见 ai/ui/001）。
  test("keeps layout out of the file-button base class", () => {
    const bodies = cssRuleBodies(stylesCss);
    const base = bodies.get(".file-button") ?? [];
    expect(base.length).toBeGreaterThan(0);
    for (const body of base) {
      // 纵向外边距：它当初就是被这个推离同排兄弟基线的（align-items: center 下偏移一半）。
      expect(body).not.toMatch(/(?<![-\w])margin(-top|-bottom|-block|-inline)?\s*:/);
      // 宽度：基类曾写着 `width: 100%`（为一种并不存在的全宽用法而写），
      // 它会以同等优先级盖掉变体的 `width: auto`。高度与 min-height 不在此列 ——
      // 它们来自与 .ghost-button 共用的控件底座，是这一排按钮成对的基础。
      expect(body).not.toMatch(/(?<![-\w])width\s*:/);
    }
  });

  // 右栏（.side-column）的轨道宽度写死在 `.grid` 里（390px，内容区只剩 352px），
  // 而 grid item 的 `min-width: auto` 不允许它被压窄：面板里只要出现一个「不可断行的
  // token」（最典型的是自检里那条 Windows 路径），面板的 min-content 就会超过轨道宽度，
  // 把整页撑出横向滚动条 —— ≤1600px 的窗口里实测 44px（见 ai/ui/002）。
  // jsdom 量不出布局，所以这里盯住真正拦住它的那条声明。
  test("keeps the fixed-width side column able to wrap its text", () => {
    const bodies = cssRuleBodies(stylesCss);
    const sideColumn = bodies.get(".side-column") ?? [];
    expect(sideColumn.length).toBeGreaterThan(0);

    // 必须是 anywhere：`overflow-wrap: break-word` 不改变 min-content，修不了这个病
    // （这一条是实测出来的，不是风格洁癖）。
    const declaration = sideColumn.join("\n");
    expect(declaration).toMatch(/overflow-wrap:\s*anywhere/);

    // 这条声明是靠继承覆盖右栏全部后代的，所以任何地方把它显式关掉（normal）都等于
    // 把缺陷重新放回来。
    expect(stylesCss).not.toMatch(/overflow-wrap:\s*normal/);
  });

  // 语言选择触发器的标签随勾选变长。这里曾经是 `white-space: nowrap` + `overflow: hidden`
  // + `text-overflow: ellipsis`，意图明显是「太长就省略号」，但那个意图**从未生效**：
  // nowrap 之下没有断行机会，span 的 min-content 就等于整段文字；它是 flex item，父级按它的
  // min-content 算宽度 → 标签放不下时不是被裁成省略号，而是把右栏轨道、整个网格一路顶宽，
  // 整页出现横向滚动条（实测 12 种语言 84px，390px 窗口 141px，见 ai/ui/003）。
  // jsdom 量不出布局，所以盯住根因对应的不变式：**必须允许换行，且不得靠 overflow 藏内容**。
  test("lets the language trigger wrap its label instead of clipping it", () => {
    const bodies = cssRuleBodies(stylesCss);
    const label = bodies.get(".select-trigger span") ?? [];
    expect(label.length).toBeGreaterThan(0);

    const declaration = label.join("\n");
    expect(declaration).toMatch(/white-space:\s*normal/);
    // 显式而不靠从 .side-column 继承：本控件是 flex item，它的 min-content 直接决定
    // 父级轨道宽度，组件一旦被移到别处就会静默退回旧病症。
    expect(declaration).toMatch(/overflow-wrap:\s*anywhere/);

    for (const body of label) {
      expect(body).not.toMatch(/white-space:\s*nowrap/);
      // 「藏起来」不等于「放得下」—— 与 002 里否掉 `min-width: 0` 是同一条理由。
      expect(body).not.toMatch(/(?<![-\w])overflow\s*:/);
      expect(body).not.toMatch(/text-overflow\s*:/);
    }
  });
});
