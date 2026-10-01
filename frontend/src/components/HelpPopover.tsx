import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { HelpCircle, X } from "lucide-react";

/**
 * 辅助说明浮层：把「说明文字」从主功能区挪出去，但不把它藏起来。
 *
 * 三条硬约束（决定了下面的实现方式）：
 * 1. **不抢焦点**：面板不开 aria-modal，打开时不 autoFocus，触发按钮按下鼠标时阻止默认行为，
 *    所以你正在输入代理地址时点开说明，光标还留在输入框里。
 * 2. **不阻塞操作**：没有遮罩层；点面板外的任何地方、按 Esc、或点右上角关闭都能立刻收起。
 *    浮层是覆盖物而不是页面的一部分，所以它不会把下面的按钮挤走。
 * 3. **展开状态可记忆**：点击展开是「钉住」，写进 localStorage；悬停展开是临时的，不写。
 *    单击外部会取消钉住，因此同一时刻最多只有一个说明处于记忆中的展开状态。
 */

const STORAGE_PREFIX = "cascade.help.open.v1.";
/** 窄屏（手机）改成底部抽屉：横向空间不够放悬浮卡，且手指没有 hover。 */
const SHEET_QUERY = "(max-width: 640px)";
const HOVER_QUERY = "(hover: hover) and (pointer: fine)";

function storedOpen(id: string): boolean {
  try {
    return window.localStorage.getItem(STORAGE_PREFIX + id) === "1";
  } catch {
    return false;
  }
}

function rememberOpen(id: string, open: boolean) {
  try {
    if (open) window.localStorage.setItem(STORAGE_PREFIX + id, "1");
    else window.localStorage.removeItem(STORAGE_PREFIX + id);
  } catch {
    // 隐私模式或存储被禁用时记忆功能降级为「本次会话有效」，不影响使用。
  }
}

function mediaMatches(query: string): boolean {
  return typeof window.matchMedia === "function" ? window.matchMedia(query).matches : false;
}

export function HelpPopover({
  id,
  label,
  title,
  children
}: {
  /** 稳定标识，同时用作展开状态的记忆键。 */
  id: string;
  /** 触发按钮上那几个字，要能独立说明「这一段是讲什么的」。 */
  label: string;
  /** 面板标题，陈述式短句。 */
  title: string;
  children: React.ReactNode;
}) {
  const panelId = useId();
  const [pinned, setPinned] = useState(() => storedOpen(id));
  const [hovered, setHovered] = useState(false);
  const [position, setPosition] = useState<React.CSSProperties>({});
  const [isSheet, setIsSheet] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const closeTimer = useRef<number | undefined>(undefined);

  const open = pinned || hovered;

  const close = useCallback(() => {
    window.clearTimeout(closeTimer.current);
    setHovered(false);
    setPinned(false);
    rememberOpen(id, false);
  }, [id]);

  const place = useCallback(() => {
    const trigger = triggerRef.current;
    const panel = panelRef.current;
    if (!trigger || !panel) return;

    if (mediaMatches(SHEET_QUERY)) {
      setIsSheet(true);
      setPosition({});
      return;
    }
    setIsSheet(false);

    const rect = trigger.getBoundingClientRect();
    const margin = 12;
    const gap = 8;
    const width = Math.min(440, window.innerWidth - margin * 2);
    const height = panel.offsetHeight;
    const spaceBelow = window.innerHeight - rect.bottom - margin - gap;
    const spaceAbove = rect.top - margin - gap;
    const below = spaceBelow >= height || spaceBelow >= spaceAbove;

    const top = below ? rect.bottom + gap : Math.max(margin, rect.top - height - gap);
    const centered = rect.left + rect.width / 2 - width / 2;
    const left = Math.min(Math.max(margin, centered), Math.max(margin, window.innerWidth - width - margin));
    const maxHeight = Math.max(180, Math.min(520, below ? spaceBelow : spaceAbove));

    setPosition({ top, left, width, maxHeight });
  }, []);

  useLayoutEffect(() => {
    if (open) place();
  }, [open, place]);

  useEffect(() => {
    if (!open) return;

    function onPointerDown(event: PointerEvent) {
      const target = event.target as Node | null;
      if (!target) return;
      if (panelRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      close();
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      close();
      triggerRef.current?.focus();
    }

    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, place, close]);

  useEffect(() => () => window.clearTimeout(closeTimer.current), []);

  function openByHover() {
    if (!mediaMatches(HOVER_QUERY)) return;
    window.clearTimeout(closeTimer.current);
    setHovered(true);
  }

  function scheduleClose() {
    window.clearTimeout(closeTimer.current);
    closeTimer.current = window.setTimeout(() => setHovered(false), 180);
  }

  function cancelClose() {
    window.clearTimeout(closeTimer.current);
  }

  function toggle() {
    cancelClose();
    if (pinned) {
      setPinned(false);
      setHovered(false);
      rememberOpen(id, false);
      return;
    }
    setPinned(true);
    rememberOpen(id, true);
  }

  return (
    <span className="help-popover">
      <button
        ref={triggerRef}
        type="button"
        className={`help-trigger${open ? " is-open" : ""}`}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        aria-label={`说明：${title}`}
        title={title}
        onClick={toggle}
        // 按下时不转移焦点：正在输入框里填地址时点开说明，光标仍留在输入框。
        onMouseDown={(event) => event.preventDefault()}
        onPointerEnter={openByHover}
        onPointerLeave={scheduleClose}
      >
        <HelpCircle size={13} />
        {label}
      </button>
      {open && (
        <div
          ref={panelRef}
          id={panelId}
          className={`help-panel${isSheet ? " is-sheet" : ""}`}
          style={position}
          role="dialog"
          aria-modal={false}
          aria-label={title}
          onPointerEnter={cancelClose}
          onPointerLeave={scheduleClose}
        >
          <div className="help-panel-head">
            <span className="help-panel-title">{title}</span>
            <button type="button" className="help-panel-close" aria-label={`关闭「${title}」`} onClick={close}>
              <X size={14} />
            </button>
          </div>
          <div className="help-body">{children}</div>
        </div>
      )}
    </span>
  );
}
