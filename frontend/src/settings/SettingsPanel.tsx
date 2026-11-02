import { useEffect, useState } from "react";
import { Folder, Play, Settings as SettingsIcon } from "lucide-react";
import { BiliLoginSection } from "../bilibili/BiliLoginSection";
import { HelpPopover } from "../shared/ui/HelpPopover";
import { ProxyPortsPopover, ProxySection } from "./ProxySection";
import { selectDownloadDirectory, updateSettings } from "../shared/api";
import { COMPLETION_NOTIFICATION_OPTIONS } from "../notifications/notifications";
import type { CompletionNotification, Settings } from "../contracts/types";

/**
 * 设置面板：下载目录、并发、单视频并发下载数（aria2c）、代理（含连通性检测）、B站 上传、保存状态回显。
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
 *   （`YTDL_ARIA2C_ENABLED`，见 [config.py](../../../backend/app/contracts/config.py#L68)），而且
 *   `default_aria2c` 是 profile 链里的**第二个**（`default` 失败后才轮到它），
 *   所以「启用」不等于「每次下载都走」。这两条都写在说明浮层里，别从界面文案里删掉。
 *
 * B站 区（`BiliLoginSection`）是**自包含**的：它只管登录与体检，失败经 `onError` 走页顶提示条 ——
 * 它自己的面板里已经有体检清单，再在面板里堆一条红字只会更乱。
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

export function SettingsPanel({
  settings,
  onSettingsChange,
  onError,
  onPreviewNotification
}: {
  settings: Settings;
  onSettingsChange: (settings: Settings) => void;
  /** B站 区的失败提示。这里只负责上报，显示交给页面顶部的公共提示条（与其它错误同一个出口）。 */
  // `null` = 清掉页顶那条旧错误（开始新动作时用）。B站 区是自包含的，
  // 它得能把上一条与自己无关的红条撤掉，否则用户会把它读成本次失败。
  onError: (message: string | null) => void;
  /**
   * 按**当前选中的方式**演示一次提醒（声音就响、弹窗就弹）。
   *
   * 为什么需要它：「提示音」这三个字说不出那声「叮」到底什么样，而没有它，用户只能
   * 等一个真实任务跑完才知道自己选了什么 —— 那正是这一类开关最容易留下的疑问。
   * 演示由 `App` 执行：声音与浮层都在那一层，这里只负责触发。
   */
  onPreviewNotification: () => void;
}) {
  const [draft, setDraft] = useState(settings);
  const [saveMessage, setSaveMessage] = useState("");

  useEffect(() => setDraft(settings), [settings]);

  const notificationMode = draft.completion_notification ?? "both";
  // 必须写明它和下载选项里那个「完成后通知」的关系：那是「这一次要不要提醒」的逐任务开关，
  // 这里是「提醒用什么方式」。只说方式不说关系，用户会遇到「我选了提示音，怎么没响」。
  const notificationHint =
    notificationMode === "off"
      ? "任务跑完不做任何提示；下载选项里的「完成后通知」会显示成不可勾并说明原因。"
      : `${
          COMPLETION_NOTIFICATION_OPTIONS.find((option) => option.value === notificationMode)?.hint ?? ""
        }。要不要提醒由下载选项里的「完成后通知」逐任务决定。`;

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

  async function saveCompletionNotification(mode: CompletionNotification) {
    if (mode === settings.completion_notification) return;
    setSaveMessage("保存中...");
    try {
      onSettingsChange(await updateSettings({ completion_notification: mode }));
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
          <label className="field-label" htmlFor="completion-notification">
            任务完成提醒
          </label>
        </div>
        <div className="notification-mode-row">
          <select
            id="completion-notification"
            aria-label="任务完成提醒方式"
            value={draft.completion_notification ?? "both"}
            onChange={(event) => void saveCompletionNotification(event.target.value as CompletionNotification)}
          >
            {COMPLETION_NOTIFICATION_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="ghost-button"
            disabled={(draft.completion_notification ?? "both") === "off"}
            title={(draft.completion_notification ?? "both") === "off" ? "当前已关闭提醒" : "按当前方式演示一次"}
            onClick={onPreviewNotification}
          >
            <Play size={14} />
            预览
          </button>
        </div>
        <p className="notification-mode-hint">{notificationHint}</p>
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
      <BiliLoginSection onError={onError} />
      {saveMessage && <span className="settings-save-status">{saveMessage}</span>}
    </section>
  );
}
