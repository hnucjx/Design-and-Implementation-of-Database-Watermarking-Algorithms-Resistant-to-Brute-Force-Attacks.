import { cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, vi } from "vitest";
import type { BiliJobUploads, Job } from "../contracts/types";
import {
  analyzePayload,
  biliJobUploadsPayload,
  biliPrefillSinglePayload,
  biliStatusPayload,
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
 * import { installAppHarness, mockState } from "../test/appHarness";
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
  /** 本地文件动作的人为延迟（毫秒），用来观察「正在打开…」这一态。 */
  localFileDelayMs: 0,
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
  runtimeRefreshCount: 0,

  /* ---------------------------------------------------------------- B站 上传 */
  /** `GET /api/bilibili/status` 的结论。出厂是「工具在、没登录」。 */
  biliStatus: biliStatusPayload,
  /** 每次体检的 `probe_account` 取值，按调用顺序记录（`true` = 真的问了 B站）。 */
  biliStatusQueries: [] as boolean[],
  /** 非 0 时状态接口延迟这么久才返回 —— 用来观察「体检中…」那一态。 */
  biliStatusDelayMs: 0,
  /** true 时**账号探测**（`probe_account=true`）永不返回，离线体检照常 —— 用来验超时兜底。 */
  biliProbeHangs: false,
  /** true 时**账号探测**（`probe_account=true`）返回 502，离线体检照常 —— 验「失败也退回离线结论」。 */
  biliStatusFails: false,
  /** true 时**两个**体检都返回 502 —— 那才是真的读不到，用来验「只剩一条红条」与旧红条的清理。 */
  biliStatusAllFails: false,
  /** `GET /api/bilibili/uploads/summary` 的任务级徽标。**出厂为空** —— 任务卡片保持原样。 */
  biliUploads: [] as BiliJobUploads[],
  /** `GET /api/bilibili/prefill` 的返回值。 */
  biliPrefill: biliPrefillSinglePayload,
  /** `POST /api/bilibili/uploads` / `collections` 完成后返回的记录。 */
  biliSubmitResult: biliJobUploadsPayload,
  /** 两条提交接口收到的请求体（用来断言「前端送上去的就是用户在表单里填的」）。 */
  biliUploadBodies: [] as Array<Record<string, unknown>>,
  biliCollectionBodies: [] as Array<Record<string, unknown>>,
  /** `POST .../retry` 与 `.../group` 收到的记录 id。 */
  biliRetryUploadIds: [] as string[],
  biliRetrySeasonIds: [] as string[],
  /** `GET /api/bilibili/prefill` 收到的两级参数。 */
  biliPrefillQueries: [] as Array<{ jobId: string | null; itemId: string | null }>,
  /**
   * `GET /api/bilibili/seasons` 的返回值。**默认可改成各种形态**：
   * 空数组 = 账号没有合集；带 `error` = 没登录；`null` = 让请求失败。
   */
  biliSeasonList: { seasons: [], error: "", advice: "" } as {
    seasons: Array<{ season_id: number; title: string; desc: string; cover_url: string; total: number }>;
    error: string;
    advice: string;
  } | null,
  /**
   * `GET /api/bilibili/seasons` 实际被调的次数。
   *
   * 这份列表是**按需读**的（只有选中「加入已有合集」才读）—— 若哪天有人把它挪回开窗路径，
   * 上面那些「列表读不到也能投稿」的用例**照样会绿**（它们只是不选那一项），
   * 于是回归会以「一打开上传窗口就多一次真实网络往返」的形式悄悄发生。这个计数专门钉住它。
   */
  biliSeasonReads: 0,
  /** 扫码轮询的下一帧结论。`confirmed` 会让界面停止轮询并重新体检。 */
  biliLoginPollState: "waiting",
  biliLoginPollMessage: "等待扫码",
  /** `POST .../retry` 的实际动作。`none` = 这条已经好了、什么都没做。 */
  biliRetryAction: "reupload" as "reupload" | "reverify" | "none",
  /** 非空时两条提交接口返回 409 并带上这句话 —— 用来测「提交被拒，表单不关、错误留在表单里」。 */
  biliSubmitFailure: null as string | null,
  /** 起过的 `EventSource`。测试用它发一条消息，验证「SSE 重拉后比对徽标」这条路。 */
  eventSources: [] as Array<{ onmessage: ((event: MessageEvent) => void) | null; close: unknown }>,
  /**
   * `POST .../group` 这一次的**实际结果**。判据是「做了什么」，不是「合集最后长什么样」：
   * 视频还在审核时那一下**连归集请求都不会发**（`waiting`），回执与配色都要跟着它走。
   */
  biliSeasonRetryAction: "grouped" as "grouped" | "failed" | "waiting",
  /** 归集重试的往返时间。非 0 时「归集中…」那一下才抓得到（真实后端要回读平台）。 */
  biliSeasonRetryDelayMs: 0 as number,
  /** `POST .../group` 返回的合集记录。 */
  biliSeason: {
    id: "season-1",
    job_id: "job-playlist",
    title: "Playlist batch",
    desc: "Playlist batch（共 2 个视频，由本机下载工作流自动上传）",
    season_id: 9001,
    status: "grouped",
    status_text: "已归入合集",
    upload_ids: ["bili-1"],
    error: null,
    advice: null
  }
};

