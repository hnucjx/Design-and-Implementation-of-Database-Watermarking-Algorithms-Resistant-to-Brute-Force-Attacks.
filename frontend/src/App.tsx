import {
  Bell,
  Captions,
  CheckCircle2,
  Cookie,
  Download,
  FileText,
  Folder,
  Gauge,
  ListVideo,
  Loader2,
  RotateCcw,
  Settings as SettingsIcon,
  XCircle
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  analyzeUrl,
  cancelJob,
  createJob,
  deleteCookies,
  getSettings,
  listJobs,
  updateSettings,
  uploadCookies
} from "./api";
import type {
  AnalyzeResponse,
  DownloadMode,
  DownloadOptions,
  Job,
  Settings,
  SubtitleFormat,
  SubtitleSource
} from "./types";

const RESOLUTIONS = ["best", "2160p", "1440p", "1080p", "720p", "480p"];

const INITIAL_OPTIONS: DownloadOptions = {
  mode: "video_subtitles",
  resolution: "best",
  format_id: null,
  subtitle_languages: [],
  subtitle_source: "human",
  subtitle_format: "best",
  playlist_items: null,
  write_metadata: false,
  write_thumbnail: false,
  skip_existing: true,
  speed_limit_kbps: null,
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
        resolution: settings?.default_resolution ?? current.resolution,
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
            <StatusPill ok={settings?.ffmpeg?.ffprobe} label="ffprobe" />
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
              url={url}
              duplicateWarning={duplicateWarning}
              isAnalyzing={isAnalyzing}
              onAnalyze={handleAnalyze}
              onUrlChange={setUrl}
            />
            {analysis && (
              <AnalysisPanel
                analysis={analysis}
                selectedItems={selectedItems}
                setSelectedItems={setSelectedItems}
              />
            )}
            <JobQueue jobs={jobs} onCancel={(jobId) => void cancelJob(jobId).then((job) => {
              setJobs((current) => current.map((item) => (item.id === job.id ? job : item)));
            })} />
          </div>

          <aside className="side-column">
            <DownloadOptionsPanel
              analysis={analysis}
              options={options}
              subtitleLanguages={subtitleLanguages}
              isSubmitting={isSubmitting}
              onCreateJob={handleCreateJob}
              onOptionChange={updateOption}
            />
            {settings && <SettingsPanel settings={settings} onSettingsChange={setSettings} />}
            {settings && <CookieManager settings={settings} onSettingsChange={setSettings} />}
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
  url,
  duplicateWarning,
  isAnalyzing,
  onAnalyze,
  onUrlChange
}: {
  url: string;
  duplicateWarning: boolean;
  isAnalyzing: boolean;
  onAnalyze: (event: FormEvent) => void;
  onUrlChange: (value: string) => void;
}) {
  return (
    <form className="panel url-panel" onSubmit={onAnalyze}>
      <div className="panel-title">
        <ListVideo size={20} />
        <div>
          <h2>解析链接</h2>
          <p>支持单视频和 playlist</p>
        </div>
      </div>
      <label className="field">
        <span>视频或 playlist 链接</span>
        <textarea value={url} onChange={(event) => onUrlChange(event.target.value)} rows={3} />
      </label>
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
  selectedItems,
  setSelectedItems
}: {
  analysis: AnalyzeResponse;
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
  options,
  subtitleLanguages,
  isSubmitting,
  onCreateJob,
  onOptionChange
}: {
  analysis: AnalyzeResponse | null;
  options: DownloadOptions;
  subtitleLanguages: string[];
  isSubmitting: boolean;
  onCreateJob: () => void;
  onOptionChange: <K extends keyof DownloadOptions>(key: K, value: DownloadOptions[K]) => void;
}) {
  return (
    <section className="panel options-panel">
      <div className="panel-title">
        <Download size={20} />
        <div>
          <h2>下载选项</h2>
          <p>视频、字幕和批量策略</p>
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
        <span>分辨率</span>
        <select
          aria-label="分辨率"
          value={options.resolution}
          onChange={(event) => onOptionChange("resolution", event.target.value)}
          disabled={options.mode === "subtitles_only"}
        >
          {RESOLUTIONS.map((resolution) => (
            <option key={resolution} value={resolution}>
              {resolution === "best" ? "最佳可用" : resolution}
            </option>
          ))}
        </select>
      </label>

      <label className="field">
        <span>具体格式</span>
        <select
          aria-label="具体格式"
          value={options.format_id ?? ""}
          onChange={(event) => onOptionChange("format_id", event.target.value || null)}
          disabled={!analysis || options.mode === "subtitles_only"}
        >
          <option value="">跟随分辨率策略</option>
          {analysis?.formats.map((format) => (
            <option key={format.format_id} value={format.format_id}>
              {format.label}
            </option>
          ))}
        </select>
      </label>

      <div className="field">
        <span>字幕语言</span>
        <div className="language-grid">
          {subtitleLanguages.length ? (
            subtitleLanguages.map((language) => (
              <label key={language} className="check-row">
                <input
                  aria-label={`字幕 ${language}`}
                  type="checkbox"
                  checked={options.subtitle_languages.includes(language)}
                  onChange={() => {
                    const next = new Set(options.subtitle_languages);
                    if (next.has(language)) next.delete(language);
                    else next.add(language);
                    onOptionChange("subtitle_languages", Array.from(next));
                  }}
                />
                {language}
              </label>
            ))
          ) : (
            <span className="hint">解析后显示可用字幕</span>
          )}
        </div>
      </div>

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
        <label className="field">
          <span>限速 KB/s</span>
          <input
            type="number"
            min={1}
            value={options.speed_limit_kbps ?? ""}
            onChange={(event) => onOptionChange("speed_limit_kbps", event.target.value ? Number(event.target.value) : null)}
          />
        </label>
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
  const [saving, setSaving] = useState(false);

  useEffect(() => setDraft(settings), [settings]);

  async function save() {
    setSaving(true);
    try {
      onSettingsChange(await updateSettings(draft));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="panel compact-panel">
      <div className="panel-title">
        <SettingsIcon size={19} />
        <div>
          <h2>设置</h2>
          <p>本机下载默认值</p>
        </div>
      </div>
      <label className="field">
        <span>下载目录</span>
        <input value={draft.download_dir} onChange={(event) => setDraft({ ...draft, download_dir: event.target.value })} />
      </label>
      <div className="two-col">
        <label className="field">
          <span>并发</span>
          <input
            type="number"
            min={1}
            max={8}
            value={draft.default_concurrency}
            onChange={(event) => setDraft({ ...draft, default_concurrency: Number(event.target.value) })}
          />
        </label>
        <label className="field">
          <span>默认分辨率</span>
          <select value={draft.default_resolution} onChange={(event) => setDraft({ ...draft, default_resolution: event.target.value })}>
            {RESOLUTIONS.map((resolution) => <option key={resolution} value={resolution}>{resolution}</option>)}
          </select>
        </label>
      </div>
      <button className="ghost-button full" type="button" onClick={save} disabled={saving}>
        <Folder size={17} />
        保存设置
      </button>
    </section>
  );
}

function CookieManager({ settings, onSettingsChange }: { settings: Settings; onSettingsChange: (settings: Settings) => void }) {
  async function handleUpload(file: File | null) {
    if (!file) return;
    await uploadCookies(file);
    onSettingsChange(await getSettings());
  }

  async function handleDelete() {
    await deleteCookies();
    onSettingsChange(await getSettings());
  }

  return (
    <section className="panel compact-panel">
      <div className="panel-title">
        <Cookie size={19} />
        <div>
          <h2>Cookies</h2>
          <p>{settings.cookies_enabled ? "已启用登录态文件" : "未上传 cookies"}</p>
        </div>
      </div>
      <label className="file-button">
        选择 cookies.txt
        <input type="file" accept=".txt" onChange={(event) => void handleUpload(event.target.files?.[0] ?? null)} />
      </label>
      <button className="ghost-button full" type="button" onClick={handleDelete} disabled={!settings.cookies_enabled}>
        清除 cookies
      </button>
    </section>
  );
}

function JobQueue({ jobs, onCancel }: { jobs: Job[]; onCancel: (jobId: string) => void }) {
  return (
    <section className="panel">
      <div className="panel-title">
        <Gauge size={20} />
        <div>
          <h2>任务中心</h2>
          <p>{jobs.length ? `${jobs.length} 个任务` : "暂无任务"}</p>
        </div>
      </div>
      <div className="job-list">
        {jobs.map((job) => (
          <article key={job.id} className="job-card">
            <div className="job-row">
              <div>
                <h3>{job.title}</h3>
                <p>{job.status} · {job.completed_items}/{job.total_items} 完成{job.error ? ` · ${job.error}` : ""}</p>
              </div>
              {["queued", "running"].includes(job.status) && (
                <button className="icon-button" type="button" aria-label={`取消 ${job.title}`} onClick={() => onCancel(job.id)}>
                  <XCircle size={18} />
                </button>
              )}
            </div>
            <div className="progress-bar">
              <span style={{ width: `${Math.max(0, Math.min(100, job.progress))}%` }} />
            </div>
            {job.items.length > 0 && (
              <div className="item-list">
                {job.items.map((item) => (
                  <span key={item.id}>{item.index}. {item.title} · {item.status}</span>
                ))}
              </div>
            )}
          </article>
        ))}
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
