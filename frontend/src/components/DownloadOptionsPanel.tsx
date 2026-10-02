import { Bell, Captions, Download, FileText, Loader2, RotateCcw } from "lucide-react";
import { SearchableLanguageSelect } from "./SearchableLanguageSelect";
import { Toggle } from "./Toggle";
import { buildResolutionOptions, formatResolutionLabel, resolutionHeight } from "../quality";
import { formatSubtitleInfo } from "../subtitles";
import type { AnalyzeResponse, DownloadMode, DownloadOptions, SubtitleFormat, SubtitleSource } from "../types";

/**
 * 下载选项面板：模式、清晰度、字幕语言/来源/格式、开关项、限速与重试、提交按钮。
 *
 * 面板**不持有状态**：所有值都来自 `options`，改动一律经 `onOptionChange` 回到 `App`。
 * 这样「提交时用的是面板里看到的那份」是结构上成立的，而不是靠同步 effect 维持。
 *
 * 两个易踩的点：
 * - 清晰度选项由 `buildResolutionOptions(analysis)` 生成（只列可选分辨率，合成值放在 label 里），
 *   改之前先读 [ai/ui/003](../../../ai/ui/003-language-trigger-label-overflows-the-page.md) 与 `quality.ts`。
 * - 限速与重试是**运行时设置**（`onRuntimeOptionChange` 会顺带写回后端设置），不是本次请求的临时值。
 */
export function DownloadOptionsPanel({
  analysis,
  ffmpegAvailable,
  options,
  subtitleLanguages,
  isSubmitting,
  onCreateJob,
  onOptionChange,
  onQualityChange,
  runtimeSaveMessage,
  onRuntimeOptionChange
}: {
  analysis: AnalyzeResponse | null;
  ffmpegAvailable: boolean;
  options: DownloadOptions;
  subtitleLanguages: string[];
  isSubmitting: boolean;
  onCreateJob: () => void;
  onOptionChange: <K extends keyof DownloadOptions>(key: K, value: DownloadOptions[K]) => void;
  onQualityChange: (resolution: string) => void;
  runtimeSaveMessage: string;
  onRuntimeOptionChange: <K extends "speed_limit_kbps" | "retries">(key: K, value: DownloadOptions[K]) => void;
}) {
  const resolutionOptions = buildResolutionOptions(analysis);
  const qualityValue = `resolution:${options.resolution}`;
  const showMergeWarning =
    Boolean(analysis) &&
    !ffmpegAvailable &&
    options.mode !== "subtitles_only" &&
    Boolean(resolutionHeight(options.resolution));
  const subtitleInfo = formatSubtitleInfo(analysis, options);

  return (
    <section className="panel options-panel">
      <div className="panel-title">
        <Download size={20} />
        <div>
          <h2>下载选项</h2>
        </div>
      </div>

      <label className="field">
        <span>下载模式</span>
        <select
          aria-label="下载模式"
          value={options.mode}
          onChange={(event) => onOptionChange("mode", event.target.value as DownloadMode)}
        >
          <option value="video_subtitles">视频 + 字幕</option>
          <option value="video_only">仅视频</option>
          <option value="subtitles_only">仅字幕</option>
        </select>
      </label>

      <label className="field">
        <span>清晰度</span>
        <select
          aria-label="清晰度"
          value={qualityValue}
          onChange={(event) => {
            const value = event.target.value;
            onQualityChange(value.replace("resolution:", ""));
          }}
          disabled={options.mode === "subtitles_only"}
        >
          {resolutionOptions.map((resolution) => (
            <option key={resolution} value={`resolution:${resolution}`}>
              {formatResolutionLabel(resolution)}
            </option>
          ))}
        </select>
        {showMergeWarning && (
          <p className="warning-note">高分辨率 YouTube 视频需要 ffmpeg 合并音视频；当前环境不可用时任务会失败。</p>
        )}
      </label>

      <SearchableLanguageSelect
        languages={subtitleLanguages}
        selectedLanguages={options.subtitle_languages}
        onChange={(languages) => onOptionChange("subtitle_languages", languages)}
      />

      <div className="two-col">
        <label className="field">
          <span>字幕来源</span>
          <select
            value={options.subtitle_source}
            onChange={(event) => onOptionChange("subtitle_source", event.target.value as SubtitleSource)}
          >
            <option value="human">人工字幕</option>
            <option value="auto">自动字幕</option>
            <option value="both">两者都要</option>
          </select>
        </label>
        <label className="field">
          <span>字幕格式</span>
          <select
            value={options.subtitle_format}
            onChange={(event) => onOptionChange("subtitle_format", event.target.value as SubtitleFormat)}
          >
            <option value="best">最佳</option>
            <option value="srt">SRT</option>
            <option value="vtt">VTT</option>
          </select>
        </label>
      </div>

      <div className="subtitle-info" aria-live="polite">
        <Captions size={16} />
        <span>{subtitleInfo}</span>
      </div>

      <div className="toggle-list">
        <Toggle icon={<FileText size={16} />} label="保存 metadata" checked={options.write_metadata} onChange={(value) => onOptionChange("write_metadata", value)} />
        <Toggle icon={<Captions size={16} />} label="保存缩略图" checked={options.write_thumbnail} onChange={(value) => onOptionChange("write_thumbnail", value)} />
        <Toggle icon={<RotateCcw size={16} />} label="跳过已下载" checked={options.skip_existing} onChange={(value) => onOptionChange("skip_existing", value)} />
        <Toggle icon={<Bell size={16} />} label="完成后通知" checked={options.notify_on_complete} onChange={(value) => onOptionChange("notify_on_complete", value)} />
      </div>

      <div className="two-col">
        <div className="field">
          <label className="field-label" htmlFor="speed-limit-kbps">
            限速 KB/s（清空：不限速）
          </label>
          <input
            id="speed-limit-kbps"
            type="number"
            min={1}
            value={options.speed_limit_kbps ?? ""}
            onChange={(event) => onRuntimeOptionChange("speed_limit_kbps", event.target.value ? Number(event.target.value) : null)}
          />
        </div>
        <label className="field">
          <span>重试次数</span>
          <input
            type="number"
            min={0}
            max={20}
            value={options.retries}
            onChange={(event) => onRuntimeOptionChange("retries", Number(event.target.value))}
          />
        </label>
      </div>
      {runtimeSaveMessage && <span className="settings-save-status">{runtimeSaveMessage}</span>}

      <button className="primary-button full" type="button" disabled={!analysis || isSubmitting} onClick={onCreateJob}>
        {isSubmitting ? <Loader2 className="spin" size={18} /> : <Download size={18} />}
        加入下载队列
      </button>
    </section>
  );
}
