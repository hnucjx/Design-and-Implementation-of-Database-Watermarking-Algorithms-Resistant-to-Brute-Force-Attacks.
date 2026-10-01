import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Copy, Loader2, PlugZap, RefreshCw, XCircle } from "lucide-react";
import { getDiagnostics, getSettings, refreshRuntimeDiagnostics, testProxy } from "../api";
import { HelpPopover } from "./HelpPopover";
import type { Diagnostics, ProxyTestResult, Settings } from "../types";

/**
 * 代理与运行环境的自检面板。
 *
 * 设计目标（用户视角）：
 * 1. **先给结论**：现在到底走没走代理、通不通、用了几毫秒，一眼看完；
 * 2. **再给动作**：一个「检测代理」按钮，和一个「先试这个地址再保存」的按钮；
 * 3. **最后给知识**：收进浮层的两项说明——常用本地端口挂在代理输入框旁，
 *    连通性排查顺序挂在这块结果区旁。两者都不占主功能区版面。
 *
 * 这里刻意**不**把失败只写成一行红字：每次失败都必须同时给出 next_steps，
 * 否则用户只能回来问「那我现在该干嘛」。
 */

const PROXY_PRESETS = [
  { software: "Clash / Clash Verge / Mihomo", address: "127.0.0.1:7890", note: "混合端口（HTTP 与 SOCKS 共用同一端口）" },
  { software: "v2rayN", address: "127.0.0.1:10809", note: "HTTP 代理端口；SOCKS 端口通常是 10808" },
  { software: "Shadowsocks / SS 客户端", address: "127.0.0.1:1080", note: "本地 SOCKS5 端口，本应用同样支持" },
  { software: "Surge / Quantumult 等", address: "127.0.0.1:6152", note: "HTTP 代理端口" }
];

export function proxySourceLabel(source: Settings["proxy_source"] | undefined): string {
  switch (source) {
    case "setting":
      return "你手动填写的地址";
    case "system":
      return "Windows 系统代理";
    case "environment":
      return "环境变量（HTTP_PROXY / HTTPS_PROXY）";
    case "direct":
      return "强制直连";
    default:
      return "没有发现代理";
  }
}

export function proxyHint(settings: Settings): string {
  if (settings.proxy_source === "direct") return "当前强制直连，不使用任何代理。";
  if (!settings.proxy_effective) {
    return "当前直连：没有填写代理，也没发现 Windows 系统代理或 HTTP_PROXY/HTTPS_PROXY 环境变量。";
  }
  return `当前生效：${settings.proxy_effective}（来源：${proxySourceLabel(settings.proxy_source)}）`;
}

function CopyableCommand({ command, label }: { command: string; label?: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }

  return (
    <span className="command-row">
      <code className="command-code">{command}</code>
      <button className="ghost-button command-copy" type="button" onClick={() => void copy()}>
        <Copy size={14} />
        {copied ? "已复制" : (label ?? "复制")}
      </button>
    </span>
  );
}

/** 挂在代理输入框旁：常见代理软件在本机监听的端口，点一下即可填入。 */
export function ProxyPortsPopover({ onPick }: { onPick: (address: string) => void }) {
  return (
    <HelpPopover id="proxy-ports" label="常用端口" title="常见代理软件的本地端口">
      <p>
        本地端口指代理软件在本机 <code>127.0.0.1</code> 上监听的端口，以代理软件设置界面中显示的值为准。
        下列为常见默认值，点击地址即可填入输入框。
      </p>
      <ul className="preset-list">
        {PROXY_PRESETS.map((preset) => (
          <li key={preset.software}>
            <span className="preset-name">{preset.software}</span>
            <button
              className="link-button"
              type="button"
              onClick={() => onPick(preset.address)}
              // 同「先试输入框里的地址」：不让这次点击把输入框弄失焦，
              // 否则 onBlur 会把还没验证过的地址直接存进设置。
              onMouseDown={(event) => event.preventDefault()}
            >
              {preset.address}
            </button>
            <span className="preset-note">{preset.note}</span>
          </li>
        ))}
      </ul>
      <p>
        本应用同时支持 HTTP 与 SOCKS5（一种代理协议，通常用于 Shadowsocks 类客户端）。
      </p>
      <p>
        填入后点「先试输入框里的地址」验证，通过后再离开输入框保存。留空表示自动（优先使用 Windows 系统代理）；
        填 <code>direct</code> 表示强制直连。
      </p>
    </HelpPopover>
  );
}

