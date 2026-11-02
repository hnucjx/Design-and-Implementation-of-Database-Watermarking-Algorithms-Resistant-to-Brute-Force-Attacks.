import { GROUP_MODE_EXISTING } from "../bilibili/biliForm";
import type {
  AnalyzeResponse,
  ApiErrorDetail,
  BiliGroupMode,
  BiliJobUploads,
  BiliJobUploadsSummary,
  BiliLoginPoll,
  BiliMetadata,
  BiliPrefill,
  BiliQrcode,
  BiliRetry,
  BiliSeasonList,
  BiliSeasonRetry,
  BiliStatus,
  CookieHealth,
  CookieStatus,
  DeleteJobItemsResponse,
  Diagnostics,
  DownloadOptions,
  Job,
  JobBatchAction,
  JobBatchActionResponse,
  ProxyTestResult,
  Settings
} from "../contracts/types";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public detail: ApiErrorDetail | string | null
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * 一次请求最多等多久。**这是「永远不返回」的兜底，不是性能预算** —— 取值要比任何正常的
 * 服务端耗时都长，所以它只在真的挂住时才触发。
 *
 * 为什么需要它：`fetch` 本身没有超时。后端把连接接下来却不回话（后端卡死、DNS 挂起、
 * 代理黑洞）时，Promise **永不 settle**，界面就是一条永远不结束的 loading ——
 * 用户看到的不是错误，而是一句「正在读取…」永远不消失。这类失败没有任何提示，
 * 只能靠调用方自己计时（见 ai/bug-fix/025）。
 *
 * 60 s 的依据：服务端最长的一次「合法阻塞」是「打开方式」窗口的出现判定（6 s）与
 * 浏览器 cookie 库的等待（约 10 s），B站 请求自带 20 s 超时，都远在 60 s 之内。
 */
export const REQUEST_TIMEOUT_MS = 60_000;

/**
 * 解析链接的预算。它要真的连 YouTube（`extract_metadata` + 可能的 cookies 导入重试 +
 * JS challenge 求解），实测可以到半分钟以上，所以单独放宽 —— 但对「永不返回」同样必须封顶。
 */
export const ANALYZE_TIMEOUT_MS = 180_000;

function timeoutError(url: string, timeoutMs: number, label?: string): ApiError {
  const seconds = Math.round(timeoutMs / 1000);
  // **必须点名是哪个动作**：错误横幅是页面级的，上一条别的动作遗留的红条会一直挂着，
  // 用户会把它读成「我刚点的这个失败了」——实测有人把「重试投稿超时」读成「扫码登录失败」。
  // 只给 URL 不够：`/api/bilibili/uploads/<id>/retry` 里没有一个字是用户认识的。
  const what = label ? `「${label}」` : "";
  return new ApiError(
    `${what}请求超时：${seconds} 秒内后端没有响应（${url}）。` +
      "这不代表请求失败，而是连「失败」都没回来 —— 后端可能卡住、代理或 DNS 挂起。" +
      "可以先看 data/logs/app.log 的最后一条，或重启后端再试。",
    0,
    null
  );
}

