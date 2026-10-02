import type { AnalyzeResponse, DownloadOptions, SubtitleFormat, SubtitleSource } from "./types";

/**
 * 字幕相关的**展示文案与选择归一**。
 *
 * 这一组函数只看「解析结果 + 选项」，不发请求、不读 localStorage，因此可以脱离组件单独验证。
 * 语言值的形状见 `@/types` 与后端 `_map_subtitles()`：是**语言代码**（`en` / `zh-Hans`），
 * 不是展示名 —— 写断言时不要用 `English` 这类名字。
 */

/** 下载选项面板里那行「字幕：来源 X · 格式 Y」的说明文案。 */
export function formatSubtitleInfo(analysis: AnalyzeResponse | null, options: DownloadOptions): string {
  if (!analysis) {
    return "字幕：待解析 · 来源 两者都要 · 格式 最佳";
  }
  if (options.mode === "video_only") {
    return "字幕：无字幕（仅视频模式）";
  }

  const hasHuman = analysis.subtitles.length > 0;
  const hasAuto = analysis.automatic_subtitles.length > 0;
  if (!hasHuman && !hasAuto) {
    return "字幕：无字幕";
  }

  const source = subtitleSourceDescription(options.subtitle_source, hasHuman, hasAuto);
  const format = subtitleFormatLabel(options.subtitle_format);
  return `字幕：来源 ${source} · 格式 ${format}`;
}

/**
 * 提交任务前把「两者都要」这类诉求收敛成**该视频真的有的那一类**。
 *
 * 之所以在提交前收敛而不是让后端兜底：后端只能忽略不存在的来源，而界面需要如实告诉用户
 * 「你选的那类没有，实际下的是另一类」。
 */
export function effectiveSubtitleSourceForAnalysis(analysis: AnalyzeResponse, source: SubtitleSource): SubtitleSource {
  const hasHuman = analysis.subtitles.length > 0;
  const hasAuto = analysis.automatic_subtitles.length > 0;
  if (source === "both" && hasHuman !== hasAuto) return hasHuman ? "human" : "auto";
  if (source === "human" && !hasHuman && hasAuto) return "auto";
  if (source === "auto" && !hasAuto && hasHuman) return "human";
  return source;
}

function subtitleSourceDescription(source: SubtitleSource, hasHuman: boolean, hasAuto: boolean): string {
  if (source === "both") {
    if (hasHuman && hasAuto) return "两者都要（人工字幕 + 自动字幕）";
    if (hasHuman) return "人工字幕（自动字幕缺失，已 fallback）";
    return "自动字幕（人工字幕缺失，已 fallback）";
  }
  if (source === "human") {
    return hasHuman ? "人工字幕" : "自动字幕（人工字幕缺失，已 fallback）";
  }
  return hasAuto ? "自动字幕" : "人工字幕（自动字幕缺失，已 fallback）";
}

function subtitleFormatLabel(format: SubtitleFormat): string {
  if (format === "srt") return "SRT";
  if (format === "vtt") return "VTT";
  return "最佳";
}
