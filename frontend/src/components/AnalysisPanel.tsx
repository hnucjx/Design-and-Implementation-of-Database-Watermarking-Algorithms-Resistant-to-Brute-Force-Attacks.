import { FileText } from "lucide-react";
import { formatDuration } from "../formatting";
import { formatSelectedQualitySize } from "../quality";
import type { AnalyzeResponse, DownloadOptions } from "../types";

/**
 * 解析结果面板：缩略图/标题/时长，单视频显示汇总，playlist 显示可勾选条目表。
 *
 * `selectedItems` 的**状态归调用方**（提交任务时要用同一份选择），这里只负责渲染与回调 ——
 * 面板自己存一份就会出现「勾了但提交的是旧的」。
 */
export function AnalysisPanel({
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
