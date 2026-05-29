import type { AnalyzeResponse, DownloadOptions, ResolutionFallback } from "./types";
import { formatFileSize } from "./formatting";

const RESOLUTIONS = ["best", "2160p", "1440p", "1080p", "720p", "480p"];

export function resolutionFallbackRestartLabel(fallback: ResolutionFallback, scope: "job" | "item"): string | undefined {
  if (!fallback.restart_resolution) {
    return undefined;
  }
  if (fallback.reason === "requested_resolution_unselectable") {
    return `以 ${fallback.restart_resolution} 重试`;
  }
  return scope === "job" ? `以 ${fallback.restart_resolution} 重启任务` : `以 ${fallback.restart_resolution} 重启`;
}

export function formatResolutionLabel(resolution: string): string {
  return resolution === "best" ? "最佳可用" : resolution;
}

export function resolutionHeight(resolution: string): number | null {
  if (!resolution.endsWith("p")) return null;
  const value = Number(resolution.slice(0, -1));
  return Number.isFinite(value) ? value : null;
}

export function formatHeights(analysis: AnalyzeResponse | null): number[] {
  return Array.from(
    new Set((analysis?.formats ?? []).map((format) => format.height).filter((height): height is number => Boolean(height)))
  ).sort((a, b) => b - a);
}

export function buildResolutionOptions(analysis: AnalyzeResponse | null): string[] {
  const heights = new Set<number>();
  for (const resolution of RESOLUTIONS) {
    const height = resolutionHeight(resolution);
    if (height) heights.add(height);
  }
  for (const height of formatHeights(analysis)) {
    heights.add(height);
  }
  return ["best", ...Array.from(heights).sort((a, b) => b - a).map((height) => `${height}p`)];
}

export function chooseAvailableResolution(_analysis: AnalyzeResponse, preferredResolution: string): string {
  return preferredResolution === "best" ? "best" : preferredResolution;
}

export function formatSelectedQualitySize(analysis: AnalyzeResponse, options: DownloadOptions): string {
  if (options.resolution === "best") {
    return "最佳可用 · 大小未知";
  }

  const height = resolutionHeight(options.resolution);
  const matchingSizes = analysis.formats
    .filter((format) => format.height === height && format.filesize != null)
    .map((format) => format.filesize as number);
  const size = matchingSizes.length ? Math.max(...matchingSizes) : null;
  return `${options.resolution} · ${formatFileSize(size)}`;
}
