/**
 * 把一份 CSS 源文拆成「选择器 → 规则体列表」。
 *
 * 只服务于样式不变式断言。为什么需要它：jsdom **不做布局**，
 * `getBoundingClientRect()` 一律返回 0，所以「两个按钮在不在同一条基线上」「面板有没有
 * 被一个长 token 撑宽」这类问题在单测里永远量不出来（见 `ai/ui/001`~`003`）。
 * 能进单测的上限是**样式源文的不变式**——例如「文件上传控件的基类不得声明版式」，
 * 而那正好是当时那个缺陷的根因。
 *
 * 真实判据仍是浏览器级探针（CDP 量像素 + 截图），这里只是防止根因被静默改回去。
 */
export function cssRuleBodies(source: string): Map<string, string[]> {
  // 注释必须先剥掉，否则紧邻规则上方的注释会被当成选择器的一部分，找不到 `.file-button` 之类。
  const stripped = source.replace(/\/\*[\s\S]*?\*\//g, "");
  const bodies = new Map<string, string[]>();
  for (const match of stripped.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    for (const raw of match[1].split(",")) {
      const selector = raw.trim();
      if (selector) bodies.set(selector, [...(bodies.get(selector) ?? []), match[2]]);
    }
  }
  return bodies;
}
