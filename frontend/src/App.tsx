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
  Pause,
  RotateCcw,
  Search,
  Settings as SettingsIcon,
  Trash2,
  XCircle
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  analyzeUrl,
  batchJobAction,
  createJob,
  deleteJob,
  deleteCookies,
  getSettings,
  importBrowserCookies,
  listJobs,
  pauseJob,
  restartJob,
  restartJobItem,
  selectDownloadDirectory,
  updateSettings,
  uploadCookies
} from "./api";
import type {
  AnalyzeResponse,
  DownloadMode,
  DownloadOptions,
  FormatOption,
  Job,
  JobBatchAction,
  Settings,
  SubtitleFormat,
  SubtitleSource
} from "./types";

const RESOLUTIONS = ["best", "2160p", "1440p", "1080p", "720p", "480p"];
const BROWSER_COOKIE_OPTIONS = [
  { value: "auto", label: "自动检测" },
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
  resolution: "1080p",
  format_id: null,
  subtitle_languages: [],
  subtitle_source: "human",
  subtitle_format: "best",
  playlist_items: null,
  write_metadata: false,
  write_thumbnail: false,
  skip_existing: true,
  speed_limit_kbps: 2048,
  retries: 3,
  notify_on_complete: false
};

export default function App() {
  const [url, setUrl] = useState("");
  const [analysis, setAnalysis] = useState<AnalyzeResponse | null>(null);
  const [options, setOptions] = useState<DownloadOptions>(INITIAL_OPTIONS);
  const [selectedItems, setSelectedItems] = useState<Set<number>>(new Set());
  const [settings, setSettings] = useState<Settings | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJobIds, setSelectedJobIds] = useState<Set<string>>(new Set());
  const [deleteFilesWithJobs, setDeleteFilesWithJobs] = useState(false);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
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

  async function handleAnalyze(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setIsAnalyzing(true);
    try {
      const result = await analyzeUrl(url.trim());
      setAnalysis(result);
      setSelectedItems(new Set(result.entries.map((entry) => entry.index)));
      setOptions((current) => ({
        ...current,
        resolution: chooseAvailableResolution(result, settings?.default_resolution ?? current.resolution),
        format_id: null,
        subtitle_languages: settings?.default_subtitle_languages ?? current.subtitle_languages
      }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "解析失败");
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
        playlist_items: playlistItems
      });
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
      const nextHistory = Array.from(new Set([analysis.url, ...history])).slice(0, 20);
      setHistory(nextHistory);
      localStorage.setItem("download-history", JSON.stringify(nextHistory));
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建任务失败");
    } finally {
      setIsSubmitting(false);
    }
  }

  function updateOption<K extends keyof DownloadOptions>(key: K, value: DownloadOptions[K]) {
    setOptions((current) => ({ ...current, [key]: value }));
  }

  function updateQuality(resolution: string, formatId: string | null) {
    setOptions((current) => ({ ...current, resolution, format_id: formatId }));
  }

  async function handleCookieUpload(file: File | null) {
    if (!file) return;
    await uploadCookies(file);
    setSettings(await getSettings());
  }

  async function handleCookieDelete() {
    await deleteCookies();
    setSettings(await getSettings());
  }

  async function handleBrowserCookieImport(browser: string) {
    await importBrowserCookies(browser);
    setSettings(await getSettings());
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

  async function handleRestartJob(jobId: string) {
    updateJobInList(await restartJob(jobId));
  }

  async function handleRestartJobItem(jobId: string, itemId: string) {
    updateJobInList(await restartJobItem(jobId, itemId));
  }

  async function handleDeleteJob(jobId: string) {
    await deleteJob(jobId, deleteFilesWithJobs);
    setJobs((current) => current.filter((job) => job.id !== jobId));
  }

  async function handleBatchAction(action: JobBatchAction) {
    const jobIds = Array.from(selectedJobIds);
    if (!jobIds.length) return;
    const response = await batchJobAction(action, jobIds, action === "delete" ? deleteFilesWithJobs : false);
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
              isAnalyzing={isAnalyzing}
              onAnalyze={handleAnalyze}
              onBrowserCookieImport={(browser) => handleBrowserCookieImport(browser).catch((err) => setError(err.message))}
              onCookieDelete={() => void handleCookieDelete().catch((err) => setError(err.message))}
              onCookieUpload={(file) => void handleCookieUpload(file).catch((err) => setError(err.message))}
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
              deleteFilesWithJobs={deleteFilesWithJobs}
              selectedJobIds={selectedJobIds}
              onBatchAction={(action) => void handleBatchAction(action).catch((err) => setError(err.message))}
              onDeleteFilesWithJobsChange={setDeleteFilesWithJobs}
              onDelete={(jobId) => void handleDeleteJob(jobId).catch((err) => setError(err.message))}
              onPause={(jobId) => void handlePauseJob(jobId).catch((err) => setError(err.message))}
              onRestart={(jobId) => void handleRestartJob(jobId).catch((err) => setError(err.message))}
              onRestartItem={(jobId, itemId) => void handleRestartJobItem(jobId, itemId).catch((err) => setError(err.message))}
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

function UrlAnalyzer({
  settings,
  url,
  duplicateWarning,
  isAnalyzing,
  onAnalyze,
  onBrowserCookieImport,
  onCookieDelete,
  onCookieUpload,
  onUrlChange
}: {
  settings: Settings | null;
  url: string;
  duplicateWarning: boolean;
  isAnalyzing: boolean;
  onAnalyze: (event: FormEvent) => void;
  onBrowserCookieImport: (browser: string) => Promise<void>;
  onCookieDelete: () => void;
  onCookieUpload: (file: File | null) => void;
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
            选择 cookies.txt
            <input
              aria-label="选择 cookies.txt"
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
            <span>浏览器 cookies 来源</span>
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
  onQualityChange: (resolution: string, formatId: string | null) => void;
}) {
  const selectedFormat = analysis?.formats.find((format) => format.format_id === options.format_id) ?? null;
  const resolutionOptions = buildResolutionOptions(analysis);
  const qualityValue = selectedFormat ? `format:${selectedFormat.format_id}` : `resolution:${options.resolution}`;
  const showMergeWarning =
    Boolean(analysis) &&
    !ffmpegAvailable &&
    options.mode !== "subtitles_only" &&
    (Boolean(options.format_id) || Boolean(resolutionHeight(options.resolution)));

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
        <span>清晰度 / 格式</span>
        <select
          aria-label="清晰度 / 格式"
          value={qualityValue}
          onChange={(event) => {
            const value = event.target.value;
            if (value.startsWith("format:")) {
              onQualityChange(options.resolution, value.replace("format:", ""));
              return;
            }
            onQualityChange(value.replace("resolution:", ""), null);
          }}
          disabled={options.mode === "subtitles_only"}
        >
          <optgroup label="清晰度策略">
            {resolutionOptions.map((resolution) => (
              <option key={resolution} value={`resolution:${resolution}`}>
                {formatResolutionLabel(resolution)}
              </option>
            ))}
          </optgroup>
          {analysis && (
            <optgroup label="具体格式">
              {analysis.formats.map((format) => (
                <option key={format.format_id} value={`format:${format.format_id}`}>
                  {formatFormatOption(format)}
                </option>
              ))}
            </optgroup>
          )}
        </select>
        {selectedFormat && <p className="hint">已选格式：{formatFormatOption(selectedFormat)}</p>}
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

      <div className="toggle-list">
        <Toggle icon={<FileText size={16} />} label="保存 metadata" checked={options.write_metadata} onChange={(value) => onOptionChange("write_metadata", value)} />
        <Toggle icon={<Captions size={16} />} label="保存缩略图" checked={options.write_thumbnail} onChange={(value) => onOptionChange("write_thumbnail", value)} />
        <Toggle icon={<RotateCcw size={16} />} label="跳过已下载" checked={options.skip_existing} onChange={(value) => onOptionChange("skip_existing", value)} />
        <Toggle icon={<Bell size={16} />} label="完成后通知" checked={options.notify_on_complete} onChange={(value) => onOptionChange("notify_on_complete", value)} />
      </div>

      <div className="two-col">
        <div className="field">
          <label className="field-label" htmlFor="speed-limit-kbps">
            限速 KB/s
          </label>
          <input
            id="speed-limit-kbps"
            type="number"
            min={1}
            value={options.speed_limit_kbps ?? ""}
            onChange={(event) => onOptionChange("speed_limit_kbps", event.target.value ? Number(event.target.value) : null)}
          />
          <p className="hint">清空表示不限速</p>
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
        <span>并发 (默认跟随 CPU Core 数量，可按需调整。)</span>
        <input
          type="number"
          min={1}
          value={draft.default_concurrency ?? 1}
          onChange={(event) => setDraft({ ...draft, default_concurrency: Number(event.target.value) })}
          onBlur={(event) => void saveConcurrency(Number(event.currentTarget.value))}
        />
      </label>
      {saveMessage && <span className="settings-save-status">{saveMessage}</span>}
    </section>
  );
}

function JobQueue({
  jobs,
  deleteFilesWithJobs,
  selectedJobIds,
  onBatchAction,
  onDeleteFilesWithJobsChange,
  onDelete,
  onPause,
  onRestart,
  onRestartItem,
  onToggleJobSelection
}: {
  jobs: Job[];
  deleteFilesWithJobs: boolean;
  selectedJobIds: Set<string>;
  onBatchAction: (action: JobBatchAction) => void;
  onDeleteFilesWithJobsChange: (checked: boolean) => void;
  onDelete: (jobId: string) => void;
  onPause: (jobId: string) => void;
  onRestart: (jobId: string) => void;
  onRestartItem: (jobId: string, itemId: string) => void;
  onToggleJobSelection: (jobId: string) => void;
}) {
  const selectedCount = selectedJobIds.size;
  const [expandedJobIds, setExpandedJobIds] = useState<Record<string, boolean>>({});

  useEffect(() => {
    setExpandedJobIds((current) => {
      const availableJobIds = new Set(jobs.map((job) => job.id));
      const next = Object.fromEntries(Object.entries(current).filter(([jobId]) => availableJobIds.has(jobId)));
      return Object.keys(next).length === Object.keys(current).length ? current : next;
    });
  }, [jobs]);

  function toggleExpanded(jobId: string, isExpanded: boolean) {
    setExpandedJobIds((current) => ({ ...current, [jobId]: !isExpanded }));
  }

  return (
    <section className="panel">
      <div className="panel-title job-title-row">
        <div className="job-title-main">
          <Gauge size={20} />
          <h2>任务中心</h2>
        </div>
        <span className="job-count-badge">{jobs.length ? `${jobs.length} 个任务` : "暂无任务"}</span>
      </div>
      <label className="delete-files-toggle">
        <input
          type="checkbox"
          checked={deleteFilesWithJobs}
          onChange={(event) => onDeleteFilesWithJobsChange(event.target.checked)}
        />
        <span>删除任务时同时删除已下载视频</span>
      </label>
      {selectedCount > 0 && (
        <div className="batch-toolbar" aria-label="批量任务操作">
          <span>{selectedCount} 个已选择</span>
          <button type="button" className="ghost-button" onClick={() => onBatchAction("pause")}>
            <Pause size={16} />
            批量暂停
          </button>
          <button type="button" className="ghost-button" onClick={() => onBatchAction("restart")}>
            <RotateCcw size={16} />
            批量重启
          </button>
          <button type="button" className="ghost-button danger" onClick={() => onBatchAction("delete")}>
            <Trash2 size={16} />
            批量删除
          </button>
        </div>
      )}
      <div className="job-list">
        {jobs.map((job) => {
          const title = job.title || "未命名任务";
          const isPlaylist = job.total_items > 1;
          const defaultExpanded = isPlaylist && ["running", "failed"].includes(job.status);
          const isExpanded = isPlaylist ? expandedJobIds[job.id] ?? defaultExpanded : true;
          return (
          <article key={job.id} className="job-card">
            <div className="job-row">
              <input
                aria-label={`选择任务 ${title}`}
                className="job-select"
                type="checkbox"
                checked={selectedJobIds.has(job.id)}
                onChange={() => onToggleJobSelection(job.id)}
              />
              <div className="job-main">
                <h3>{title}</h3>
                <p>{job.status} · {job.completed_items}/{job.total_items} 完成{job.error ? ` · ${job.error}` : ""}</p>
              </div>
              <div className="job-actions">
                {isPlaylist && (
                  <button
                    className={`icon-button expand-button ${isExpanded ? "is-expanded" : "is-collapsed"}`}
                    type="button"
                    title={isExpanded ? "折叠" : "展开"}
                    aria-label={`${isExpanded ? "折叠" : "展开"} ${title}`}
                    aria-expanded={isExpanded}
                    onClick={() => toggleExpanded(job.id, isExpanded)}
                  >
                    <ChevronDown size={18} />
                  </button>
                )}
                {["queued", "running"].includes(job.status) && (
                  <button className="icon-button" type="button" title="暂停" aria-label={`暂停 ${title}`} onClick={() => onPause(job.id)}>
                    <Pause size={18} />
                  </button>
                )}
                {job.status !== "running" && (
                  <button className="icon-button" type="button" title="重启" aria-label={`重启 ${title}`} onClick={() => onRestart(job.id)}>
                    <RotateCcw size={18} />
                  </button>
                )}
                <button className="icon-button danger" type="button" title="删除" aria-label={`删除 ${title}`} onClick={() => onDelete(job.id)}>
                  <Trash2 size={18} />
                </button>
              </div>
            </div>
            <div className="job-metrics">
              <span>{formatPercent(job.progress)}</span>
              <span>开始 {formatDateTime(job.started_at)}</span>
              <span>结束 {formatDateTime(job.finished_at)}</span>
              <span>分辨率 {job.actual_resolution ?? "检测中"}</span>
              <span>已用 {formatClock(job.elapsed_seconds)}</span>
              <span>剩余 {formatClock(job.eta)}</span>
              {job.speed ? <span>{formatBytesPerSecond(job.speed)}</span> : <span>-- KB/s</span>}
            </div>
            <div className="progress-bar">
              <span style={{ width: `${Math.max(0, Math.min(100, job.progress))}%` }} />
            </div>
            {job.items.length > 0 && isPlaylist && isExpanded && (
              <div className="item-list">
                {job.items.map((item) => (
                  <div key={item.id} className="job-item-detail">
                    <div className="item-row">
                      <span>{item.index}. {item.title} · {item.status}</span>
                      <div className="item-actions">
                        {item.error && <span className="item-error">{item.error}</span>}
                        {item.status !== "running" && (
                          <button
                            className="icon-button item-action-button"
                            type="button"
                            title="重启"
                            aria-label={`重启 ${item.title}`}
                            onClick={() => onRestartItem(job.id, item.id)}
                          >
                            <RotateCcw size={16} />
                          </button>
                        )}
                      </div>
                    </div>
                    <div className="item-metrics">
                      <span>{formatPercent(item.progress)}</span>
                      <span>{formatFileSize(item.downloaded_bytes)} / {formatFileSize(item.total_bytes)}</span>
                      <span>已用 {formatClock(item.elapsed_seconds)}</span>
                      <span>剩余 {formatClock(item.eta)}</span>
                      {item.speed ? <span>{formatBytesPerSecond(item.speed)}</span> : <span>-- KB/s</span>}
                    </div>
                    <div className="progress-bar item-progress">
                      <span style={{ width: `${Math.max(0, Math.min(100, item.progress))}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </article>
          );
        })}
      </div>
    </section>
  );
}

function formatDuration(seconds: number | null): string {
  if (!seconds) return "--:--";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60).toString().padStart(2, "0");
  return `${mins}:${secs}`;
}

function formatResolutionLabel(resolution: string): string {
  return resolution === "best" ? "最佳可用" : resolution;
}

function resolutionHeight(resolution: string): number | null {
  if (!resolution.endsWith("p")) return null;
  const value = Number(resolution.slice(0, -1));
  return Number.isFinite(value) ? value : null;
}

function formatHeights(analysis: AnalyzeResponse | null): number[] {
  return Array.from(
    new Set((analysis?.formats ?? []).map((format) => format.height).filter((height): height is number => Boolean(height)))
  ).sort((a, b) => b - a);
}

function buildResolutionOptions(analysis: AnalyzeResponse | null): string[] {
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

function chooseAvailableResolution(analysis: AnalyzeResponse, preferredResolution: string): string {
  if (preferredResolution === "best") return preferredResolution;
  const preferredHeight = resolutionHeight(preferredResolution);
  const heights = formatHeights(analysis);
  if (!preferredHeight || !heights.length || heights.includes(preferredHeight)) {
    return preferredResolution;
  }
  return `${heights[0]}p`;
}

function formatPercent(value: number | null | undefined): string {
  return `${Math.max(0, Math.min(100, value ?? 0)).toFixed(1)}%`;
}

function formatClock(seconds: number | null | undefined): string {
  const safeSeconds = Math.max(0, Math.floor(seconds ?? 0));
  const hours = Math.floor(safeSeconds / 3600);
  const minutes = Math.floor((safeSeconds % 3600) / 60).toString().padStart(2, "0");
  const secs = Math.floor(safeSeconds % 60).toString().padStart(2, "0");
  return hours ? `${hours}:${minutes}:${secs}` : `${minutes}:${secs}`;
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "--";
  return value.replace("T", " ").replace(/\.\d+Z?$/, "").replace(/Z$/, "").slice(0, 19);
}

function formatFileSize(bytes: number | null | undefined): string {
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

function formatFormatOption(format: FormatOption): string {
  const parts = [
    format.format_id,
    format.height ? `${format.height}p` : null,
    format.fps ? `${Number.isInteger(format.fps) ? format.fps.toFixed(0) : format.fps}fps` : null,
    format.ext,
    formatFileSize(format.filesize)
  ];
  return parts.filter(Boolean).join(" · ");
}

function formatSelectedQualitySize(analysis: AnalyzeResponse, options: DownloadOptions): string {
  if (options.format_id) {
    const format = analysis.formats.find((item) => item.format_id === options.format_id);
    if (!format) return `${options.format_id} · 大小未知`;
    return [
      format.format_id,
      format.height ? `${format.height}p` : null,
      formatFileSize(format.filesize)
    ].filter(Boolean).join(" · ");
  }

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

function formatBytesPerSecond(bytes: number): string {
  if (bytes >= 1024 * 1024) {
    return `${(bytes / 1024 / 1024).toFixed(1)} MB/s`;
  }
  return `${(bytes / 1024).toFixed(1)} KB/s`;
}
