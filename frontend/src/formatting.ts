export function formatDuration(seconds: number | null): string {
  if (!seconds) return "--:--";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60).toString().padStart(2, "0");
  return `${mins}:${secs}`;
}

export function formatPercent(value: number | null | undefined): string {
  return `${Math.max(0, Math.min(100, value ?? 0)).toFixed(1)}%`;
}

export function formatClock(seconds: number | null | undefined): string {
  const safeSeconds = Math.max(0, Math.floor(seconds ?? 0));
  const hours = Math.floor(safeSeconds / 3600);
  const minutes = Math.floor((safeSeconds % 3600) / 60).toString().padStart(2, "0");
  const secs = Math.floor(safeSeconds % 60).toString().padStart(2, "0");
  return hours ? `${hours}:${minutes}:${secs}` : `${minutes}:${secs}`;
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "--";
  return value.replace("T", " ").replace(/\.\d+Z?$/, "").replace(/Z$/, "").slice(0, 19);
}

export function formatFileSize(bytes: number | null | undefined): string {
  if (bytes == null) return "大小未知";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

export function formatItemResolution(item: { actual_width: number | null; actual_height: number | null }): string {
  if (item.actual_width == null || item.actual_height == null) {
    return "检测中";
  }
  return `${item.actual_width}x${item.actual_height}`;
}

export function formatBytesPerSecond(bytes: number): string {
  if (bytes >= 1024 * 1024) {
    return `${(bytes / 1024 / 1024).toFixed(1)} MB/s`;
  }
  return `${(bytes / 1024).toFixed(1)} KB/s`;
}
