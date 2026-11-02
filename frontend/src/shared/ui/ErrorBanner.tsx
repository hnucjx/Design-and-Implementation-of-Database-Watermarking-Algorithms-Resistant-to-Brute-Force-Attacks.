import { X, XCircle } from "lucide-react";

/**
 * 页面级错误的回执条。
 *
 * **为什么不继续放在页面顶端**：它原来就排在页头下面、跟着文档流走。任务中心一滚到
 * 1000px 以下，那条红字就落在视口**上方之外** —— 用户既没看到报错，也没看到别的东西
 * 在变，只会以为「既没报错、也没进展」（见 [ai/ui/009](../../../ai/ui/009-page-level-errors-scroll-out-of-view.md)）。
 * 所以这里改成 `position: sticky`：不滚动时它仍在页头下方（读起来还是「这一页的操作失败了」），
 * 一旦滚过就贴住视口顶边，**任何滚动位置都在视野里**。
 *
 * **为什么不 `position: fixed`**：固定定位从出现那一刻就盖住内容，而这条提示的宽度应当
 * 跟内容列对齐（它说的是页面上的操作失败了，不是浏览器级通知）。sticky 只在被滚过之后
 * 才接管顶部，静止时仍占位、不遮任何东西；窄屏上也不需要为它额外留白。
 *
 * **为什么不自动消失**：错误里常常带着要照着做的信息（路径、建议、该重试还是先改设置）。
 * 定时缩回等于「你还没读完它就走了」，比不显示更坏。消失只有三条路：用户点关闭、
 * 焦点在条内时按 Esc、或者下一次动作开始时被收回（见 `App` 里的 `dismissError`）。
 *
 * **不抢焦点**：出现时不 `focus()` —— 正在输入框里改地址的人会被打断。`role="alert"`
 * 让读屏立刻播报；视觉用户靠位置、颜色和「它一直不走」注意到它。
 */
export function ErrorBanner({
  message,
  onDismiss
}: {
  message: string | null;
  onDismiss: () => void;
}) {
  if (!message) return null;
  return (
    <div
      className="error-banner"
      role="alert"
      aria-atomic="true"
      onKeyDown={(event) => {
        // 只在焦点已经在条内（关闭按钮上）时响应：全局 Esc 监听会和说明浮层、
        // 上传弹窗抢同一个键。
        if (event.key === "Escape") onDismiss();
      }}
    >
      <XCircle size={18} className="error-banner-icon" aria-hidden="true" />
      <span className="error-banner-text">{message}</span>
      <button type="button" className="error-banner-close" aria-label="关闭错误提示" onClick={onDismiss}>
        <X size={16} aria-hidden="true" />
      </button>
    </div>
  );
}
