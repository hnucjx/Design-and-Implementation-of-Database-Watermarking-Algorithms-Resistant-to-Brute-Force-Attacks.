import {
  Bell,
  Captions,
  CheckCircle2,
  ChevronDown,
  Cookie,
  Download,
  FileText,
  Folder,
  Gauge,
  ListVideo,
  Loader2,
  RotateCcw,
  Search,
  Settings as SettingsIcon,
  XCircle
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  analyzeUrl,
  batchJobAction,
  createJob,
  deleteJob,
  deleteJobItems,
  deleteCookies,
  getSettings,
  importBrowserCookies,
  listJobs,
  openJobFolder,
  openJobItemFolder,
  pauseJob,
  playJobItemVideo,
  playJobVideo,
  restartJob,
  restartJobItem,
  selectDownloadDirectory,
  updateSettings,
  uploadCookies
} from "./api";
import { JobQueue } from "./components/JobQueue";
import { formatDuration } from "./formatting";
import {
  buildResolutionOptions,
  chooseAvailableResolution,
  formatResolutionLabel,
  formatSelectedQualitySize,
  resolutionHeight
} from "./quality";
import type {
  AnalyzeResponse,
  DownloadMode,
  DownloadOptions,
  Job,
  JobBatchAction,
  Settings,
  SubtitleFormat,
  SubtitleSource
} from "./types";

const BROWSER_COOKIE_OPTIONS = [
  { value: "auto", label: "自动检测浏览器" },
  { value: "edge", label: "Edge" },
  { value: "chrome", label: "Chrome" },
  { value: "firefox", label: "Firefox" },
  { value: "brave", label: "Brave" },
  { value: "chromium", label: "Chromium" },
  { value: "vivaldi", label: "Vivaldi" },
  { value: "opera", label: "Opera" }
];

const INITIAL_OPTIONS: DownloadOptions = {
  mode: "video_subtitles",
  resolution: "1440p",
  format_id: null,
  subtitle_languages: [],
  subtitle_source: "both",
  subtitle_format: "best",
  playlist_items: null,
  write_metadata: false,
  write_thumbnail: false,
  skip_existing: true,
  speed_limit_kbps: null,
  retries: 10,
  notify_on_complete: false
};

type BrowserCookieLock = {
  browser: string;
  message: string;
  pendingAnalyzeUrl: string | null;
};

