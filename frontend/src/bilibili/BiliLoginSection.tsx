import { useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, LogOut, QrCode, RefreshCw, Upload, Wrench } from "lucide-react";
import {
  createBiliQrcode,
  deleteBiliCookies,
  getBiliStatus,
  pollBiliLogin,
  repairBiliTool,
  uploadBiliCookies
} from "../shared/api";
import { errorMessage } from "../shared/errors";
import { BILI_CALL_TIMEOUT_MS, runHealthCheck } from "./biliHealth";
import type { BiliQrcode, BiliStatus } from "../contracts/types";

/** 轮询间隔。B站 的扫码状态没有推送，只能轮询；1.5 秒在「反应够快」与「不打扰服务端」之间。 */
const POLL_INTERVAL_MS = 1500;

/**
 * 设置面板里的 B站 区：环境体检 + 扫码登录 + 导入登录文件。
 *
 * 四条刻意的做法：
 *
 * 1. **体检结论永远显示「下一步」**。只显示「没找到投稿工具」等于把问题丢回给用户；
 *    每条提示都要带上要么一条命令、要么一个按钮。这是本功能的验收口径。
 * 2. **凭据一个字符都不显示**。界面上只出现「已保存 N 项 / 缺 SESSDATA」这种摘要 ——
 *    连掩码前缀都不给（掩码前缀截图一贴就是凭据的一部分）。要看登录的是谁，走账号名。
 * 3. **「登录文件被 git 跟踪」单独一条红字**：那等于把账号密码提交进了仓库，
 *    比「没登录」严重得多，且用户通常完全不知道。
 * 4. **「重新体检」必须看得出它跑了**：点下去立刻变「体检中…」，结束后写上「上次体检 11:58:03」。
 *    原先这个按钮只做一次静默请求 —— 结论没变时界面**一个像素都不动**，用户合理地认为它没实现。
 * 5. **点任何一个动作就先清掉页顶那条旧错误**。错误横幅是页面级的（不在这个面板里），
 *    上一条别的动作遗留的红条会一直挂在页顶，用户把它读成「我刚点的这个失败了」——
 *    实测有人把「重试投稿超时」读成「扫码登录失败」。见 ai/bug-fix/031。
 */
