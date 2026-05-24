export type DownloadMode = "video_subtitles" | "video_only" | "subtitles_only";
export type SubtitleSource = "human" | "auto" | "both";
export type SubtitleFormat = "best" | "srt" | "vtt";
export type JobBatchAction = "pause" | "restart" | "delete";

export interface FormatOption {
  format_id: string;
  label: string;
  height: number | null;
  ext: string | null;
  fps: number | null;
  filesize: number | null;
}

export interface SubtitleOption {
  language: string;
  name: string | null;
  formats: string[];
}

export interface VideoEntry {
  index: number;
  id: string | null;
  title: string;
  url: string;
  duration: number | null;
  thumbnail: string | null;
}

export interface AnalyzeResponse {
  url: string;
  title: string;
  is_playlist: boolean;
  duration: number | null;
  thumbnail: string | null;
  entries: VideoEntry[];
  formats: FormatOption[];
  subtitles: SubtitleOption[];
  automatic_subtitles: SubtitleOption[];
  ffmpeg: Record<string, boolean>;
}

export interface DownloadOptions {
  mode: DownloadMode;
  resolution: string;
  format_id?: string | null;
  subtitle_languages: string[];
  subtitle_source: SubtitleSource;
  subtitle_format: SubtitleFormat;
  playlist_items: number[] | null;
  write_metadata: boolean;
  write_thumbnail: boolean;
  skip_existing: boolean;
  speed_limit_kbps: number | null;
  retries: number;
  notify_on_complete: boolean;
}

export interface JobItem {
  id: string;
  job_id: string;
  source_url: string;
  title: string;
  index: number;
  status: string;
  progress: number;
  downloaded_bytes: number | null;
  total_bytes: number | null;
  speed: number | null;
  eta: number | null;
  output_path: string | null;
  actual_width: number | null;
  actual_height: number | null;
  actual_format: string | null;
  requested_resolution: string | null;
  fallback_resolution: string | null;
  fallback_reason: string | null;
  resolution_fallback: ResolutionFallback | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  elapsed_seconds: number;
}

export interface Job {
  id: string;
  url: string;
  title: string;
  status: string;
  progress: number;
  speed: number | null;
  eta: number | null;
  total_items: number;
  completed_items: number;
  failed_items: number;
  current_item_title: string | null;
  error: string | null;
  download_dir: string | null;
  actual_resolution: string | null;
  actual_format: string | null;
  resolution_fallback: ResolutionFallback | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  elapsed_seconds: number;
  items: JobItem[];
}

export interface JobBatchActionResponse {
  affected_job_ids: string[];
  jobs: Job[];
}

export interface DeleteJobItemsResponse {
  deleted_item_ids: string[];
  job_deleted: boolean;
  job: Job | null;
}

export interface Settings {
  download_dir: string;
  default_concurrency: number;
  default_subtitle_languages: string[];
  default_resolution: string;
  cookies_enabled: boolean;
  ffmpeg: Record<string, boolean>;
}

export interface CookieStatus {
  enabled: boolean;
  filename: string | null;
  source?: "none" | "file" | "browser";
  browser?: string | null;
  imported_count?: number | null;
}

export interface ApiErrorDetail {
  code?: string;
  browser?: string | null;
  message?: string;
  raw_detail?: string | null;
}

export interface ResolutionFallback {
  requested_resolution: string;
  fallback_resolution: string;
  reason: string | null;
  restart_resolution: string | null;
  message: string;
}