export default function App() {
  const [url, setUrl] = useState("");
  const [analysis, setAnalysis] = useState<AnalyzeResponse | null>(null);
  const [options, setOptions] = useState<DownloadOptions>(INITIAL_OPTIONS);
  const [selectedItems, setSelectedItems] = useState<Set<number>>(new Set());
  const [settings, setSettings] = useState<Settings | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJobIds, setSelectedJobIds] = useState<Set<string>>(new Set());
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [browserCookieLock, setBrowserCookieLock] = useState<BrowserCookieLock | null>(null);
  const [history, setHistory] = useState<string[]>(() => JSON.parse(localStorage.getItem("download-history") ?? "[]"));

  useEffect(() => {
    void getSettings().then(setSettings).catch((err) => setError(err.message));
    void listJobs().then(setJobs).catch(() => setJobs([]));
  }, []);

  useEffect(() => {
    const source = new EventSource("/api/events");
    source.onmessage = () => {
      void listJobs().then(setJobs).catch(() => undefined);
    };
    return () => source.close();
  }, []);

  useEffect(() => {
    setSelectedJobIds((current) => new Set(Array.from(current).filter((jobId) => jobs.some((job) => job.id === jobId))));
  }, [jobs]);

  const subtitleLanguages = useMemo(() => {
    const human = analysis?.subtitles.map((item) => item.language) ?? [];
    const auto = analysis?.automatic_subtitles.map((item) => item.language) ?? [];
    return Array.from(new Set([...human, ...auto])).sort();
  }, [analysis]);

  const duplicateWarning = url.trim().length > 0 && history.includes(url.trim());

  function applyAnalysisResult(result: AnalyzeResponse) {
    setAnalysis(result);
    setSelectedItems(new Set(result.entries.map((entry) => entry.index)));
    setOptions((current) => ({
      ...current,
      resolution: chooseAvailableResolution(result, settings?.default_resolution ?? current.resolution),
      format_id: null,
      subtitle_languages: settings?.default_subtitle_languages ?? current.subtitle_languages
    }));
    setBrowserCookieLock(null);
  }

  async function runAnalyze(targetUrl: string) {
    applyAnalysisResult(await analyzeUrl(targetUrl));
  }

  function handleAppError(err: unknown, fallback: string, pendingAnalyzeUrl: string | null = null) {
    const lock = browserCookieLockFromError(err, pendingAnalyzeUrl);
    if (lock) {
      setBrowserCookieLock(lock);
      setError(lock.message);
      return;
    }
    setError(err instanceof Error ? err.message : fallback);
  }

  async function handleAnalyze(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setIsAnalyzing(true);
    const targetUrl = url.trim();
    try {
      await runAnalyze(targetUrl);
    } catch (err) {
      handleAppError(err, "解析失败", targetUrl);
    } finally {
      setIsAnalyzing(false);
    }
  }

  async function handleCreateJob() {
    if (!analysis) return;
    setError(null);
    setIsSubmitting(true);
    const playlistItems = analysis.is_playlist ? Array.from(selectedItems).sort((a, b) => a - b) : null;
    try {
      const job = await createJob(analysis.url, {
        ...options,
        format_id: null,
        subtitle_source: effectiveSubtitleSourceForAnalysis(analysis, options.subtitle_source),
        playlist_items: playlistItems
      });
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
      const nextHistory = Array.from(new Set([analysis.url, ...history])).slice(0, 20);
      setHistory(nextHistory);
      localStorage.setItem("download-history", JSON.stringify(nextHistory));
    } catch (err) {
      handleAppError(err, "创建任务失败");
    } finally {
      setIsSubmitting(false);
    }
  }

  function updateOption<K extends keyof DownloadOptions>(key: K, value: DownloadOptions[K]) {
    setOptions((current) => ({ ...current, [key]: value }));
  }

  function updateQuality(resolution: string) {
    setOptions((current) => ({ ...current, resolution, format_id: null }));
  }

  async function handleCookieUpload(file: File | null) {
    if (!file) return;
    await uploadCookies(file);
    setSettings(await getSettings());
    setBrowserCookieLock(null);
  }

  async function handleCookieDelete() {
    await deleteCookies();
    setSettings(await getSettings());
    setBrowserCookieLock(null);
  }

  async function handleBrowserCookieImport(browser: string, closeBrowserIfLocked = false) {
    await importBrowserCookies(browser, closeBrowserIfLocked);
    setSettings(await getSettings());
    setBrowserCookieLock(null);
  }

  async function handleLockedBrowserCookieImport() {
    if (!browserCookieLock) return;
    const confirmed = window.confirm("将关闭所有 Edge 窗口以释放 cookies 数据库，然后重新导入。是否继续？");
    if (!confirmed) return;
    const pendingAnalyzeUrl = browserCookieLock.pendingAnalyzeUrl;
    setError(null);
    await handleBrowserCookieImport("edge", true);
    if (!pendingAnalyzeUrl) return;
    setIsAnalyzing(true);
    try {
      await runAnalyze(pendingAnalyzeUrl);
    } catch (err) {
      handleAppError(err, "解析失败", pendingAnalyzeUrl);
    } finally {
      setIsAnalyzing(false);
    }
  }

  function updateJobInList(job: Job) {
    setJobs((current) => current.map((item) => (item.id === job.id ? job : item)));
  }

  function toggleJobSelection(jobId: string) {
    setSelectedJobIds((current) => {
      const next = new Set(current);
      if (next.has(jobId)) next.delete(jobId);
      else next.add(jobId);
      return next;
    });
  }

  async function handlePauseJob(jobId: string) {
    updateJobInList(await pauseJob(jobId));
  }

  async function handleRestartJob(jobId: string, resolution?: string) {
    updateJobInList(await restartJob(jobId, resolution));
  }

  async function handleRestartJobItem(jobId: string, itemId: string, resolution?: string) {
    updateJobInList(await restartJobItem(jobId, itemId, resolution));
  }

  async function handleDeleteJob(jobId: string, deleteFiles = false) {
    if (deleteFiles && !window.confirm("将删除该任务记录及其已下载的视频、字幕、metadata、缩略图和 description 等相关文件。是否继续？")) {
      return;
    }
    await deleteJob(jobId, deleteFiles);
    setJobs((current) => current.filter((job) => job.id !== jobId));
  }

  async function handleDeleteJobItems(jobId: string, itemIds: string[], deleteFiles = false) {
    if (!itemIds.length) return;
    if (deleteFiles && !window.confirm("将删除所选视频任务记录及其已下载的视频、字幕、metadata、缩略图和 description 等相关文件。是否继续？")) {
      return;
    }
    const response = await deleteJobItems(jobId, itemIds, deleteFiles);
    if (response.job_deleted) {
      setJobs((current) => current.filter((job) => job.id !== jobId));
      setSelectedJobIds((current) => {
        const next = new Set(current);
        next.delete(jobId);
        return next;
      });
      return;
    }
    if (response.job) {
      updateJobInList(response.job);
    }
  }

  async function handleBatchAction(action: JobBatchAction, deleteFiles = false) {
    const jobIds = Array.from(selectedJobIds);
    if (!jobIds.length) return;
    if (action === "delete" && deleteFiles && !window.confirm("将删除所选任务记录及其已下载的视频、字幕、metadata、缩略图和 description 等相关文件。是否继续？")) {
      return;
    }
    const response = await batchJobAction(action, jobIds, action === "delete" ? deleteFiles : false);
    if (action === "delete") {
      const deleted = new Set(response.affected_job_ids);
      setJobs((current) => current.filter((job) => !deleted.has(job.id)));
      setSelectedJobIds(new Set());
      return;
    }
    setJobs((current) =>
      current.map((job) => response.jobs.find((updatedJob) => updatedJob.id === job.id) ?? job)
    );
  }

  async function handleCopySourceLink(sourceUrl: string) {
    try {
      const clipboard = navigator.clipboard;
      if (!clipboard?.writeText) {
        throw new Error("当前浏览器不支持剪贴板写入。");
      }
      await clipboard.writeText(sourceUrl);
    } catch (err) {
      const message = err instanceof Error ? err.message : "复制链接失败。";
      setError(message);
      throw err;
    }
  }

  function openSourcePage(sourceUrl: string) {
    window.open(sourceUrl, "_blank", "noopener,noreferrer");
  }

  return (
    <main className="app-shell">
      <section className="workspace">
        <header className="topbar">
          <div>
            <h1>YouTube Downloader</h1>
            <p>本机视频、playlist 和字幕下载控制台</p>
          </div>
          <div className="status-strip">
            <StatusPill ok={settings?.ffmpeg?.ffmpeg} label="ffmpeg" />
            <StatusPill ok={settings?.cookies_enabled} label="cookies" />
          </div>
        </header>

        {error && (
          <div className="alert" role="alert">
            <XCircle size={18} />
            {error}
          </div>
        )}

        <section className="grid">
          <div className="primary-column">
            <UrlAnalyzer
              settings={settings}
              url={url}
              duplicateWarning={duplicateWarning}
              browserCookieLock={browserCookieLock}
              isAnalyzing={isAnalyzing}
              onAnalyze={handleAnalyze}
              onBrowserCookieImport={(browser) => handleBrowserCookieImport(browser).catch((err) => handleAppError(err, "导入 cookies 失败"))}
              onCookieDelete={() => void handleCookieDelete().catch((err) => handleAppError(err, "清除 cookies 失败"))}
              onCookieUpload={(file) => void handleCookieUpload(file).catch((err) => handleAppError(err, "上传 cookies 失败"))}
              onLockedBrowserCookieImport={() =>
                void handleLockedBrowserCookieImport().catch((err) =>
                  handleAppError(err, "导入 cookies 失败", browserCookieLock?.pendingAnalyzeUrl ?? null)
                )
              }
              onUrlChange={setUrl}
            />
            {analysis && (
              <AnalysisPanel
                analysis={analysis}
                options={options}
                selectedItems={selectedItems}
                setSelectedItems={setSelectedItems}
              />
            )}
            <JobQueue
              jobs={jobs}
              selectedJobIds={selectedJobIds}
              onBatchAction={(action, deleteFiles) => void handleBatchAction(action, deleteFiles).catch((err) => setError(err.message))}
              onDelete={(jobId, deleteFiles) => void handleDeleteJob(jobId, deleteFiles).catch((err) => setError(err.message))}
              onDeleteItems={(jobId, itemIds, deleteFiles) =>
                void handleDeleteJobItems(jobId, itemIds, deleteFiles).catch((err) => setError(err.message))
              }
              onCopyLink={(sourceUrl) => handleCopySourceLink(sourceUrl)}
              onPause={(jobId) => void handlePauseJob(jobId).catch((err) => setError(err.message))}
              onOpenFolder={(jobId) => openJobFolder(jobId)}
              onOpenItemFolder={(jobId, itemId) => openJobItemFolder(jobId, itemId)}
              onOpenSourcePage={openSourcePage}
              onPlay={(jobId) => playJobVideo(jobId)}
              onPlayItem={(jobId, itemId) => playJobItemVideo(jobId, itemId)}
              onRestart={(jobId, resolution) => void handleRestartJob(jobId, resolution).catch((err) => setError(err.message))}
              onRestartItem={(jobId, itemId, resolution) => void handleRestartJobItem(jobId, itemId, resolution).catch((err) => setError(err.message))}
              onToggleJobSelection={toggleJobSelection}
            />
          </div>

          <aside className="side-column">
            <DownloadOptionsPanel
              analysis={analysis}
              ffmpegAvailable={Boolean(settings?.ffmpeg?.ffmpeg)}
              options={options}
              subtitleLanguages={subtitleLanguages}
              isSubmitting={isSubmitting}
              onCreateJob={handleCreateJob}
              onOptionChange={updateOption}
              onQualityChange={updateQuality}
            />
            {settings && <SettingsPanel settings={settings} onSettingsChange={setSettings} />}
          </aside>
        </section>
      </section>
    </main>
  );
}

