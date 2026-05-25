import type {
  AnalyzeResponse,
  ApiErrorDetail,
  CookieStatus,
  DeleteJobItemsResponse,
  DownloadOptions,
  Job,
  JobBatchAction,
  JobBatchActionResponse,
  Settings
} from "./types";

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

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: init?.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...init
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
  return response.json() as Promise<T>;
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
  return request<AnalyzeResponse>("/api/analyze", {
    method: "POST",
    body: JSON.stringify({ url, cookies_enabled: true })
  });
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

function restartRequest(resolution?: string): RequestInit {
  return resolution
    ? { method: "POST", body: JSON.stringify({ resolution }) }
    : { method: "POST" };
}
