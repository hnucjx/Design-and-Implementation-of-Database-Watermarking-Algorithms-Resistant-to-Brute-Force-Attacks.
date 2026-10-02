import { cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, vi } from "vitest";
import type { Job } from "../types";
import {
  analyzePayload,
  cookieHealthPayload,
  diagnosticsPayload,
  jobPayload,
  lockedEdgeCookieDetail,
  pausedJobPayload,
  playlistJobPayload,
  proxyTestSuccessPayload,
  settingsPayload,
  singleFallbackJobPayload
} from "./appFixtures";

/**
 * `App` 集成测试的共享测试台。
 *
 * 这些用例原先挤在 `App.test.tsx` 一个文件里（1521 行），每个用例都靠模块级的 `let`
 * 变量与一份约 130 行的 `fetch` 替身交互。按功能拆成多个文件之后，**必须只有一份**的
 * 东西移到这里：
 *
 * - `mockState`：后端替身返回什么、以及它收到了什么；
 * - `installAppHarness()`：前后置钩子与全局替身（`fetch` / `EventSource` / `confirm` / `clipboard`）；
 * - `ensureExpanded` / `exactRow`：界面查询的辅助函数。
 *
 * 用法（测试文件顶层，收集阶段调用一次）：
 *
 * ```ts
 * import { installAppHarness, mockState } from "./test/appHarness";
 * installAppHarness();
 * ```
 *
 * `beforeEach` 会把 `mockState` 复位到出厂值，所以用例之间不会互相污染，
 * 各文件之间也互不干扰（Vitest 默认按文件隔离模块注册表）。
 */
export const mockState = {
  /** `GET /api/analyze` 返回的解析结果。 */
  analyze: analyzePayload,
  /** `GET /api/jobs` 返回的任务列表。 */
  jobs: [jobPayload, pausedJobPayload, playlistJobPayload] as Job[],
  /** `/api/settings` 的当前值（GET 读它，PUT 写它）。 */
  settings: settingsPayload,
  /** `POST /api/cookies/from-browser` 是否模拟「浏览器正在运行、cookie 库被锁」。 */
  browserCookieImportLocked: false,
  /** 下一次 `/api/analyze` 是否先返回一次「Edge 锁库」错误（用来验证解析失败后的重试）。 */
  analyzeLockedByEdgeCookies: false,
  /** 本地文件操作（播放 / 打开文件夹）的失败文案；`null` 表示成功。 */
  localFileActionFailure: null as string | null,
  /** `PUT /api/settings` 的人为延迟（毫秒），用来观察「保存中...」这一态。 */
  settingsUpdateDelayMs: 0,
  /** `PUT /api/settings` 是否返回 500。 */
  settingsUpdateShouldFail: false,
  /** `GET /api/diagnostics` 与 `POST /api/diagnostics/runtime` 的返回值。 */
  diagnostics: diagnosticsPayload,
  /** `POST /api/proxy/test` 的返回值。 */
  proxyTest: proxyTestSuccessPayload,
  /** `POST /api/cookies/verify` 的返回值。 */
  cookieHealth: cookieHealthPayload,
  /** `POST /api/proxy/test` 收到的请求体——「先试输入框里的地址」不许保存，靠它断言。 */
  proxyTestBodies: [] as Array<Record<string, unknown>>,
  /** `POST /api/cookies/verify` 收到的请求体。 */
  cookieVerifyBodies: [] as Array<Record<string, unknown>>,
  /** `POST /api/diagnostics/runtime` 被调用了几次。 */
  runtimeRefreshCount: 0
};

/** 复位到出厂值。初始值与复位值必须同源，否则「第一个用例」与「后续用例」的前提会不一致。 */
function resetMockState(): void {
  mockState.analyze = analyzePayload;
  mockState.jobs = [jobPayload, pausedJobPayload, playlistJobPayload];
  mockState.settings = settingsPayload;
  mockState.browserCookieImportLocked = false;
  mockState.analyzeLockedByEdgeCookies = false;
  mockState.localFileActionFailure = null;
  mockState.settingsUpdateDelayMs = 0;
  mockState.settingsUpdateShouldFail = false;
  mockState.diagnostics = diagnosticsPayload;
  mockState.proxyTest = proxyTestSuccessPayload;
  mockState.cookieHealth = cookieHealthPayload;
  mockState.proxyTestBodies = [];
  mockState.cookieVerifyBodies = [];
  mockState.runtimeRefreshCount = 0;
}

