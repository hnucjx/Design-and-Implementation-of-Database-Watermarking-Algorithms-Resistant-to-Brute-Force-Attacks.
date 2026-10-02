import { XCircle } from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
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
  updateSettings,
  uploadCookies
} from "./api";
import { AnalysisPanel } from "./components/AnalysisPanel";
import { DownloadOptionsPanel } from "./components/DownloadOptionsPanel";
import { JobQueue } from "./components/JobQueue";
import { SettingsPanel } from "./components/SettingsPanel";
import { StatusPill } from "./components/StatusPill";
import { UrlAnalyzer } from "./components/UrlAnalyzer";
import { browserCookieLockFromError, type BrowserCookieLock } from "./cookieLock";
import { chooseAvailableResolution } from "./quality";
import { effectiveSubtitleSourceForAnalysis } from "./subtitles";
import type { AnalyzeResponse, DownloadOptions, Job, JobBatchAction, Settings } from "./types";

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

/**
 * 应用容器：**只做状态编排、请求发起与布局**。
 *
 * 展示职责一律在 `components/` 下（解析面板、解析结果、下载选项、任务中心、设置、状态点），
 * 纯计算在 `quality.ts` / `subtitles.ts` / `formatting.ts` / `cookieLock.ts`。
 * 判断一段新代码该放哪：**它需要 `useState`/请求吗？** 需要就留在这里（或抽成 hook），
 * 只把 props 变成界面就去 `components/`。
 *
 * 三条容易被改坏的约定：
 * 1. **设置是权威来源**：`applySettings` 回灌后，`options.speed_limit_kbps` / `retries` 跟随设置走，
 *    而不是反过来 —— 这两个字段是运行时设置，不是本次请求的临时值。
 * 2. **每次解析都会重置选择**：`applyAnalysisResult` 用设置里的默认字幕语言覆盖当前选择
 *    （出厂 `["en"]`）。副作用是可能留下一个该视频没有、下拉里也摘不掉的语言，见 ai/ui/README.md。
 * 3. **列表数据以服务端为准**：SSE 每来一条事件就重新拉一次 `/api/jobs`，本地只做乐观增删。
 */
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
  const [runtimeSaveMessage, setRuntimeSaveMessage] = useState("");
  const runtimeSaveRequestSeq = useRef(0);
  const runtimeSaveClearTimer = useRef<number | null>(null);

  useEffect(() => {
    void getSettings().then(applySettings).catch((err) => setError(err.message));
    void listJobs().then(setJobs).catch(() => setJobs([]));
  }, []);

  useEffect(() => {
    const source = new EventSource("/api/events");
    source.onmessage = () => {
      void listJobs().then(setJobs).catch(() => undefined);
    };
    return () => source.close();
  }, []);

  useEffect(
    () => () => {
      if (runtimeSaveClearTimer.current !== null) {
        window.clearTimeout(runtimeSaveClearTimer.current);
      }
    },
    []
  );

  useEffect(() => {
    setSelectedJobIds((current) => new Set(Array.from(current).filter((jobId) => jobs.some((job) => job.id === jobId))));
  }, [jobs]);

  const subtitleLanguages = useMemo(() => {
    const human = analysis?.subtitles.map((item) => item.language) ?? [];
    const auto = analysis?.automatic_subtitles.map((item) => item.language) ?? [];
    return Array.from(new Set([...human, ...auto])).sort();
  }, [analysis]);

  const duplicateWarning = url.trim().length > 0 && history.includes(url.trim());

  function applySettings(updated: Settings) {
    setSettings(updated);
    setOptions((current) => ({
      ...current,
      speed_limit_kbps: updated.default_speed_limit_kbps,
      retries: updated.default_retries
    }));
  }

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

  async function updateRuntimeDownloadOption<K extends "speed_limit_kbps" | "retries">(key: K, value: DownloadOptions[K]) {
    const requestSeq = runtimeSaveRequestSeq.current + 1;
    runtimeSaveRequestSeq.current = requestSeq;
    if (runtimeSaveClearTimer.current !== null) {
      window.clearTimeout(runtimeSaveClearTimer.current);
      runtimeSaveClearTimer.current = null;
    }
    updateOption(key, value);
    setRuntimeSaveMessage("保存中...");
    try {
      const updated = await updateSettings(
        key === "speed_limit_kbps"
          ? { default_speed_limit_kbps: value as number | null }
          : { default_retries: value as number }
      );
      if (runtimeSaveRequestSeq.current === requestSeq) {
        applySettings(updated);
        setRuntimeSaveMessage("已保存");
      }
    } catch (err) {
      if (runtimeSaveRequestSeq.current === requestSeq) {
        setRuntimeSaveMessage("保存失败");
      }
      throw err;
    } finally {
      if (runtimeSaveRequestSeq.current === requestSeq) {
        runtimeSaveClearTimer.current = window.setTimeout(() => {
          setRuntimeSaveMessage("");
          runtimeSaveClearTimer.current = null;
        }, 1800);
      }
    }
  }

  function updateQuality(resolution: string) {
    setOptions((current) => ({ ...current, resolution, format_id: null }));
  }

  async function handleCookieUpload(file: File | null) {
    if (!file) return;
    await uploadCookies(file);
    applySettings(await getSettings());
    setBrowserCookieLock(null);
  }

  async function handleCookieDelete() {
    await deleteCookies();
    applySettings(await getSettings());
    setBrowserCookieLock(null);
  }

  async function handleBrowserCookieImport(browser: string, closeBrowserIfLocked = false) {
    await importBrowserCookies(browser, closeBrowserIfLocked);
    applySettings(await getSettings());
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
              runtimeSaveMessage={runtimeSaveMessage}
              onRuntimeOptionChange={(key, value) =>
                void updateRuntimeDownloadOption(key, value).catch((err) => handleAppError(err, "保存下载设置失败"))
              }
            />
            {settings && <SettingsPanel settings={settings} onSettingsChange={applySettings} />}
          </aside>
        </section>
      </section>
    </main>
  );
}
