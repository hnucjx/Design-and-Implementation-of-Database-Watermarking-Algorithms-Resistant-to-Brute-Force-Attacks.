import type { Job } from "../types";

export const analyzePayload = {
  url: "https://youtube.com/playlist?list=abc",
  title: "Batch",
  is_playlist: true,
  duration: null,
  thumbnail: null,
  entries: [
    { index: 1, id: "one", title: "One", url: "https://youtu.be/one", duration: 60, thumbnail: null },
    { index: 2, id: "two", title: "Two", url: "https://youtu.be/two", duration: 90, thumbnail: null }
  ],
  formats: [
    { format_id: "22", label: "720p mp4", height: 720, ext: "mp4", filesize: 10_485_760, fps: null },
    { format_id: "137", label: "1080p mp4", height: 1080, ext: "mp4", filesize: null, fps: 30 }
  ],
  subtitles: [{ language: "en", name: null, formats: ["vtt"] }],
  automatic_subtitles: [{ language: "zh-Hans", name: null, formats: ["vtt"] }],
  ffmpeg: { ffmpeg: true, ffprobe: true }
};

let currentAnalyzePayload = analyzePayload;

export const jobPayload: Job = {
  id: "job-running",
  url: "https://youtu.be/running",
  title: "Running video",
  status: "running",
  progress: 34,
  created_at: "2026-05-15T10:00:00Z",
  updated_at: "2026-05-15T10:00:42Z",
  started_at: "2026-05-15T10:00:00Z",
  finished_at: null,
  elapsed_seconds: 42,
  actual_resolution: "1920x1080",
  actual_format: "mp4 · avc1 + mp4a",
  resolution_fallback: null,
  speed: 2048,
  eta: 10,
  total_items: 1,
  completed_items: 0,
  failed_items: 0,
  current_item_title: "Running video",
  error: null,
  download_dir: null,
  items: [
    {
      id: "item-running",
      job_id: "job-running",
      source_url: "https://youtu.be/running",
      title: "Running video",
      index: 1,
      status: "running",
      progress: 34,
      created_at: "2026-05-15T10:00:00Z",
      updated_at: "2026-05-15T10:00:42Z",
      started_at: "2026-05-15T10:00:00Z",
      finished_at: null,
      elapsed_seconds: 42,
      actual_width: 1920,
      actual_height: 1080,
      actual_format: "mp4 · avc1 + mp4a",
      requested_resolution: null,
      fallback_resolution: null,
      fallback_reason: null,
      resolution_fallback: null,
      downloaded_bytes: 34,
      total_bytes: 100,
      speed: 2048,
      eta: 10,
      output_path: null,
      error: null
    }
  ]
};

export const pausedJobPayload: Job = {
  ...jobPayload,
  id: "job-paused",
  title: "Paused video",
  status: "paused",
  progress: 34,
  finished_at: "2026-05-15T10:05:00Z",
  actual_resolution: "1280x720",
  actual_format: "mp4 · avc1 + mp4a",
  items: [{ ...jobPayload.items[0], id: "item-paused", job_id: "job-paused", title: "Paused video", status: "paused" }]
};

export const playlistJobPayload: Job = {
  ...jobPayload,
  id: "job-playlist",
  url: "https://youtube.com/playlist?list=abc",
  title: "Playlist batch",
  actual_resolution: "混合分辨率",
  actual_format: "混合格式",
  total_items: 2,
  completed_items: 0,
  failed_items: 0,
  items: [
    {
      ...jobPayload.items[0],
      id: "item-playlist-1",
      job_id: "job-playlist",
      source_url: "https://youtu.be/one",
      title: "Part one",
      index: 1,
      progress: 50,
      downloaded_bytes: 5_242_880,
      total_bytes: 10_485_760,
      speed: 2048,
      eta: 20,
      elapsed_seconds: 42,
      actual_width: 1920,
      actual_height: 1080,
      actual_format: "mp4 · avc1 + mp4a"
    },
    {
      ...jobPayload.items[0],
      id: "item-playlist-2",
      job_id: "job-playlist",
      source_url: "https://youtu.be/two",
      title: "Part two",
      index: 2,
      progress: 0,
      status: "queued",
      downloaded_bytes: null,
      total_bytes: null,
      speed: null,
      eta: null,
      elapsed_seconds: 0,
      actual_width: null,
      actual_height: null,
      actual_format: null
    }
  ]
};

export const resolutionFallback = {
  requested_resolution: "1080p",
  fallback_resolution: "720p",
  reason: "media_stream_blocked",
  restart_resolution: "720p",
  message: "当前 1080p 媒体流下载被 YouTube 拒绝或连接重置，可尝试以 720p 重启。"
};

export const automaticResolutionFallback = {
  requested_resolution: "1080p",
  fallback_resolution: "720p",
  reason: "requested_resolution_missing",
  restart_resolution: null,
  message: "视频本来没有 1080p，已自动降级到 720p。"
};

export const unselectableResolutionFallback = {
  requested_resolution: "1080p",
  fallback_resolution: "720p",
  reason: "requested_resolution_unselectable",
  restart_resolution: "1080p",
  message: "检测到 1080p 清晰度，但该清晰度当前没有可下载的视频/音频组合，已自动降级到 720p。"
};

export const singleFallbackJobPayload: Job = {
  ...jobPayload,
  id: "job-format-failed",
  title: "Unsupported resolution",
  status: "failed",
  progress: 0,
  error: "1 item(s) failed.",
  resolution_fallback: resolutionFallback,
  items: [
    {
      ...jobPayload.items[0],
      id: "item-format-failed",
      job_id: "job-format-failed",
      title: "Unsupported resolution",
      status: "failed",
      progress: 0,
      error: resolutionFallback.message,
      requested_resolution: "1080p",
      fallback_resolution: "720p",
      fallback_reason: "media_stream_blocked",
      resolution_fallback: resolutionFallback
    }
  ]
};

export const playlistFallbackJobPayload: Job = {
  ...playlistJobPayload,
  status: "failed",
  failed_items: 1,
  items: [
    playlistJobPayload.items[0],
    {
      ...playlistJobPayload.items[1],
      status: "failed",
      error: resolutionFallback.message,
      requested_resolution: "1080p",
      fallback_resolution: "720p",
      fallback_reason: "media_stream_blocked",
      resolution_fallback: resolutionFallback
    }
  ]
};

export const settingsPayload = {
  download_dir: "downloads",
  default_concurrency: 2,
  default_subtitle_languages: ["en"],
  default_resolution: "1440p",
  cookies_enabled: false,
  ffmpeg: { ffmpeg: true, ffprobe: true }
};

export const lockedEdgeCookieDetail = {
  code: "browser_locked",
  browser: "edge",
  message: "Edge 正在运行，cookies 数据库被锁定。请关闭 Edge 后重试，或确认由应用关闭 Edge 并重新导入。",
  raw_detail: "Could not copy Chrome cookie database."
};