function StatusPill({ ok, label }: { ok?: boolean; label: string }) {
  return (
    <span className={`status-pill ${ok ? "is-ok" : "is-warn"}`}>
      {ok ? <CheckCircle2 size={15} /> : <XCircle size={15} />}
      {label}
    </span>
  );
}

function browserCookieLockFromError(err: unknown, pendingAnalyzeUrl: string | null): BrowserCookieLock | null {
  if (!(err instanceof ApiError) || !err.detail || typeof err.detail !== "object") {
    return null;
  }
  if (err.detail.code !== "browser_locked") {
    return null;
  }
  return {
    browser: err.detail.browser ?? "edge",
    message: err.detail.message ?? err.message,
    pendingAnalyzeUrl
  };
}

function UrlAnalyzer({
  settings,
  url,
  duplicateWarning,
  browserCookieLock,
  isAnalyzing,
  onAnalyze,
  onBrowserCookieImport,
  onCookieDelete,
  onCookieUpload,
  onLockedBrowserCookieImport,
  onUrlChange
}: {
  settings: Settings | null;
  url: string;
  duplicateWarning: boolean;
  browserCookieLock: BrowserCookieLock | null;
  isAnalyzing: boolean;
  onAnalyze: (event: FormEvent) => void;
  onBrowserCookieImport: (browser: string) => Promise<void>;
  onCookieDelete: () => void;
  onCookieUpload: (file: File | null) => void;
  onLockedBrowserCookieImport: () => void;
  onUrlChange: (value: string) => void;
}) {
  const [browserCookieSource, setBrowserCookieSource] = useState("auto");
  const [isImportingCookies, setIsImportingCookies] = useState(false);

  async function handleBrowserImport() {
    setIsImportingCookies(true);
    try {
      await onBrowserCookieImport(browserCookieSource);
    } finally {
      setIsImportingCookies(false);
    }
  }

  return (
    <form className="panel url-panel" onSubmit={onAnalyze}>
      <div className="panel-title">
        <ListVideo size={20} />
        <div>
          <h2>解析链接</h2>
        </div>
      </div>
      <label className="field">
        <span>视频或 playlist 链接</span>
        <textarea value={url} onChange={(event) => onUrlChange(event.target.value)} rows={3} />
      </label>
      <div className="cookie-inline">
        <span className="cookie-inline-status">
          <Cookie size={16} />
          {settings?.cookies_enabled ? "已启用 cookies" : "未上传 cookies"}
        </span>
        <div className="cookie-inline-actions">
          <label className="file-button compact-file-button">
            <span>选择</span>
            <span>cookies</span>
            <input
              aria-label="选择 cookies"
              type="file"
              accept=".txt"
              onChange={(event) => onCookieUpload(event.target.files?.[0] ?? null)}
            />
          </label>
          <button className="ghost-button" type="button" onClick={onCookieDelete} disabled={!settings?.cookies_enabled}>
            清除 cookies
          </button>
        </div>
        <div className="browser-cookie-import">
          <label className="compact-select-label">
            <select
              aria-label="浏览器 cookies 来源"
              className="compact-select"
              value={browserCookieSource}
              onChange={(event) => setBrowserCookieSource(event.target.value)}
            >
              {BROWSER_COOKIE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <button
            className="ghost-button"
            type="button"
            onClick={() => void handleBrowserImport()}
            disabled={isImportingCookies}
          >
            {isImportingCookies ? "导入中..." : "从浏览器导入"}
          </button>
        </div>
      </div>
      {browserCookieLock && (
        <div className="cookie-lock-note" role="status">
          <span>{browserCookieLock.message}</span>
          <button className="ghost-button" type="button" onClick={onLockedBrowserCookieImport} disabled={isImportingCookies}>
            关闭 Edge 并导入
          </button>
        </div>
      )}
      {duplicateWarning && <p className="hint">这个链接已经在下载历史中出现过。</p>}
      <button className="primary-button" type="submit" disabled={isAnalyzing || !url.trim()}>
        {isAnalyzing ? <Loader2 className="spin" size={18} /> : <Gauge size={18} />}
        解析链接
      </button>
    </form>
  );
}

function AnalysisPanel({
  analysis,
  options,
  selectedItems,
  setSelectedItems
}: {
  analysis: AnalyzeResponse;
  options: DownloadOptions;
  selectedItems: Set<number>;
  setSelectedItems: (items: Set<number>) => void;
}) {
  const allSelected = analysis.entries.length > 0 && selectedItems.size === analysis.entries.length;

  function toggle(index: number) {
    const next = new Set(selectedItems);
    if (next.has(index)) next.delete(index);
    else next.add(index);
    setSelectedItems(next);
  }

  function toggleAll() {
    setSelectedItems(allSelected ? new Set() : new Set(analysis.entries.map((entry) => entry.index)));
  }

  return (
    <section className="panel analysis-panel">
      <div className="media-heading">
        {analysis.thumbnail ? <img src={analysis.thumbnail} alt="" /> : <div className="thumbnail-placeholder" />}
        <div>
          <h2>{analysis.title}</h2>
          <p>{analysis.is_playlist ? `${analysis.entries.length} 个视频` : "单视频"}</p>
          <p className="quality-size-line">当前选择：{formatSelectedQualitySize(analysis, options)}</p>
        </div>
      </div>

      {analysis.is_playlist ? (
        <div className="table-wrap">
          <div className="table-actions">
            <button type="button" className="ghost-button" onClick={toggleAll}>
              {allSelected ? "清空选择" : "全选"}
            </button>
            <span>{selectedItems.size} 个已选择</span>
          </div>
          <table>
            <thead>
              <tr>
                <th>选择</th>
                <th>#</th>
                <th>标题</th>
                <th>时长</th>
              </tr>
            </thead>
            <tbody>
              {analysis.entries.map((entry) => (
                <tr key={entry.index}>
                  <td>
                    <input
                      aria-label={`选择 ${entry.title}`}
                      type="checkbox"
                      checked={selectedItems.has(entry.index)}
                      onChange={() => toggle(entry.index)}
                    />
                  </td>
                  <td>{entry.index}</td>
                  <td>{entry.title}</td>
                  <td>{formatDuration(entry.duration)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="single-summary">
          <FileText size={18} />
          <span>{formatDuration(analysis.duration)} · {analysis.formats.length} 个格式 · {analysis.subtitles.length} 种字幕</span>
        </div>
      )}
    </section>
  );
}

function DownloadOptionsPanel({
  analysis,
  ffmpegAvailable,
  options,
  subtitleLanguages,
  isSubmitting,
  onCreateJob,
  onOptionChange,
  onQualityChange
}: {
  analysis: AnalyzeResponse | null;
  ffmpegAvailable: boolean;
  options: DownloadOptions;
  subtitleLanguages: string[];
  isSubmitting: boolean;
  onCreateJob: () => void;
  onOptionChange: <K extends keyof DownloadOptions>(key: K, value: DownloadOptions[K]) => void;
  onQualityChange: (resolution: string) => void;
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
            onChange={(event) => onOptionChange("speed_limit_kbps", event.target.value ? Number(event.target.value) : null)}
          />
        </div>
        <label className="field">
          <span>重试次数</span>
          <input
            type="number"
            min={0}
            max={20}
            value={options.retries}
            onChange={(event) => onOptionChange("retries", Number(event.target.value))}
          />
        </label>
      </div>

      <button className="primary-button full" type="button" disabled={!analysis || isSubmitting} onClick={onCreateJob}>
        {isSubmitting ? <Loader2 className="spin" size={18} /> : <Download size={18} />}
        加入下载队列
      </button>
    </section>
  );
}

function formatSubtitleInfo(analysis: AnalyzeResponse | null, options: DownloadOptions): string {
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

function effectiveSubtitleSourceForAnalysis(analysis: AnalyzeResponse, source: SubtitleSource): SubtitleSource {
  const hasHuman = analysis.subtitles.length > 0;
  const hasAuto = analysis.automatic_subtitles.length > 0;
  if (source === "both" && hasHuman !== hasAuto) return hasHuman ? "human" : "auto";
  if (source === "human" && !hasHuman && hasAuto) return "auto";
  if (source === "auto" && !hasAuto && hasHuman) return "human";
  return source;
}

function subtitleFormatLabel(format: SubtitleFormat): string {
  if (format === "srt") return "SRT";
  if (format === "vtt") return "VTT";
  return "最佳";
}

function SearchableLanguageSelect({
  languages,
  selectedLanguages,
  onChange
}: {
  languages: string[];
  selectedLanguages: string[];
  onChange: (languages: string[]) => void;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState("");
  const filteredLanguages = languages.filter((language) => language.toLowerCase().includes(query.trim().toLowerCase()));
  const selectedSet = new Set(selectedLanguages);
  const summary = selectedLanguages.length ? `已选 ${selectedLanguages.length} 项：${selectedLanguages.join(", ")}` : "选择字幕语言";

  function toggleLanguage(language: string) {
    const next = new Set(selectedLanguages);
    if (next.has(language)) next.delete(language);
    else next.add(language);
    onChange(Array.from(next));
  }

  return (
    <div className="field language-select">
      <span>字幕语言</span>
      <button
        type="button"
        className="select-trigger"
        aria-expanded={isOpen}
        onClick={() => setIsOpen((current) => !current)}
      >
        <span>{summary}</span>
        <ChevronDown size={17} />
      </button>
      {isOpen && (
        <div className="language-dropdown">
          <label className="search-field">
            <Search size={16} />
            <input
              aria-label="搜索字幕语言"
              placeholder="搜索字幕语言"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <div className="language-options" role="listbox" aria-label="字幕语言列表" aria-multiselectable="true">
            {filteredLanguages.length ? (
              filteredLanguages.map((language) => (
                <label key={language} className="language-option">
                  <input
                    aria-label={`字幕 ${language}`}
                    type="checkbox"
                    checked={selectedSet.has(language)}
                    onChange={() => toggleLanguage(language)}
                  />
                  <span>{language}</span>
                </label>
              ))
            ) : (
              <p className="empty-option">没有匹配的字幕语言</p>
            )}
          </div>
        </div>
      )}
      {!languages.length && <span className="hint">解析后显示可用字幕</span>}
    </div>
  );
}

function Toggle({
  icon,
  label,
  checked,
  onChange
}: {
  icon: React.ReactNode;
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="toggle-row">
      <span>{icon}{label}</span>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
    </label>
  );
}

function SettingsPanel({ settings, onSettingsChange }: { settings: Settings; onSettingsChange: (settings: Settings) => void }) {
  const [draft, setDraft] = useState(settings);
  const [saveMessage, setSaveMessage] = useState("");

  useEffect(() => setDraft(settings), [settings]);

  async function saveConcurrency(value: number) {
    const nextConcurrency = Math.max(1, Number(value) || settings.default_concurrency);
    if (nextConcurrency === settings.default_concurrency) return;
    setSaveMessage("保存中...");
    try {
      onSettingsChange(
        await updateSettings({
          default_concurrency: nextConcurrency
        })
      );
      setSaveMessage("已保存");
    } catch {
      setSaveMessage("保存失败");
    } finally {
      window.setTimeout(() => setSaveMessage(""), 1800);
    }
  }

  async function chooseDownloadDirectory() {
    setSaveMessage("选择中...");
    try {
      const updated = await selectDownloadDirectory();
      onSettingsChange(updated);
      setDraft(updated);
      setSaveMessage("已更新");
    } catch {
      setSaveMessage("选择失败");
    } finally {
      window.setTimeout(() => setSaveMessage(""), 1800);
    }
  }

  return (
    <section className="panel compact-panel">
      <div className="panel-title">
        <SettingsIcon size={19} />
        <div>
          <h2>设置</h2>
        </div>
      </div>
      <label className="field">
        <span>下载目录</span>
        <div className="directory-picker-row">
          <input value={draft.download_dir ?? ""} readOnly />
          <button className="ghost-button" type="button" onClick={() => void chooseDownloadDirectory()}>
            <Folder size={16} />
            选择文件夹
          </button>
        </div>
      </label>
      <label className="field settings-number-field">
        <span>并发（若追求稳定，可设为 1）</span>
        <input
          type="number"
          min={1}
          value={draft.default_concurrency ?? 5}
          onChange={(event) => setDraft({ ...draft, default_concurrency: Number(event.target.value) })}
          onBlur={(event) => void saveConcurrency(Number(event.currentTarget.value))}
        />
      </label>
      {saveMessage && <span className="settings-save-status">{saveMessage}</span>}
    </section>
  );
}
