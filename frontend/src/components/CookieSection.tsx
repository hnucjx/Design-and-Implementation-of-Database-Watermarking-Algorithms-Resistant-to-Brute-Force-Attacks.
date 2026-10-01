import { useState } from "react";
import { AlertTriangle, CheckCircle2, Copy, Cookie, Loader2, RefreshCw, ShieldCheck, XCircle } from "lucide-react";
import { verifyCookies } from "../api";
import { HelpPopover } from "./HelpPopover";
import type { CookieHealth } from "../types";

/**
 * cookies 面板：把「我这个 cookies 到底有没有用」变成一个能点出来的结论。
 *
 * 为什么值得单独做一块：一个**看起来完全正常**的 cookies.txt 可能是废的 ——
 * 鉴权 cookie 全落在 ``.google.com`` 上时，文件里有 SID、yt-dlp 也不报错，
 * 但每个请求都是匿名的。光看「文件存在」永远发现不了，只能靠域名与 LOGGED_IN 校验。
 *
 * 说明性内容（获取方式、结论解读）一律走 HelpPopover，不占主功能区版面：
 * 获取方式挂在 cookies 输入区旁边，结论解读挂在这块结果区旁边。
 */

const EXPORT_COMMAND = "python scripts/export_cookies_via_cdp.py";

function CopyButton({ text, label }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }

  return (
    <button className="ghost-button command-copy" type="button" onClick={() => void copy()}>
      <Copy size={14} />
      {copied ? "已复制" : (label ?? "复制")}
    </button>
  );
}

export function loginStateLabel(health: CookieHealth | null): { text: string; tone: "ok" | "bad" | "warn" } {
  if (!health || !health.present) return { text: "未配置", tone: "warn" };
  // 「域名不对」必须排在「登录态无效」之前：一个 cookie 全落在 .google.com 的文件，
  // 联网校验当然也会说「被当成匿名」，但真正要用户动手改的是导出方式，不是重新登录。
  if (health.youtube_domain_count === 0) return { text: "域名不对，等于没配", tone: "bad" };
  if (health.logged_in === true) return { text: "登录态有效", tone: "ok" };
  if (health.logged_in === false) return { text: "登录态无效", tone: "bad" };
  if (!health.auth_cookie_names.length) return { text: "只有匿名 cookie", tone: "warn" };
  return { text: "含鉴权 cookie（未联网确认）", tone: "warn" };
}

/** 挂在 cookies 输入区旁边：拿到一份能用文件的三种途径。 */
export function CookieHowToPopover() {
  return (
    <HelpPopover id="cookie-howto" label="获取方式" title="Cookie 获取方式">
      <ol className="help-list">
        <li>
          <strong>导出脚本（推荐）。</strong>它会启动一个使用<strong>独立配置</strong>的浏览器窗口，不影响你正在用的浏览器；
          首次在弹出的窗口中登录一次 Google 账号，脚本随后自动导出到 <code>data/cookies.txt</code>。
          <br />
          <span className="command-row">
            <code className="command-code">{EXPORT_COMMAND}</code>
            <CopyButton text={EXPORT_COMMAND} />
          </span>
          <ul>
            <li>
              窗口必须是<strong>有头模式</strong>（可见窗口）。无头模式下 YouTube 不会签发 <code>.youtube.com</code> 的鉴权 cookie
              （鉴权 cookie：用来证明「这个请求来自已登录账号」的条目），导出的文件看似正常，实际每个请求都是匿名的。
            </li>
            <li>
              脚本会先校验 cookie 所属域名，取不到 <code>.youtube.com</code> 时<strong>拒绝写文件</strong>（退出码 2），
              因此不会用一份无效文件覆盖掉当前可用的那份。
            </li>
          </ul>
        </li>
        <li>
          <strong>浏览器扩展。</strong>安装 <em>Get cookies.txt LOCALLY</em> 之类的扩展，
          在 <code>youtube.com</code> 页面导出，另存为 <code>data/cookies.txt</code>。
          导出格式须选 <strong>Netscape / cookies.txt</strong>，不要选 JSON。
        </li>
        <li>
          <strong>直接上传文件。</strong>用上方的「选择 cookies」上传即可；文件须为 Netscape 格式，
          即 Tab 分隔的 7 列。
        </li>
      </ol>
      <p>
        <strong>不支持的方式：</strong>应用不读取你正在使用的 Edge / Chrome 配置目录。
        Edge 的 cookie 采用 v20 app-bound 加密（密钥绑定正在运行的浏览器进程，任何离线程序都无法解密）；
        挂载真实配置目录的变通做法会<strong>破坏浏览器的 cookie 库</strong>，该路径已从应用中移除。
      </p>
    </HelpPopover>
  );
}