/** 挂在检测结果旁：浏览器可访问而应用不可访问时的定位顺序。 */
export function ProxyTroubleshootingPopover() {
  return (
    <HelpPopover id="proxy-flow" label="排查顺序" title="浏览器可访问而应用不可访问时的排查顺序">
      <ol className="help-list">
        <li>
          点「检测代理」确认出口连通性。返回非 200 的 HTTP 状态码或直接超时，
          说明问题在代理链路本身，不在本应用。
        </li>
        <li>
          确认代理软件处于运行状态。代理软件退出后，系统代理设置可能被清空，或仍指向一个没有进程监听的端口，
          两种情形都会让请求等待至超时。
        </li>
        <li>
          核对端口。Clash 常用 <code>7890</code>，v2rayN 常用 <code>10808</code> / <code>10809</code>，
          Shadowsocks 常用 <code>1080</code>；端口填写错误是最常见的成因。
        </li>
        <li>
          区分两条独立路径：浏览器可能使用系统代理或浏览器插件的代理，本应用使用设置面板中填写的地址。
          因此浏览器可访问不能证明本应用可访问，反之亦然。
        </li>
        <li>
          若本机确需直连（例如公司内网），填写 <code>direct</code>：请求会立即失败，而不是等待 30 秒超时。
        </li>
      </ol>
    </HelpPopover>
  );
}