async function request<T>(
  url: string,
  init?: RequestInit,
  timeoutMs: number = REQUEST_TIMEOUT_MS,
  label?: string
): Promise<T> {
  // 超时和「用户主动取消」必须分得开：前者要报一句能照做的话，后者应该安静地结束。
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      headers: init?.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
      ...init,
      signal: controller.signal
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      const detail = payload?.detail ?? null;
      if (detail && typeof detail === "object") {
        throw new ApiError(detail.message ?? `Request failed with ${response.status}`, response.status, detail);
      }
      throw new ApiError(detail ?? `Request failed with ${response.status}`, response.status, detail);
    }
    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  } catch (error) {
    // `AbortError` 只可能来自我们自己（调用方目前都不传 signal），所以直接翻译成超时。
    if (controller.signal.aborted) {
      throw timeoutError(url, timeoutMs, label);
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

export function getSettings(): Promise<Settings> {
  return request<Settings>("/api/settings");
}

export function updateSettings(settings: Partial<Settings>): Promise<Settings> {
  return request<Settings>("/api/settings", {
    method: "PUT",
    body: JSON.stringify(settings)
  });
}

export function selectDownloadDirectory(): Promise<Settings> {
  return request<Settings>("/api/settings/download-dir/select", { method: "POST" });
}

export function analyzeUrl(url: string): Promise<AnalyzeResponse> {
  return request<AnalyzeResponse>(
    "/api/analyze",
    {
      method: "POST",
      body: JSON.stringify({ url, cookies_enabled: true })
    },
    ANALYZE_TIMEOUT_MS
  );
}

export function listJobs(): Promise<Job[]> {
  return request<Job[]>("/api/jobs");
}

export function createJob(url: string, options: DownloadOptions): Promise<Job> {
  return request<Job>("/api/jobs", {
    method: "POST",
    body: JSON.stringify({ url, options })
  });
}

export function cancelJob(jobId: string): Promise<Job> {
  return request<Job>(`/api/jobs/${jobId}/cancel`, { method: "POST" });
}

export function pauseJob(jobId: string): Promise<Job> {
  return request<Job>(`/api/jobs/${jobId}/pause`, { method: "POST" });
}

export function restartJob(jobId: string, resolution?: string): Promise<Job> {
  return request<Job>(`/api/jobs/${jobId}/restart`, restartRequest(resolution));
}

export function restartJobItem(jobId: string, itemId: string, resolution?: string): Promise<Job> {
  return request<Job>(`/api/jobs/${jobId}/items/${itemId}/restart`, restartRequest(resolution));
}

export function playJobVideo(jobId: string): Promise<void> {
  return request<void>(`/api/jobs/${jobId}/play`, { method: "POST" });
}

export function openJobFolder(jobId: string): Promise<void> {
  return request<void>(`/api/jobs/${jobId}/open-folder`, { method: "POST" });
}

export function playJobItemVideo(jobId: string, itemId: string): Promise<void> {
  return request<void>(`/api/jobs/${jobId}/items/${itemId}/play`, { method: "POST" });
}

export function openJobItemFolder(jobId: string, itemId: string): Promise<void> {
  return request<void>(`/api/jobs/${jobId}/items/${itemId}/open-folder`, { method: "POST" });
}

/**
 * 打开下载到的字幕文件。
 *
 * 不带 `mode`：服务端的缺省就是「弹系统的『打开方式』窗口」—— 界面只有这一个动作，
 * 于是这个决定只写在服务端一处，前端不跟它各写一遍。
 * **程序永远不由前端指定** —— 否则前端就得知道用户机器上装了什么（后端也不必知道）。
 */
export function openJobSubtitle(jobId: string): Promise<void> {
  return request<void>(`/api/jobs/${jobId}/open-subtitle`, { method: "POST" });
}

export function openJobItemSubtitle(jobId: string, itemId: string): Promise<void> {
  return request<void>(`/api/jobs/${jobId}/items/${itemId}/open-subtitle`, { method: "POST" });
}

export function deleteJob(jobId: string, deleteFiles = false): Promise<void> {
  const query = deleteFiles ? "?delete_files=true" : "";
  return request<void>(`/api/jobs/${jobId}${query}`, { method: "DELETE" });
}

export function deleteJobItems(jobId: string, itemIds: string[], deleteFiles = false): Promise<DeleteJobItemsResponse> {
  return request<DeleteJobItemsResponse>(`/api/jobs/${jobId}/items/delete`, {
    method: "POST",
    body: JSON.stringify({ item_ids: itemIds, delete_files: deleteFiles })
  });
}

export function batchJobAction(action: JobBatchAction, jobIds: string[], deleteFiles = false): Promise<JobBatchActionResponse> {
  return request<JobBatchActionResponse>("/api/jobs/batch", {
    method: "POST",
    body: JSON.stringify({ action, job_ids: jobIds, delete_files: action === "delete" ? deleteFiles : false })
  });
}

export function uploadCookies(file: File): Promise<CookieStatus> {
  const data = new FormData();
  data.append("file", file);
  return request<CookieStatus>("/api/cookies", {
    method: "POST",
    body: data
  });
}

export function importBrowserCookies(browser: string, closeBrowserIfLocked = false): Promise<CookieStatus> {
  return request<CookieStatus>("/api/cookies/from-browser", {
    method: "POST",
    body: JSON.stringify({ browser, close_browser_if_locked: closeBrowserIfLocked })
  });
}

export function deleteCookies(): Promise<CookieStatus> {
  return request<CookieStatus>("/api/cookies", { method: "DELETE" });
}

/**
 * 用当前生效的代理真实探测一次 YouTube。
 *
 * `proxy` 传 undefined = 用已保存的设置；传具体值 = 先试这个地址（不保存）。
 */
export function testProxy(proxy?: string): Promise<ProxyTestResult> {
  return request<ProxyTestResult>("/api/proxy/test", {
    method: "POST",
    body: JSON.stringify(proxy === undefined ? {} : { proxy })
  });
}

/** 校验 cookies 是否真的能登录。deep=false 只做离线体检（不联网）。 */
export function verifyCookies(deep = true): Promise<CookieHealth> {
  return request<CookieHealth>("/api/cookies/verify", {
    method: "POST",
    body: JSON.stringify({ deep })
  });
}

/** 重新做一次 JS 运行时/依赖探测（装完 Node/Deno 之后用）。 */
export function refreshRuntimeDiagnostics(): Promise<Diagnostics> {
  return request<Diagnostics>("/api/diagnostics/runtime", { method: "POST" });
}

export function getDiagnostics(): Promise<Diagnostics> {
  return request<Diagnostics>("/api/diagnostics");
}

// ---------------------------------------------------------------- B站 上传
/**
 * B站 上传的环境体检。
 *
 * `probeAccount=false` 只做离线体检（不请求 B站）：没有网络时也能看出「工具装没装、登录文件在不在」。
 */
export function getBiliStatus(probeAccount = true): Promise<BiliStatus> {
  return request<BiliStatus>(
    `/api/bilibili/status?probe_account=${probeAccount ? "true" : "false"}`,
    undefined,
    REQUEST_TIMEOUT_MS,
    probeAccount ? "重新体检" : "读取 B 站 状态"
  );
}

/** 修复 WinGet `Links` 下那个 0 字节的 `biliup.exe` 空壳，然后返回重新体检的结论。 */
export function repairBiliTool(): Promise<BiliStatus> {
  return request<BiliStatus>("/api/bilibili/tools/repair", { method: "POST" });
}

/** 生成扫码登录二维码。返回 **SVG 文本**（内联渲染），不是位图。 */
export function createBiliQrcode(): Promise<BiliQrcode> {
  return request<BiliQrcode>("/api/bilibili/login/qrcode", { method: "POST" }, REQUEST_TIMEOUT_MS, "扫码登录");
}

export function pollBiliLogin(qrcodeKey: string): Promise<BiliLoginPoll> {
  return request<BiliLoginPoll>(`/api/bilibili/login/poll?qrcode_key=${encodeURIComponent(qrcodeKey)}`);
}

/** 导入一份已有的登录文件（`biliup` 的 LoginInfo 或 Netscape cookies.txt 都能认）。 */
export function uploadBiliCookies(file: File): Promise<BiliStatus> {
  const data = new FormData();
  data.append("file", file);
  return request<BiliStatus>("/api/bilibili/cookies", { method: "POST", body: data });
}

export function deleteBiliCookies(): Promise<BiliStatus> {
  return request<BiliStatus>("/api/bilibili/cookies", { method: "DELETE" });
}

/** 预填上传表单：单视频给一组值，合集给「模板 + 逐条预览 + 超限定位」。 */
export function getBiliPrefill(jobId: string, itemId?: string | null): Promise<BiliPrefill> {
  const query = itemId ? `&item_id=${encodeURIComponent(itemId)}` : "";
  return request<BiliPrefill>(`/api/bilibili/prefill?job_id=${encodeURIComponent(jobId)}${query}`);
}

/** 所有有 B站 记录的任务的徽标。任务中心一次拿全，避免「N 个任务 = N 个请求」。 */
export function getBiliUploadsSummary(): Promise<BiliJobUploadsSummary> {
  return request<BiliJobUploadsSummary>("/api/bilibili/uploads/summary");
}

/**
 * 账号下的已有合集，供投稿界面下拉选择。
 *
 * **未登录也返回 200**（列表为空 + `error` 说明原因），所以这里不做异常处理：
 * 界面要显示那句原因，而不是把它变成一句「加载失败」。
 */
export function listBiliSeasons(): Promise<BiliSeasonList> {
  return request<BiliSeasonList>("/api/bilibili/seasons", undefined, REQUEST_TIMEOUT_MS, "读取合集列表");
}

export function getBiliJobUploads(jobId: string): Promise<BiliJobUploads> {
  return request<BiliJobUploads>(`/api/bilibili/uploads?job_id=${encodeURIComponent(jobId)}`);
}

/**
 * 归集方式那一组字段。**两条提交路径共用**（单视频 / 合集）。
 *
 * `season_title` 只在 `new` 时才有意义，但照传无害：服务端在 `existing` 时用的是平台上
 * 那个合集自己的名字。`season_id` 则相反 —— `existing` 之外**一个字符都不能带**：
 * 带上一个残留编号会让服务端按「有 season_id 就不建合集」处理，
 * 「新建合集」就悄悄变成了往老合集里塞视频（`bili_manager._upsert_season` 的优先级）。
 *
 * 这段判断原本在两条路径里各写了一遍。它错起来的代价是**静默的**（归集去了错的合集），
 * 所以只留一份 —— 复制时最容易漏掉的正是那行 `...(groupMode === "existing" ? …)`。
 */
function biliGroupFields(
  groupMode: BiliGroupMode,
  seasonId: number | null,
  seasonTitle: string
): Record<string, unknown> {
  return {
    group_mode: groupMode,
    season_title: seasonTitle,
    ...(groupMode === GROUP_MODE_EXISTING ? { season_id: seasonId } : {})
  };
}

export function createBiliUpload(
  jobId: string,
  itemId: string | null,
  metadata: BiliMetadata,
  makeCover: boolean,
  comment: string,
  groupMode: BiliGroupMode,
  seasonId: number | null,
  seasonTitle: string
): Promise<BiliJobUploads> {
  return request<BiliJobUploads>("/api/bilibili/uploads", {
    method: "POST",
    body: JSON.stringify({
      job_id: jobId,
      item_id: itemId,
      metadata,
      make_cover: makeCover,
      comment,
      ...biliGroupFields(groupMode, seasonId, seasonTitle)
    })
  });
}

export function createBiliCollection(
  jobId: string,
  template: BiliMetadata,
  seasonTitle: string,
  seasonDesc: string,
  makeCover: boolean,
  comment: string,
  groupMode: BiliGroupMode,
  seasonId: number | null
): Promise<BiliJobUploads> {
  return request<BiliJobUploads>("/api/bilibili/collections", {
    method: "POST",
    body: JSON.stringify({
      job_id: jobId,
      template,
      season_desc: seasonDesc,
      make_cover: makeCover,
      comment,
      ...biliGroupFields(groupMode, seasonId, seasonTitle)
    })
  });
}

/** 重试一条稿件。已成功的条目不会被重投（服务端保证），返回 `action` 说明实际做了什么。 */
export function retryBiliUpload(uploadId: string): Promise<BiliRetry> {
  return request<BiliRetry>(
    `/api/bilibili/uploads/${encodeURIComponent(uploadId)}/retry`,
    { method: "POST" },
    REQUEST_TIMEOUT_MS,
    "重试投稿"
  );
}

/** 重试合集归集。已有合集时只补加视频，不会重复建合集。 */
export function retryBiliSeason(seasonRecordId: string): Promise<BiliSeasonRetry> {
  return request<BiliSeasonRetry>(
    `/api/bilibili/collections/${encodeURIComponent(seasonRecordId)}/group`,
    { method: "POST" },
    REQUEST_TIMEOUT_MS,
    "重试归集"
  );
}

function restartRequest(resolution?: string): RequestInit {
  return resolution
    ? { method: "POST", body: JSON.stringify({ resolution }) }
    : { method: "POST" };
}
