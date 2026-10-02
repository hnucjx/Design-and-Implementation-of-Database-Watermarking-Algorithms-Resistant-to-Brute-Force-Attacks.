import { ApiError } from "./api";

/**
 * 「浏览器 cookies 被锁」这一种失败的界面状态。
 *
 * `pendingAnalyzeUrl` 是触发这次失败的解析地址：用户点「关闭 Edge 并导入」之后要用它把刚才那次
 * 解析自动补上，所以必须跟着锁一起带过去。
 */
export type BrowserCookieLock = {
  browser: string;
  message: string;
  pendingAnalyzeUrl: string | null;
};

/**
 * 把后端的 `browser_locked` 错误映射成界面状态；其它错误返回 `null`（交给普通报错通道）。
 *
 * 判据是 `detail.code`，不是文案 —— 文案是可以改的，`code` 是契约。
 */
export function browserCookieLockFromError(err: unknown, pendingAnalyzeUrl: string | null): BrowserCookieLock | null {
  if (!(err instanceof ApiError) || !err.detail || typeof err.detail !== "object") {
    return null;
  }
  if (err.detail.code !== "browser_locked") {
    return null;
  }
  return {
    browser: err.detail.browser ?? "edge",
    message: err.detail.message ?? err.message,
    pendingAnalyzeUrl
  };
}