/** 复位到出厂值。初始值与复位值必须同源，否则「第一个用例」与「后续用例」的前提会不一致。 */
function resetMockState(): void {
  mockState.analyze = analyzePayload;
  mockState.jobs = [jobPayload, pausedJobPayload, playlistJobPayload];
  mockState.settings = settingsPayload;
  mockState.browserCookieImportLocked = false;
  mockState.analyzeLockedByEdgeCookies = false;
  mockState.localFileActionFailure = null;
  mockState.localFileDelayMs = 0;
  mockState.settingsUpdateDelayMs = 0;
  mockState.settingsUpdateShouldFail = false;
  mockState.diagnostics = diagnosticsPayload;
  mockState.proxyTest = proxyTestSuccessPayload;
  mockState.cookieHealth = cookieHealthPayload;
  mockState.proxyTestBodies = [];
  mockState.cookieVerifyBodies = [];
  mockState.runtimeRefreshCount = 0;
  mockState.biliStatus = biliStatusPayload;
  mockState.biliStatusQueries = [];
  mockState.biliStatusDelayMs = 0;
  mockState.biliProbeHangs = false;
  mockState.biliStatusFails = false;
  mockState.biliStatusAllFails = false;
  mockState.biliUploads = [];
  mockState.biliPrefill = biliPrefillSinglePayload;
  mockState.biliSubmitResult = biliJobUploadsPayload;
  mockState.biliUploadBodies = [];
  mockState.biliCollectionBodies = [];
  mockState.biliRetryUploadIds = [];
  mockState.biliRetrySeasonIds = [];
  mockState.biliPrefillQueries = [];
  mockState.biliSeasonList = { seasons: [], error: "", advice: "" };
  mockState.biliSeasonReads = 0;
  mockState.biliLoginPollState = "waiting";
  mockState.biliLoginPollMessage = "等待扫码";
  mockState.biliRetryAction = "reupload";
  mockState.biliSeasonRetryAction = "grouped";
  mockState.biliSeasonRetryDelayMs = 0;
  mockState.biliSubmitFailure = null;
  mockState.eventSources = [];
  mockState.biliSeason = {
    id: "season-1",
    job_id: "job-playlist",
    title: "Playlist batch",
    desc: "Playlist batch（共 2 个视频，由本机下载工作流自动上传）",
    season_id: 9001,
    status: "grouped",
    status_text: "已归入合集",
    upload_ids: ["bili-1"],
    error: null,
    advice: null
  };
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
      constructor(public url: string) {
        mockState.eventSources.push(this);
      }
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
        if (url.includes("/open-subtitle") && init?.method === "POST") {
          // 打开字幕与播放/打开文件夹共用同一条失败通道（界面把它们都当「本机文件操作」）。
          if (mockState.localFileDelayMs) {
            await new Promise((resolve) => setTimeout(resolve, mockState.localFileDelayMs));
          }
          if (mockState.localFileActionFailure) {
            return Response.json({ detail: mockState.localFileActionFailure }, { status: 409 });
          }
          return new Response(null, { status: 204 });
        }
        if (
          (url.endsWith("/api/jobs/job-running/play") ||
            url.endsWith("/api/jobs/job-running/open-folder") ||
            url.endsWith("/api/jobs/job-playlist/open-folder") ||
            url.endsWith("/api/jobs/job-playlist/items/item-playlist-1/open-folder") ||
            url.endsWith("/api/jobs/job-playlist/items/item-playlist-1/play")) &&
          init?.method === "POST"
        ) {
          if (mockState.localFileDelayMs) {
            await new Promise((resolve) => setTimeout(resolve, mockState.localFileDelayMs));
          }
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
        // ---- B站 上传。**具体路径必须排在通配之前**，否则 `/uploads/summary` 会被
        // `/uploads` 吃掉 —— 那是一条只在运行时才暴露的错配，类型检查看不出来。
        if (url.includes("/api/bilibili/status")) {
          const probe = new URL(url, "http://localhost").searchParams.get("probe_account") !== "false";
          mockState.biliStatusQueries.push(probe);
          // 卡住的只有**账号探测**：离线那一半必须还能答上来，否则验不出「超时后退回离线结论」。
          if (probe && mockState.biliProbeHangs) return new Promise<Response>(() => {});
          if (mockState.biliStatusAllFails || (probe && mockState.biliStatusFails)) {
            // 真实后端在 B站 连不上时给的是 502 + 一句话（`upstream connect failed: …`）。
            return Response.json({ detail: "upstream connect failed: 由于目标计算机积极拒绝" }, { status: 502 });
          }
          if (mockState.biliStatusDelayMs) {
            await new Promise((resolve) => setTimeout(resolve, mockState.biliStatusDelayMs));
          }
          return Response.json(mockState.biliStatus);
        }
        if (url.endsWith("/api/bilibili/tools/repair")) {
          return Response.json(mockState.biliStatus);
        }
        if (url.endsWith("/api/bilibili/login/qrcode")) {
          return Response.json({
            qrcode_key: "qr-key-1",
            url: "https://www.bilibili.com/h5/login?qrcode_key=qr-key-1",
            // 真实的 SVG 由服务端用 `qrcode` 生成，这里给一段等价形状的替换物 ——
            // 断言只关心「有没有内联渲染它」，不关心像素。
            svg: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 3 3"><rect width="3" height="3" fill="#fff"/></svg>',
            note: null,
            expires_in: 180
          });
        }
        if (url.includes("/api/bilibili/login/poll")) {
          return Response.json({
            state: mockState.biliLoginPollState,
            message: mockState.biliLoginPollMessage,
            logged_in: mockState.biliLoginPollState === "confirmed",
            account: mockState.biliLoginPollState === "confirmed" ? { mid: 12345, name: "长沙的 HX" } : null
          });
        }
        if (url.endsWith("/api/bilibili/cookies") && init?.method === "POST") {
          return Response.json(mockState.biliStatus);
        }
        if (url.endsWith("/api/bilibili/cookies") && init?.method === "DELETE") {
          return Response.json(mockState.biliStatus);
        }
        if (url.includes("/api/bilibili/prefill")) {
          const query = new URL(url, "http://localhost").searchParams;
          mockState.biliPrefillQueries.push({
            jobId: query.get("job_id"),
            itemId: query.get("item_id")
          });
          return Response.json(mockState.biliPrefill);
        }
        if (url.endsWith("/api/bilibili/uploads/summary")) {
          return Response.json({ jobs: mockState.biliUploads });
        }
        if (url.includes("/api/bilibili/uploads/") && url.endsWith("/retry")) {
          const uploadId = url.split("/api/bilibili/uploads/")[1].replace("/retry", "");
          mockState.biliRetryUploadIds.push(uploadId);
          // 三种动作的提示各不相同 —— 混成一句会让「重新核对」看起来像「重新上传」。
          const message =
            mockState.biliRetryAction === "none"
              ? "这条已经上传成功，不需要重试。"
              : mockState.biliRetryAction === "reverify"
                ? "已重新核对。"
                : "已重新排队。";
          return Response.json({
            action: mockState.biliRetryAction,
            message,
            upload: mockState.biliSubmitResult.uploads[0] ?? null
          });
        }
        if (url.includes("/api/bilibili/collections/") && url.endsWith("/group")) {
          mockState.biliRetrySeasonIds.push(url.split("/api/bilibili/collections/")[1].replace("/group", ""));
          if (mockState.biliSeasonRetryDelayMs > 0) {
            await new Promise((resolve) => window.setTimeout(resolve, mockState.biliSeasonRetryDelayMs));
          }
          const action = mockState.biliSeasonRetryAction;
          return Response.json({
            action,
            message:
              action === "grouped"
                ? "已重新归集。"
                : action === "failed"
                  ? "归集还是没有成功"
                  : "这次没有发起归集：视频还在 B站 排队审核，暂时没法加进合集",
            season: mockState.biliSeason
          });
        }
        if (url.endsWith("/api/bilibili/seasons") && init?.method !== "POST") {
          mockState.biliSeasonReads += 1;
          if (mockState.biliSeasonList === null) {
            return Response.json({ detail: "读不到合集列表" }, { status: 500 });
          }
          return Response.json(mockState.biliSeasonList);
        }
        if (url.endsWith("/api/bilibili/uploads") && init?.method === "POST") {
          mockState.biliUploadBodies.push(JSON.parse(String(init.body ?? "{}")));
          if (mockState.biliSubmitFailure) {
            return Response.json({ detail: mockState.biliSubmitFailure }, { status: 409 });
          }
          return Response.json(mockState.biliSubmitResult, { status: 201 });
        }
        if (url.endsWith("/api/bilibili/collections") && init?.method === "POST") {
          mockState.biliCollectionBodies.push(JSON.parse(String(init.body ?? "{}")));
          if (mockState.biliSubmitFailure) {
            return Response.json({ detail: mockState.biliSubmitFailure }, { status: 409 });
          }
          return Response.json(mockState.biliSubmitResult, { status: 201 });
        }
        if (url.includes("/api/bilibili/uploads")) {
          const jobId = new URL(url, "http://localhost").searchParams.get("job_id");
          const entry = mockState.biliUploads.find((item) => item.job_id === jobId);
          return Response.json(
            entry ?? { job_id: jobId, badge: "none", badge_text: "未上传", badge_tone: "idle", uploads: [], season: null }
          );
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
