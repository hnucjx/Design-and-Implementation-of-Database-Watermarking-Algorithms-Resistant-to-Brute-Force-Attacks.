import { useEffect, useState } from "react";
import { ChevronDown, Gauge, Pause, RotateCcw, Trash2 } from "lucide-react";
import type { Job, JobBatchAction, ResolutionFallback } from "../types";
import {
  formatBytesPerSecond,
  formatClock,
  formatDateTime,
  formatFileSize,
  formatItemResolution,
  formatPercent
} from "../formatting";
import { resolutionFallbackRestartLabel } from "../quality";
export function JobQueue({
  jobs,
  deleteFilesWithJobs,
  selectedJobIds,
  onBatchAction,
  onDeleteFilesWithJobsChange,
  onDelete,
  onDeleteItems,
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
  onDelete: (jobId: string, deleteFiles?: boolean) => void;
  onDeleteItems: (jobId: string, itemIds: string[], deleteFiles?: boolean) => void;
  onPause: (jobId: string) => void;
  onRestart: (jobId: string, resolution?: string) => void;
  onRestartItem: (jobId: string, itemId: string, resolution?: string) => void;
  onToggleJobSelection: (jobId: string) => void;
}) {
  const selectedCount = selectedJobIds.size;
  const [expandedJobIds, setExpandedJobIds] = useState<Record<string, boolean>>({});
  const [selectedItemIdsByJob, setSelectedItemIdsByJob] = useState<Record<string, Set<string>>>({});

  useEffect(() => {
    setExpandedJobIds((current) => {
      const availableJobIds = new Set(jobs.map((job) => job.id));
      const next = Object.fromEntries(Object.entries(current).filter(([jobId]) => availableJobIds.has(jobId)));
      return Object.keys(next).length === Object.keys(current).length ? current : next;
    });
    setSelectedItemIdsByJob((current) => {
      const availableItems = new Map(jobs.map((job) => [job.id, new Set(job.items.map((item) => item.id))]));
      const next: Record<string, Set<string>> = {};
      for (const [jobId, itemIds] of Object.entries(current)) {
        const available = availableItems.get(jobId);
        if (!available) continue;
        const kept = new Set(Array.from(itemIds).filter((itemId) => available.has(itemId)));
        if (kept.size) next[jobId] = kept;
      }
      return next;
    });
  }, [jobs]);

  function toggleExpanded(jobId: string, isExpanded: boolean) {
    setExpandedJobIds((current) => ({ ...current, [jobId]: !isExpanded }));
  }

  function toggleItemSelection(jobId: string, itemId: string) {
    setSelectedItemIdsByJob((current) => {
      const next = { ...current };
      const selected = new Set(next[jobId] ?? []);
      if (selected.has(itemId)) selected.delete(itemId);
      else selected.add(itemId);
      if (selected.size) next[jobId] = selected;
      else delete next[jobId];
      return next;
    });
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
          const jobRestartResolution = job.resolution_fallback?.restart_resolution ?? null;
          const jobRestartLabel = job.resolution_fallback
            ? resolutionFallbackRestartLabel(job.resolution_fallback, "job")
            : undefined;
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
                <button
                  className="icon-button danger"
                  type="button"
                  title="仅删除任务"
                  aria-label={`仅删除任务 ${title}`}
                  onClick={() => onDelete(job.id, false)}
                >
                  <Trash2 size={18} />
                </button>
                <button
                  className="icon-button danger"
                  type="button"
                  title="删除任务和已下载文件"
                  aria-label={`删除任务和已下载文件 ${title}`}
                  onClick={() => onDelete(job.id, true)}
                >
                  <Trash2 size={18} />
                </button>
              </div>
            </div>
            <div className="job-metrics">
              <span>{formatPercent(job.progress)}</span>
              <span>开始 {formatDateTime(job.started_at)}</span>
              <span>结束 {formatDateTime(job.finished_at)}</span>
              <span>分辨率 {job.actual_resolution ?? "检测中"}</span>
              <span>格式 {job.actual_format ?? "检测中"}</span>
              <span>已用 {formatClock(job.elapsed_seconds)}</span>
              <span>剩余 {formatClock(job.eta)}</span>
              {job.speed ? <span>{formatBytesPerSecond(job.speed)}</span> : <span>-- KB/s</span>}
            </div>
            <div className="progress-bar">
              <span style={{ width: `${Math.max(0, Math.min(100, job.progress))}%` }} />
            </div>
            {!isPlaylist && job.resolution_fallback && (
              <ResolutionFallbackNotice
                fallback={job.resolution_fallback}
                restartLabel={jobRestartLabel}
                restartAriaLabel={jobRestartLabel ? `${jobRestartLabel} ${title}` : undefined}
                onRestart={
                  jobRestartResolution
                    ? () => onRestart(job.id, jobRestartResolution)
                    : undefined
                }
              />
            )}
            {job.items.length > 0 && isPlaylist && isExpanded && (
              <div className="item-list">
                {(() => {
                  const selectedItemIds = Array.from(selectedItemIdsByJob[job.id] ?? []);
                  return selectedItemIds.length > 0 ? (
                    <div className="item-batch-toolbar" aria-label={`批量视频操作 ${title}`}>
                      <span>{selectedItemIds.length} 个视频已选择</span>
                      <button
                        type="button"
                        className="ghost-button danger"
                        onClick={() => onDeleteItems(job.id, selectedItemIds, false)}
                      >
                        <Trash2 size={15} />
                        删除已选视频任务
                      </button>
                      <button
                        type="button"
                        className="ghost-button danger"
                        onClick={() => onDeleteItems(job.id, selectedItemIds, true)}
                      >
                        <Trash2 size={15} />
                        删除已选任务和已下载文件
                      </button>
                    </div>
                  ) : null;
                })()}
                {job.items.map((item) => {
                  const itemRestartResolution = item.resolution_fallback?.restart_resolution ?? null;
                  const itemRestartLabel = item.resolution_fallback
                    ? resolutionFallbackRestartLabel(item.resolution_fallback, "item")
                    : undefined;
                  return (
                  <div key={item.id} className="job-item-detail">
                    <div className="item-row">
                      <label className="item-title-select">
                        <input
                          aria-label={`选择视频任务 ${item.title}`}
                          type="checkbox"
                          checked={selectedItemIdsByJob[job.id]?.has(item.id) ?? false}
                          onChange={() => toggleItemSelection(job.id, item.id)}
                        />
                        <span>{item.index}. {item.title} · {item.status}</span>
                      </label>
                      <div className="item-actions">
                        {item.error && !item.resolution_fallback && <span className="item-error">{item.error}</span>}
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
                        <button
                          className="icon-button item-action-button danger"
                          type="button"
                          title="仅删除视频任务"
                          aria-label={`仅删除视频任务 ${item.title}`}
                          onClick={() => onDeleteItems(job.id, [item.id], false)}
                        >
                          <Trash2 size={16} />
                        </button>
                        <button
                          className="icon-button item-action-button danger"
                          type="button"
                          title="删除视频任务和已下载文件"
                          aria-label={`删除视频任务和已下载文件 ${item.title}`}
                          onClick={() => onDeleteItems(job.id, [item.id], true)}
                        >
                          <Trash2 size={16} />
                        </button>
                      </div>
                    </div>
                    <div className="item-metrics">
                      <span>{formatPercent(item.progress)}</span>
                      <span>{formatFileSize(item.downloaded_bytes)} / {formatFileSize(item.total_bytes)}</span>
                      <span>分辨率 {formatItemResolution(item)}</span>
                      <span>格式 {item.actual_format ?? "检测中"}</span>
                      <span>已用 {formatClock(item.elapsed_seconds)}</span>
                      <span>剩余 {formatClock(item.eta)}</span>
                      {item.speed ? <span>{formatBytesPerSecond(item.speed)}</span> : <span>-- KB/s</span>}
                    </div>
                    {item.resolution_fallback && (
                      <ResolutionFallbackNotice
                        fallback={item.resolution_fallback}
                        restartLabel={itemRestartLabel}
                        restartAriaLabel={itemRestartLabel ? `${itemRestartLabel} ${item.title}` : undefined}
                        onRestart={
                          itemRestartResolution
                            ? () => onRestartItem(job.id, item.id, itemRestartResolution)
                            : undefined
                        }
                      />
                    )}
                    <div className="progress-bar item-progress">
                      <span style={{ width: `${Math.max(0, Math.min(100, item.progress))}%` }} />
                    </div>
                  </div>
                  );
                })}
              </div>
            )}
          </article>
          );
        })}
      </div>
    </section>
  );
}

function ResolutionFallbackNotice({
  fallback,
  restartLabel,
  restartAriaLabel,
  onRestart
}: {
  fallback: ResolutionFallback;
  restartLabel?: string;
  restartAriaLabel?: string;
  onRestart?: () => void;
}) {
  return (
    <div className="resolution-fallback-note">
      <span>{fallback.message}</span>
      {onRestart && restartLabel && restartAriaLabel && (
        <button className="ghost-button" type="button" aria-label={restartAriaLabel} onClick={onRestart}>
          <RotateCcw size={15} />
          {restartLabel}
        </button>
      )}
    </div>
  );
}

