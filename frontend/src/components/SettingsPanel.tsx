import { useEffect, useState } from "react";
import { Folder, Settings as SettingsIcon } from "lucide-react";
import { ProxyPortsPopover, ProxySection } from "./ProxySection";
import { selectDownloadDirectory, updateSettings } from "../api";
import type { Settings } from "../types";

/**
 * 设置面板：下载目录、并发、aria2c 连接数、代理（含连通性检测）、保存状态回显。
 *
 * 面板持有一份 `draft`（输入过程中不落库），**失焦即保存**（`onBlur`），保存成功由
 * `onSettingsChange` 把权威值回灌。因此「先试后改」在输入框上是安全的：没失焦就不发请求。
 *
 * 两个语义陷阱（改动前先读注释再动手）：
 * - 代理留空 = 回到「自动」，不是「强制直连」—— 要直连必须显式填 `direct`。
 * - 并发是**视频（item）级**的，单视频任务只有 1 个 item，因此对单视频恒等于 1；
 *   界面文案必须说明这一点，否则「调了没变化」会被当成 bug（见 [ai/perf/PLAN.md](../../../ai/perf/PLAN.md)）。
 */
export function SettingsPanel({ settings, onSettingsChange }: { settings: Settings; onSettingsChange: (settings: Settings) => void }) {
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

  async function saveAria2cConnections(value: number) {
    const nextConnections = Math.min(4, Math.max(1, Number(value) || settings.aria2c_connections));
    if (nextConnections === settings.aria2c_connections) return;
    setSaveMessage("保存中...");
    try {
      onSettingsChange(
        await updateSettings({
          aria2c_connections: nextConnections
        })
      );
      setSaveMessage("已保存");
    } catch {
      setSaveMessage("保存失败");
    } finally {
      window.setTimeout(() => setSaveMessage(""), 1800);
    }
  }

  async function saveProxy(value: string) {
    const nextProxy = value.trim();
    if (nextProxy === (settings.proxy ?? "")) return;
    setSaveMessage("保存中...");
    try {
      onSettingsChange(
        await updateSettings({
          // 空值传 null = 回到「自动」，而不是「强制直连」（要直连请显式填 direct）。
          proxy: nextProxy === "" ? null : nextProxy
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
        <span>并发（同时下载的视频数；单个视频无效）</span>
        <input
          type="number"
          min={1}
          value={draft.default_concurrency ?? 5}
          onChange={(event) => setDraft({ ...draft, default_concurrency: Number(event.target.value) })}
          onBlur={(event) => void saveConcurrency(Number(event.currentTarget.value))}
        />
      </label>
      <label className="field settings-number-field">
        <span>aria2c 连接数（1–4，仅在启用 aria2c 后生效）</span>
        <input
          type="number"
          min={1}
          max={4}
          value={draft.aria2c_connections ?? 2}
          onChange={(event) => setDraft({ ...draft, aria2c_connections: Number(event.target.value) })}
          onBlur={(event) => void saveAria2cConnections(Number(event.currentTarget.value))}
        />
      </label>
      <div className="field">
        <div className="field-label-row">
          <label className="field-label" htmlFor="proxy-address">
            代理（留空 = 自动；填 direct 强制直连）
          </label>
          <ProxyPortsPopover onPick={(value) => setDraft({ ...draft, proxy: value })} />
        </div>
        <input
          id="proxy-address"
          value={draft.proxy ?? ""}
          placeholder="例如 127.0.0.1:7890"
          onChange={(event) => setDraft({ ...draft, proxy: event.target.value })}
          onBlur={(event) => void saveProxy(event.currentTarget.value)}
        />
      </div>
      <ProxySection
        settings={settings}
        draftProxy={draft.proxy ?? ""}
        onDraftProxyChange={(value) => setDraft({ ...draft, proxy: value })}
        onSettingsChange={onSettingsChange}
      />
      {saveMessage && <span className="settings-save-status">{saveMessage}</span>}
    </section>
  );
}
