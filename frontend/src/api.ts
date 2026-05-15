import type { AnalyzeResponse, DownloadOptions, Job, Settings } from "./types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: init?.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...init
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(payload?.detail ?? `Request failed with ${response.status}`);
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