export function BiliLoginSection({ onError }: { onError: (message: string | null) => void }) {
  const [status, setStatus] = useState<BiliStatus | null>(null);
  const [qrcode, setQrcode] = useState<BiliQrcode | null>(null);
  const [pollMessage, setPollMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const [checkedAt, setCheckedAt] = useState("");
  const [checkNote, setCheckNote] = useState("");
  const pollTimer = useRef<number | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    void refresh();
    return stopPolling;
    // 只在挂载时体检一次：每次打开设置都请求 B站 没有必要。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function stopPolling() {
    if (pollTimer.current !== null) {
      window.clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
  }

  async function refresh() {
    setChecking(true);
    setCheckNote("");
    onError(null);
    try {
      // 超时不是失败：离线体检（不请求 B站）仍然能回答「工具装没装、登录文件在不在」。
      // 停在原来的界面上什么都不说，才是真正的失败 —— 策略本身在 `biliHealth` 里，可单测。
      const result = await runHealthCheck(() => getBiliStatus(true), () => getBiliStatus(false));
      setStatus(result.status);
      if (result.timedOut) {
        setCheckNote(
          `账号探测 ${Math.round(BILI_CALL_TIMEOUT_MS / 1000)} 秒还没回来（B站 此刻连不上），` +
            "下面是离线结论：投稿工具与登录文件的存在性。等网络恢复后再点一次「重新体检」就能拿到账号信息。"
        );
      } else if (result.probeError) {
        // 探测**失败了**（不是慢）：原因要说出来，否则用户看着「没有账号名」只能猜。
        // 离线结论仍然给 —— 投稿工具与登录文件的存在性本来就不需要网络。
        setCheckNote(
          `账号探测失败（${result.probeError}），下面是离线结论：投稿工具与登录文件的存在性。`
        );
      }
      setCheckedAt(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
    } catch (err) {
      onError(errorMessage(err, "读取 B 站 状态失败"));
    } finally {
      setChecking(false);
    }
  }

  async function startLogin() {
    setBusy(true);
    setPollMessage("");
    onError(null);
    try {
      const challenge = await createBiliQrcode();
      setQrcode(challenge);
      stopPolling();
      pollTimer.current = window.setInterval(() => void poll(challenge.qrcode_key), POLL_INTERVAL_MS);
    } catch (err) {
      onError(errorMessage(err, "获取登录二维码失败"));
    } finally {
      setBusy(false);
    }
  }

  async function poll(qrcodeKey: string) {
    try {
      const result = await pollBiliLogin(qrcodeKey);
      setPollMessage(result.message);
      if (result.state === "confirmed") {
        stopPolling();
        setQrcode(null);
        await refresh();
      } else if (result.state === "expired") {
        stopPolling();
        setQrcode(null);
      }
    } catch {
      // 单次轮询失败不中断：网络抖一下不该让用户重新扫码。
    }
  }

  async function importFile(file: File | null) {
    if (!file) return;
    setBusy(true);
    try {
      setStatus(await uploadBiliCookies(file));
    } catch (err) {
      onError(errorMessage(err, "导入登录文件失败"));
    } finally {
      setBusy(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function clear() {
    setBusy(true);
    try {
      setStatus(await deleteBiliCookies());
      setQrcode(null);
      stopPolling();
      setPollMessage("");
    } catch (err) {
      onError(errorMessage(err, "清除登录失败"));
    } finally {
      setBusy(false);
    }
  }

  async function repair() {
    setBusy(true);
    try {
      setStatus(await repairBiliTool());
    } catch (err) {
      onError(errorMessage(err, "修复失败"));
    } finally {
      setBusy(false);
    }
  }

  const login = status?.login;
  const account = login?.account ?? null;
  /**
   * 「找到了」不等于「能用」。
   *
   * WinGet 未开开发者模式时会在 `Links` 目录留一个 **0 字节的 `biliup.exe` 空壳**：
   * `found` 为真、`path` 也在，但 `CreateProcess` 拒绝执行它。若按 `found` 显示成绿色通过，
   * 界面就会一边打勾一边给「修复」按钮 —— 用户只能猜这个勾是什么意思。所以有 `problem`
   * 一律按不通过渲染，并把 `problem` 本身显示出来（只给按钮、不给原因同样是在让他猜）。
   */
  const biliupOk = Boolean(status?.biliup.found && !status.biliup.problem);

  return (
    <div className="field bili-login-section">
      <div className="field-label-row">
        <span className="field-label">B站 上传</span>
        {checkedAt && <span className="bili-checked-at">上次体检 {checkedAt}</span>}
        <button className="bili-link" type="button" onClick={() => void refresh()} disabled={checking}>
          <RefreshCw size={14} />
          {checking ? "体检中…" : "重新体检"}
        </button>
      </div>

      {checkNote && <p className="hint bili-check-note">{checkNote}</p>}

      <ul className="bili-check-list">
        <li className={biliupOk ? "is-ok" : "is-bad"}>
          {biliupOk ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}
          <span className="bili-check-name">投稿工具</span>
          <span className="bili-check-value">
            {status === null
              ? "检查中…"
              : // `problem` 优先：空壳的 `path` 看着完全正常，把它显示出来等于什么都没说。
                status.biliup.problem || status.biliup.version || status.biliup.path || "已找到"}
          </span>
          {status?.biliup.advice && (
            <span className="bili-check-action">
              {status.biliup.problem?.includes("0 字节") ? (
                <button className="ghost-button bili-retry" type="button" disabled={busy} onClick={() => void repair()}>
                  <Wrench size={15} />
                  修复
                </button>
              ) : (
                status.biliup.advice
              )}
            </span>
          )}
        </li>
        <li className={login?.present && !login.missing_required.length ? "is-ok" : "is-bad"}>
          {login?.present && !login.missing_required.length ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}
          <span className="bili-check-name">登录状态</span>
          <span className="bili-check-value">
            {login === undefined
              ? "检查中…"
              : account
                ? `已登录：${account.name || "（未取到昵称）"}`
                : login.present
                  ? `登录信息不完整，缺 ${login.missing_required.join("、") || "有效凭据"}`
                  : "还没有登录"}
          </span>
        </li>
        <li className={status?.ffmpeg.found ? "is-ok" : "is-warn"}>
          {status?.ffmpeg.found ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}
          <span className="bili-check-name">封面抽帧</span>
          <span className="bili-check-value">
            {status?.ffmpeg.found ? "ffmpeg 可用" : "没有 ffmpeg：不会自动生成封面，不影响上传"}
          </span>
        </li>
      </ul>

      {login && (
        <p className="hint">
          {login.present
            ? `登录文件里有 ${login.cookie_count} 项凭据（只显示数量，不显示内容）。`
            : "支持扫码登录，也可以导入一份已有的 cookies.json。"}
        </p>
      )}

      {login?.tracked_by_git && (
        <p className="warning-note">
          登录文件已经被 git 跟踪，等于把账号密码提交到了仓库里。请执行
          <code> git rm --cached &lt;该文件&gt; </code>
          并确认 <code>.gitignore</code> 覆盖它，然后重新登录。
        </p>
      )}

      <div className="bili-login-actions">
        <button className="ghost-button" type="button" onClick={() => void startLogin()} disabled={busy}>
          <QrCode size={16} />
          扫码登录
        </button>
        <button className="ghost-button" type="button" onClick={() => fileInput.current?.click()} disabled={busy}>
          <Upload size={16} />
          导入 cookies.json
        </button>
        {login?.present && (
          <button className="ghost-button danger" type="button" onClick={() => void clear()} disabled={busy}>
            <LogOut size={16} />
            清除登录
          </button>
        )}
        <input
          ref={fileInput}
          className="bili-file-input"
          type="file"
          accept=".json,.txt"
          aria-label="导入 B站 登录文件"
          onChange={(event) => void importFile(event.target.files?.[0] ?? null)}
        />
      </div>

      {qrcode && (
        <div className="bili-qr">
          {qrcode.svg ? (
            // 二维码是服务端用 `qrcode` 生成的 SVG 文本，直接内联即可（不牵入位图依赖）。
            <div className="bili-qr-image" dangerouslySetInnerHTML={{ __html: qrcode.svg }} />
          ) : (
            <p className="warning-note">{qrcode.note ?? "无法显示二维码，请改用导入 cookies.json。"}</p>
          )}
          <div className="bili-qr-hint">
            <p>用 B站 App 扫描这个二维码，并在手机上确认登录。</p>
            {pollMessage && <p className="bili-qr-state">{pollMessage}</p>}
          </div>
        </div>
      )}

      {status?.blockers.length ? (
        <ul className="bili-problems">
          {status.blockers.map((blocker) => (
            <li key={blocker.message}>
              <AlertTriangle size={15} />
              <span>
                {blocker.message}
                {blocker.advice ? ` —— ${blocker.advice}` : ""}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
