import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Copy, Loader2, PlugZap, RefreshCw, XCircle } from "lucide-react";
import { getDiagnostics, getSettings, refreshRuntimeDiagnostics, testProxy } from "../api";
import type { Diagnostics, ProxyTestResult, Settings } from "../types";

/**
 * 代理与运行环境的自检面板。
 *
 * 设计目标（用户视角）：
 * 1. **先给结论**：现在到底走没走代理、通不通、用了几毫秒，一眼看完；
 * 2. **再给动作**：一个「检测代理」按钮，和一个「先试这个地址再保存」的按钮；
 * 3. **最后给知识**：折叠起来的「不知道填什么」——常见代理软件端口、三步操作，
 *    以及最容易搞混的一条：浏览器能上网 ≠ 应用能上网。
 *
 * 这里刻意**不**把失败只写成一行红字：每次失败都必须同时给出 next_steps，
 * 否则用户只能回来问「那我现在该干嘛」。
 */

const PROXY_PRESETS = [
  { software: "Clash / Clash Verge / Mihomo", address: "127.0.0.1:7890", note: "混合端口（HTTP + SOCKS 同一个口）" },
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

function Collapsible({ title, children, defaultOpen = false }: { title: string; children: React.ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="collapsible">
      <button className="collapsible-head" type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        {title}
      </button>
      {open && <div className="collapsible-body">{children}</div>}
    </div>
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

      <Collapsible title="不知道填什么？常见代理软件的本地端口">
        <table className="preset-table">
          <thead>
            <tr>
              <th>软件</th>
              <th>通常填这个</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            {PROXY_PRESETS.map((preset) => (
              <tr key={preset.software}>
                <td>{preset.software}</td>
                <td>
                  <button className="link-button" type="button" onClick={() => onDraftProxyChange(preset.address)}>
                    {preset.address}
                  </button>
                </td>
                <td>{preset.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="hint">
          填好后点「先试输入框里的地址」——通过再离开输入框保存。留空表示自动（优先用 Windows 系统代理）；填 <code>direct</code> 表示强制直连。
        </p>
      </Collapsible>

      <Collapsible title="浏览器能打开 YouTube，应用却不能？按这个顺序查">
        <ol className="diag-steps">
          <li>先点「检测代理」：若显示 HTTP 404 以外的状态码或直接超时，问题就在代理本身，不在本应用。</li>
          <li>确认代理软件在运行。代理软件关掉时，系统代理设置会被清空或仍指向一个没人监听的端口，两种都会让请求干等到超时。</li>
          <li>核对端口。Clash 常见 7890、v2rayN 常见 10808/10809、SS 常见 1080，填错端口是最高频的原因。</li>
          <li>
            浏览器可能走的是「系统代理」或它自己的插件代理，而本应用用的是这里填的地址 —— 所以「浏览器行」不能证明「应用行」。
            反之也一样：本应用用了设置里的地址，即使浏览器挂了代理插件也不受影响。
          </li>
          <li>如果本机确实需要走直连（例如公司内网），填 <code>direct</code>；这会让请求立刻失败而不是等 30 秒超时。</li>
        </ol>
      </Collapsible>

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