/** 注册共享的替身与前后置钩子。测试文件在顶层调用一次。 */
export function installAppHarness(): void {
  beforeEach(() => {
    resetMockState();
    vi.stubGlobal("confirm", vi.fn(() => true));
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: vi.fn().mockResolvedValue(undefined)
      }
    });
    vi.stubGlobal("EventSource", class {
      onmessage: ((event: MessageEvent) => void) | null = null;
      close = vi.fn();
      constructor(public url: string) {}
    });

    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url.endsWith("/api/settings") && (!init?.method || init.method === "GET")) {
          return Response.json(mockState.settings);
        }
        if (url.endsWith("/api/settings") && init?.method === "PUT") {
          if (mockState.settingsUpdateDelayMs > 0) {
            await new Promise((resolve) => window.setTimeout(resolve, mockState.settingsUpdateDelayMs));
          }
          if (mockState.settingsUpdateShouldFail) {
            return Response.json({ detail: "保存失败" }, { status: 500 });
          }
          mockState.settings = { ...mockState.settings, ...JSON.parse(String(init.body)) };
          return Response.json(mockState.settings);
        }
        if (url.endsWith("/api/settings/download-dir/select")) {
          return Response.json({ ...mockState.settings, download_dir: "D:\\Videos" });
        }
        if (url.endsWith("/api/cookies/verify") && init?.method === "POST") {
          mockState.cookieVerifyBodies.push(JSON.parse(String(init.body ?? "{}")));
          return Response.json(mockState.cookieHealth);
        }
        if (url.endsWith("/api/proxy/test") && init?.method === "POST") {
          mockState.proxyTestBodies.push(JSON.parse(String(init.body ?? "{}")));
          return Response.json(mockState.proxyTest);
        }
        if (url.endsWith("/api/diagnostics/runtime") && init?.method === "POST") {
          mockState.runtimeRefreshCount += 1;
          return Response.json(mockState.diagnostics);
        }
        if (url.endsWith("/api/diagnostics")) {
          return Response.json(mockState.diagnostics);
        }
        if (url.endsWith("/api/cookies") && init?.method === "POST") {
          mockState.settings = { ...mockState.settings, cookies_enabled: true };
          return Response.json({ enabled: true, filename: "cookies.txt" });
        }
        if (url.endsWith("/api/cookies/from-browser") && init?.method === "POST") {
          const body = JSON.parse(String(init.body));
          if (mockState.browserCookieImportLocked && !body.close_browser_if_locked) {
            return Response.json({ detail: lockedEdgeCookieDetail }, { status: 409 });
          }
          mockState.settings = { ...mockState.settings, cookies_enabled: true };
          return Response.json({
            enabled: true,
            filename: "cookies.txt",
            source: "browser",
            browser: "edge",
            imported_count: 4
          });
        }
        if (url.endsWith("/api/cookies") && init?.method === "DELETE") {
          mockState.settings = { ...mockState.settings, cookies_enabled: false };
          return Response.json({ enabled: false, filename: null });
        }
        if (url.endsWith("/api/jobs")) {
          if (init?.method === "POST") {
            return Response.json({ id: "job-1", status: "queued", total_items: 1, items: [] }, { status: 201 });
          }
          return Response.json(mockState.jobs);
        }
        if (url.endsWith("/api/jobs/batch")) {
          return Response.json({ affected_job_ids: ["job-running", "job-paused"], jobs: [] });
        }
        if (url.endsWith("/api/jobs/job-running/pause")) {
          return Response.json({ ...jobPayload, status: "paused" });
        }
        if (url.endsWith("/api/jobs/job-paused/restart")) {
          return Response.json({ ...pausedJobPayload, status: "queued" });
        }
        if (url.endsWith("/api/jobs/job-format-failed/restart")) {
          return Response.json({ ...singleFallbackJobPayload, status: "queued" });
        }
        if (
          (url.endsWith("/api/jobs/job-running/play") ||
            url.endsWith("/api/jobs/job-running/open-folder") ||
            url.endsWith("/api/jobs/job-playlist/open-folder") ||
            url.endsWith("/api/jobs/job-playlist/items/item-playlist-1/open-folder") ||
            url.endsWith("/api/jobs/job-playlist/items/item-playlist-1/play")) &&
          init?.method === "POST"
        ) {
          if (mockState.localFileActionFailure) {
            return Response.json({ detail: mockState.localFileActionFailure }, { status: 409 });
          }
          return new Response(null, { status: 204 });
        }
        if (url.endsWith("/api/jobs/job-playlist/items/delete")) {
          const body = JSON.parse(String(init?.body ?? "{}"));
          const deleted = new Set<string>(body.item_ids ?? []);
          const remainingItems = playlistJobPayload.items.filter((item) => !deleted.has(item.id));
          return Response.json({
            deleted_item_ids: Array.from(deleted),
            job_deleted: remainingItems.length === 0,
            job: remainingItems.length
              ? {
                  ...playlistJobPayload,
                  total_items: remainingItems.length,
                  items: remainingItems
                }
              : null
          });
        }
        if (url.endsWith("/api/jobs/job-playlist/items/item-playlist-2/restart")) {
          return Response.json({
            ...playlistJobPayload,
            status: "queued",
            items: [
              playlistJobPayload.items[0],
              { ...playlistJobPayload.items[1], status: "queued", progress: 0, error: null }
            ]
          });
        }
        if ((url.endsWith("/api/jobs/job-running") || url.endsWith("/api/jobs/job-running?delete_files=true")) && init?.method === "DELETE") {
          return new Response(null, { status: 204 });
        }
        if (url.endsWith("/api/analyze")) {
          if (mockState.analyzeLockedByEdgeCookies && !mockState.settings.cookies_enabled) {
            return Response.json({ detail: lockedEdgeCookieDetail }, { status: 409 });
          }
          mockState.analyzeLockedByEdgeCookies = false;
          return Response.json(mockState.analyze);
        }
        return Response.json({});
      })
    );
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });
}

/**
 * 折叠块的默认展开状态取决于业务状态（cookies 未配置时帮助内容默认展开），
 * 所以断言前先看 aria-expanded，只在收起时才点击展开——避免「点了一下反而关掉」。
 */
export async function ensureExpanded(head: HTMLElement) {
  if (head.getAttribute("aria-expanded") !== "true") {
    await userEvent.click(head);
  }
  expect(head).toHaveAttribute("aria-expanded", "true");
}

/** 一行里混了多个文本节点时，getByText 的字符串/正则匹配都不可靠，用函数匹配整行文本。 */
export function exactRow(tagName: string, text: string) {
  return (_content: string, element: Element | null) => element?.tagName === tagName && element.textContent === text;
}
