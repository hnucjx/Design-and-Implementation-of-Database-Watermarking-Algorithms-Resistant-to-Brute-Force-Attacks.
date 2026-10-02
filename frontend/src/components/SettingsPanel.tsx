import { useEffect, useState } from "react";
import { Folder, Settings as SettingsIcon } from "lucide-react";
import { HelpPopover } from "./HelpPopover";
import { ProxyPortsPopover, ProxySection } from "./ProxySection";
import { selectDownloadDirectory, updateSettings } from "../api";
import type { Settings } from "../types";

/**
 * 设置面板：下载目录、并发、单视频并发下载数（aria2c）、代理（含连通性检测）、保存状态回显。
 *
 * 面板持有一份 `draft`（输入过程中不落库），**失焦即保存**（`onBlur`），保存成功由
 * `onSettingsChange` 把权威值回灌。因此「先试后改」在输入框上是安全的：没失焦就不发请求。
 *
 * 三个语义陷阱（改动前先读注释再动手）：
 * - 代理留空 = 回到「自动」，不是「强制直连」—— 要直连必须显式填 `direct`。
 * - 并发是**视频（item）级**的，单视频任务只有 1 个 item，因此对单视频恒等于 1；
 *   界面文案必须说明这一点，否则「调了没变化」会被当成 bug（见 [ai/perf/PLAN.md](../../../ai/perf/PLAN.md)）。
 * - `aria2c_connections` 是**单视频内部**的分段并发（aria2c 的 `-x` / `-s`），与上面那个
 *   「并发」是两回事。它还有两个容易被漏掉的生效条件：aria2c 默认关闭
 *   （`YTDL_ARIA2C_ENABLED`，见 [config.py](../../../backend/app/config.py#L61)），而且
 *   `default_aria2c` 是 profile 链里的**第二个**（`default` 失败后才轮到它），
 *   所以「启用」不等于「每次下载都走」。这两条都写在说明浮层里，别从界面文案里删掉。
 */

/** 挂在这一项旁：它到底管什么、以及 aria2c 怎么查怎么开。 */
function Aria2cHelpPopover() {
  return (
    <HelpPopover id="aria2c-connections" label="怎么启用" title="单视频并发下载的作用与启用方式">
      <p>
        这一项决定把<strong>一个视频</strong>切成几段同时下载，只对单个视频生效。它和上面的「并发」
        不是一回事 —— 那一项管的是同时下载几个视频，对单视频无效。
      </p>
      <p>
        这项能力由 <code>aria2c</code>（一个命令行下载工具）提供，默认关闭。即使本机已经装了，
        也要在本应用里显式打开才会用上：
      </p>
      <ol className="help-list">
        <li>
          确认已安装：在终端里跑 <code>aria2c --version</code>，能打印版本号即可。
        </li>
        <li>
          打开开关：在仓库根的 <code>.env</code> 里加一行 <code>YTDL_ARIA2C_ENABLED=true</code>。
        </li>
        <li>重启应用 —— 改 <code>.env</code> 必须重启才生效。</li>
      </ol>
      <p>
        如果 aria2c 不在 PATH 里，再用 <code>YTDL_ARIA2C_PATH</code> 写它的完整路径
        （例如 <code>D:\tools\aria2c.exe</code>）。
      </p>
      <p>两点提醒：</p>
      <ul className="help-list">
        <li>
          它只在默认下载方式失败（403、连接被重置、要求登录校验、或 JS 校验解不出来）后、
          自动改用 aria2c 重试时才起作用 —— 不是每次下载都走。
        </li>
        <li>
          段数越多通常越快，但也更容易触发 403 和限速；出问题先降到 1，或直接关掉 aria2c。
          小于 16 MB 的片段不会被拆分，这种情况下调大没有效果。
        </li>
      </ul>
    </HelpPopover>
  );
}

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
      <div className="field settings-number-field">
        <div className="field-label-row">
          <label className="field-label" htmlFor="aria2c-connections">
            单视频并发下载数（1–4）
          </label>
          <Aria2cHelpPopover />
        </div>
        <input
          id="aria2c-connections"
          type="number"
          min={1}
          max={4}
          value={draft.aria2c_connections ?? 2}
          onChange={(event) => setDraft({ ...draft, aria2c_connections: Number(event.target.value) })}
          onBlur={(event) => void saveAria2cConnections(Number(event.currentTarget.value))}
        />
      </div>
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
