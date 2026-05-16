import type { AnalyzeResponse, DownloadOptions, Job, JobBatchAction, JobBatchActionResponse, Settings } from "./types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: init?.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...init
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? `Request failed with ${response.status}`);
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

export function restartJob(jobId: string): Promise<Job> {
  return request<Job>(`/api/jobs/${jobId}/restart`, { method: "POST" });
}

export function restartJobItem(jobId: string, itemId: string): Promise<Job> {
  return request<Job>(`/api/jobs/${jobId}/items/${itemId}/restart`, { method: "POST" });
}

export function deleteJob(jobId: string, deleteFiles = false): Promise<void> {
  const query = deleteFiles ? "?delete_files=true" : "";
  return request<void>(`/api/jobs/${jobId}${query}`, { method: "DELETE" });
}

export function batchJobAction(action: JobBatchAction, jobIds: string[], deleteFiles = false): Promise<JobBatchActionResponse> {
  return request<JobBatchActionResponse>("/api/jobs/batch", {
    method: "POST",
    body: JSON.stringify({ action, job_ids: jobIds, delete_files: action === "delete" ? deleteFiles : false })
  });
}

export function uploadCookies(file: File): Promise<{ enabled: boolean; filename: string | null }> {
  const data = new FormData();
  data.append("file", file);
  return request<{ enabled: boolean; filename: string | null }>("/api/cookies", {
    method: "POST",
    body: data
  });
}

export function deleteCookies(): Promise<{ enabled: boolean; filename: string | null }> {
  return request<{ enabled: boolean; filename: string | null }>("/api/cookies", { method: "DELETE" });
}