/** 挂在校验结果旁：四种典型结论分别意味着什么、下一步做什么。 */
export function CookieVerdictPopover() {
  return (
    <HelpPopover id="cookie-verdict" label="结论解读" title="Cookie 校验结论解读">
      <ul className="help-list">
        <li>
          <strong>youtube.com 域上计数为 0</strong>：文件无效。常见成因是鉴权 cookie 全部落在 <code>.google.com</code>，
          而 <code>.google.com</code> 的 cookie<strong>不会</strong>随请求发送到 <code>www.youtube.com</code>。需重新导出。
        </li>
        <li>
          <strong>只有匿名 cookie</strong>（<code>VISITOR_INFO1_LIVE</code> / <code>YSC</code> / <code>PREF</code>）：
          导出时未处于登录状态。登录后重新导出。
        </li>
        <li>
          <strong>域名与鉴权项正确，联网校验仍判定为匿名</strong>：多为登录态过期，或导出后出口 IP 发生变更
          （登录态与出口 IP 绑定，换出口即失效）。
        </li>
        <li>
          <strong>校验通过但下载仍返回 403</strong>：问题通常在代理出口的稳定性或并发设置。
          建议将并发调整为 1，并确认代理未在过程中更换 IP。
        </li>
      </ul>
    </HelpPopover>
  );
}

export function CookieSection({
  cookiesEnabled,
  onVerified
}: {
  cookiesEnabled: boolean;
  onVerified?: (health: CookieHealth) => void;
}) {
  const [health, setHealth] = useState<CookieHealth | null>(null);
  const [verifying, setVerifying] = useState<"deep" | "offline" | null>(null);
  const [error, setError] = useState("");

  async function runVerify(deep: boolean) {
    setVerifying(deep ? "deep" : "offline");
    setError("");
    try {
      const result = await verifyCookies(deep);
      setHealth(result);
      onVerified?.(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "校验失败");
    } finally {
      setVerifying(null);
    }
  }

  const label = loginStateLabel(health);
  const domains = health ? Object.entries(health.domains) : [];

  return (
    <div className="cookie-diagnostics">
      <div className="diag-actions">
        <button className="ghost-button" type="button" onClick={() => void runVerify(true)} disabled={verifying !== null}>
          {verifying === "deep" ? <Loader2 className="spin" size={15} /> : <ShieldCheck size={15} />}
          校验 cookies（联网确认登录态）
        </button>
        <button className="ghost-button" type="button" onClick={() => void runVerify(false)} disabled={verifying !== null}>
          {verifying === "offline" ? <Loader2 className="spin" size={15} /> : <Cookie size={15} />}
          只做离线体检
        </button>
        {health && (
          <button className="ghost-button" type="button" onClick={() => void runVerify(true)} disabled={verifying !== null}>
            <RefreshCw size={15} />
            重新校验
          </button>
        )}
      </div>

      {error && <p className="diag-error">{error}</p>}

      {!health && (
        <p className="hint">
          当前状态：{cookiesEnabled ? "已配置（未校验）" : "未配置"}。建议先点一次「校验 cookies」——
          <strong>文件存在不等于能用</strong>，只有校验证实了登录态，下载时的 403 才会真的变少。
        </p>
      )}

      {health && (
        <div className={`diag-result ${label.tone === "ok" ? "is-ok" : label.tone === "bad" ? "is-bad" : "is-warn"}`} role="status">
          <span className="diag-result-head">
            {label.tone === "ok" ? <CheckCircle2 size={16} /> : label.tone === "bad" ? <XCircle size={16} /> : <AlertTriangle size={16} />}
            {label.text} —— {health.verdict}
          </span>
          <ul className="diag-facts">
            <li>文件：{health.filename ?? "（无）"}　{health.size_bytes} 字节　{health.format_note}</li>
            <li>
              cookie 总数：{health.cookie_count}　其中 <strong>youtube.com 域上：{health.youtube_domain_count}</strong>
              {health.expired_count > 0 ? `　已过期：${health.expired_count}` : ""}
            </li>
            <li>
              域名分布：
              {domains.length ? domains.map(([domain, count]) => `${domain}×${count}`).join("、") : "（空）"}
            </li>
            <li>命中的鉴权项：{health.auth_cookie_names.length ? health.auth_cookie_names.join("、") : "（一个都没有）"}</li>
            <li>
              联网校验：{health.logged_in === true ? "已登录" : health.logged_in === false ? "被当成匿名" : "未确认"}
              {health.logged_in_detail ? `（${health.logged_in_detail}）` : ""}
            </li>
          </ul>
          {health.next_steps.length > 0 && (
            <ol className="diag-steps">
              {health.next_steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          )}
        </div>
      )}

      <div className="diag-help-row">
        <CookieVerdictPopover />
      </div>
    </div>
  );
}
