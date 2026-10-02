import { useState } from "react";
import type { FormEvent } from "react";
import { Cookie, Gauge, ListVideo, Loader2 } from "lucide-react";
import { CookieHowToPopover, CookieSection } from "./CookieSection";
import type { BrowserCookieLock } from "../cookieLock";
import type { Settings } from "../types";

const BROWSER_COOKIE_OPTIONS = [
  { value: "auto", label: "自动检测浏览器" },
  { value: "edge", label: "Edge" },
  { value: "chrome", label: "Chrome" },
  { value: "firefox", label: "Firefox" },
  { value: "brave", label: "Brave" },
  { value: "chromium", label: "Chromium" },
  { value: "vivaldi", label: "Vivaldi" },
  { value: "opera", label: "Opera" }
];

/**
 * 解析面板：链接输入、cookies 行（状态 + 上传/清除 + 浏览器导入）、锁库提示、解析按钮。
 *
 * 面板只持有「选了哪个浏览器」这类**纯界面**状态；cookies 的启用状态与锁库状态都由上层传入，
 * 因为解析失败后要拿锁去自动补一次解析（见 `App` 的 `handleLockedBrowserCookieImport`）。
 *
 * 布局约束：`.cookie-inline-primary` 里「选择 cookies」与「清除 cookies」必须共基线，
 * 改样式前先读 [ai/ui/001](../../../ai/ui/001-cookie-buttons-not-on-the-same-baseline.md) ——
 * 那条 8px 的错位来自基类规则被同优先级的变体静默吃掉。
 */
export function UrlAnalyzer({
  settings,
  url,
  duplicateWarning,
  browserCookieLock,
  isAnalyzing,
  onAnalyze,
  onBrowserCookieImport,
  onCookieDelete,
  onCookieUpload,
  onLockedBrowserCookieImport,
  onUrlChange
}: {
  settings: Settings | null;
  url: string;
  duplicateWarning: boolean;
  browserCookieLock: BrowserCookieLock | null;
  isAnalyzing: boolean;
  onAnalyze: (event: FormEvent) => void;
  onBrowserCookieImport: (browser: string) => Promise<void>;
  onCookieDelete: () => void;
  onCookieUpload: (file: File | null) => void;
  onLockedBrowserCookieImport: () => void;
  onUrlChange: (value: string) => void;
}) {
  const [browserCookieSource, setBrowserCookieSource] = useState("auto");
  const [isImportingCookies, setIsImportingCookies] = useState(false);

  async function handleBrowserImport() {
    setIsImportingCookies(true);
    try {
      await onBrowserCookieImport(browserCookieSource);
    } finally {
      setIsImportingCookies(false);
    }
  }

  return (
    <form className="panel url-panel" onSubmit={onAnalyze}>
      <div className="panel-title">
        <ListVideo size={20} />
        <div>
          <h2>解析链接</h2>
        </div>
      </div>
      <label className="field">
        <span>视频或 playlist 链接</span>
        <textarea value={url} onChange={(event) => onUrlChange(event.target.value)} rows={3} />
      </label>
      <div className="cookie-inline">
        <div className="cookie-inline-primary">
          <span className="cookie-inline-status">
            <Cookie size={16} />
            {settings?.cookies_enabled ? "已启用 cookies" : "未上传 cookies"}
            {/* 说明挂在状态文字旁，而不是混进右侧的动作按钮里 —— 它解释的是这一行，不是一个动作。 */}
            <CookieHowToPopover />
          </span>
          <div className="cookie-inline-actions">
            <label className="file-button compact-file-button">
              <span>选择 cookies</span>
              <input
                aria-label="选择 cookies"
                type="file"
                accept=".txt"
                onChange={(event) => onCookieUpload(event.target.files?.[0] ?? null)}
              />
            </label>
            <button className="ghost-button cookie-clear-button" type="button" onClick={onCookieDelete} disabled={!settings?.cookies_enabled}>
              清除 cookies
            </button>
          </div>
        </div>
        <div className="browser-cookie-import">
          <label className="compact-select-label">
            <select
              aria-label="浏览器 cookies 来源"
              className="compact-select"
              value={browserCookieSource}
              onChange={(event) => setBrowserCookieSource(event.target.value)}
            >
              {BROWSER_COOKIE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <button
            className="ghost-button"
            type="button"
            onClick={() => void handleBrowserImport()}
            disabled={isImportingCookies}
          >
            {isImportingCookies ? "导入中..." : "从浏览器导入"}
          </button>
        </div>
      </div>
      {browserCookieLock && (
        <div className="cookie-lock-note" role="status">
          <span>{browserCookieLock.message}</span>
          <button className="ghost-button" type="button" onClick={onLockedBrowserCookieImport} disabled={isImportingCookies}>
            关闭 Edge 并导入
          </button>
        </div>
      )}
      <CookieSection cookiesEnabled={Boolean(settings?.cookies_enabled)} />
      {settings && !settings.cookies_enabled && (
        <p className="hint" role="status">
          未配置 cookies 时 YouTube 媒体流 403 概率显著上升，建议先导入 cookies 再下载。
        </p>
      )}
      {duplicateWarning && <p className="hint">这个链接已经在下载历史中出现过。</p>}
      <button className="primary-button" type="submit" disabled={isAnalyzing || !url.trim()}>
        {isAnalyzing ? <Loader2 className="spin" size={18} /> : <Gauge size={18} />}
        解析链接
      </button>
    </form>
  );
}