export function ProxySection({
  settings,
  draftProxy,
  onDraftProxyChange,
  onSettingsChange
}: {
  settings: Settings;
  /** 设置面板里那个输入框的当前值。测试/试连都用它，保证「试的就是你看到的」。 */
  draftProxy: string;
  onDraftProxyChange: (value: string) => void;
  onSettingsChange: (settings: Settings) => void;
}) {
  const [result, setResult] = useState<ProxyTestResult | null>(null);
  const [testing, setTesting] = useState<"saved" | "draft" | null>(null);
  const [error, setError] = useState("");
  const [runtime, setRuntime] = useState<Diagnostics | null>(null);
  const [runtimeBusy, setRuntimeBusy] = useState(false);

  useEffect(() => {
    void getDiagnostics()
      .then(setRuntime)
      .catch(() => setRuntime(null));
  }, []);

  async function runTest(mode: "saved" | "draft") {
    setTesting(mode);
    setError("");
    try {
      setResult(await testProxy(mode === "draft" ? draftProxy.trim() : undefined));
    } catch (err) {
      setError(err instanceof Error ? err.message : "检测失败");
      setResult(null);
    } finally {
      setTesting(null);
    }
  }

  async function rerunRuntimeCheck() {
    setRuntimeBusy(true);
    try {
      setRuntime(await refreshRuntimeDiagnostics());
      onSettingsChange(await getSettings());
    } catch (err) {
      setError(err instanceof Error ? err.message : "自检失败");
    } finally {
      setRuntimeBusy(false);
    }
  }

  const jsRuntimeReady = runtime?.dependencies?.js_runtime === true;
  const jsRuntimeError = typeof runtime?.dependencies?.js_runtime_error === "string" ? runtime.dependencies.js_runtime_error : null;

  return (
    <div className="proxy-section">
      <p className="hint" role="status">
        {proxyHint(settings)}
      </p>

      <div className="diag-actions">
        <button className="ghost-button" type="button" onClick={() => void runTest("saved")} disabled={testing !== null}>
          {testing === "saved" ? <Loader2 className="spin" size={15} /> : <PlugZap size={15} />}
          检测代理
        </button>
        <button
          className="ghost-button"
          type="button"
          onClick={() => void runTest("draft")}
          // 关键：先拦住 mousedown，避免这次点击让上面的输入框失焦。
          // 输入框是 onBlur 自动保存的，一旦失焦就把还没验证过的地址写进了设置——
          // 那就等于「先试」变成了「先保存」，一个填错的端口会先被落盘再报错。
          onMouseDown={(event) => event.preventDefault()}
          disabled={testing !== null || draftProxy.trim() === ""}
          title={draftProxy.trim() === "" ? "先在输入框里填一个地址" : `用 ${draftProxy.trim()} 检测，不会保存`}
        >
          {testing === "draft" ? <Loader2 className="spin" size={15} /> : <PlugZap size={15} />}
          先试输入框里的地址
        </button>
      </div>

      {error && <p className="diag-error">{error}</p>}

      {result && (
        <div className={`diag-result ${result.ok ? "is-ok" : "is-bad"}`} role="status">
          <span className="diag-result-head">
            {result.ok ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
            {result.summary}
          </span>
          <ul className="diag-facts">
            <li>来源：{proxySourceLabel(result.source)}（{result.source}）</li>
            <li>代理：{result.proxy ?? "直连（不使用代理）"}</li>
            <li>探测地址：{result.probe_url}</li>
            <li>
              HTTP 状态：{result.http_status ?? "无响应"}　耗时：{result.elapsed_ms} ms　收到：{result.bytes_read} 字节
            </li>
            {result.error && <li>原始错误：{result.error}</li>}
          </ul>
          {result.next_steps.length > 0 && (
            <ol className="diag-steps">
              {result.next_steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          )}
        </div>
      )}

      <div className="diag-help-row">
        <ProxyTroubleshootingPopover />
      </div>

      <div className={`runtime-block ${jsRuntimeReady ? "is-ok" : "is-warn"}`}>
        <span className="runtime-head">
          {jsRuntimeReady ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
          JS 运行时（解析 YouTube 的 n 参数，登录状态下必须）：{jsRuntimeReady ? "可用" : "不可用"}
        </span>
        {jsRuntimeReady ? (
          <p className="hint">
            {String(runtime?.dependencies?.js_runtime_name ?? "")}　{runtime?.dependencies?.js_runtime_path as string}　
            v{runtime?.dependencies?.js_runtime_version as string}
          </p>
        ) : (
          <>
            <p className="hint">
              装一个 Node 18+ 或 Deno 即可。装完点「重新自检」。
              这个组件不可用时，最典型的症状是下载报 <code>The page needs to be reloaded.</code>，而不是给出任何有用的提示。
            </p>
            {jsRuntimeError && <p className="diag-error">自检报错：{jsRuntimeError}</p>}
          </>
        )}
        {runtime?.sanitized_environment && runtime.sanitized_environment.length > 0 && (
          <p className="hint">
            启动时已自动摘除会打坏 JS 运行时的宿主环境变量：
            {runtime.sanitized_environment.map((item) => `${item.name}（${item.reason.split("：")[0]}）`).join("、")}
          </p>
        )}
        <div className="diag-actions">
          <button className="ghost-button" type="button" onClick={() => void rerunRuntimeCheck()} disabled={runtimeBusy}>
            {runtimeBusy ? <Loader2 className="spin" size={15} /> : <RefreshCw size={15} />}
            重新自检
          </button>
        </div>
        {runtime?.log_file && (
          <p className="hint">
            日志文件：<code>{runtime.log_file}</code> —— 下载失败时先看这个文件的最后几十行。
            <br />
            <CopyableCommand command={runtime.log_file} label="复制路径" />
          </p>
        )}
      </div>
    </div>
  );
}
