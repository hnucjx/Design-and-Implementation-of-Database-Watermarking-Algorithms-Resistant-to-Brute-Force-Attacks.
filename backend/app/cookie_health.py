"""cookies 体检：回答「我这个 cookies.txt 到底有没有用」。

这是本项目最贵的一个坑：**一个看起来完全正常的 cookies.txt 可能是废的**。
两个真实成因（都在真机验收里实测到过）：

1. **域名错了** —— 导出到 9 个鉴权 cookie 全在 ``.google.com``。``.google.com`` 的
   cookie 永远不会发给 ``www.youtube.com``，于是文件里有 ``SID``、
   ``yt-dlp`` 也不报错，但每个请求都是匿名的（实测页面里 ``"LOGGED_IN":false``）。
2. **只导出了匿名 cookie** —— 有 ``VISITOR_INFO1_LIVE`` / ``YSC`` / ``PREF``，
   数量看着不少，一条鉴权项都没有。

所以这里的结论不能只看「cookie 条数」，必须分三步：**格式 → 域名 → 鉴权项**，
最后再（可选地）真的拿它访问一次 ``https://www.youtube.com/`` 看 ``LOGGED_IN``。
"""

from dataclasses import dataclass, field, replace
import http.cookiejar
from pathlib import Path
import time
import urllib.request

from .connectivity import build_opener
from .proxy import ProxyResolution


YOUTUBE_DOMAIN_SUFFIXES = ("youtube.com", "google.com")
# 真正让请求变成「已登录」的那些名字。缺一个不致命（例如只有 SID 也能用），
# 所以报告里区分「命中」与「缺失」，而不是一刀切说无效。
AUTH_COOKIE_NAMES = (
    "SID",
    "HSID",
    "SSID",
    "APISID",
    "SAPISID",
    "LOGIN_INFO",
    "__Secure-1PSID",
    "__Secure-3PSID",
    "__Secure-1PAPISID",
    "__Secure-3PAPISID",
    "__Secure-1PSIDTS",
    "__Secure-3PSIDTS",
)
# 只有这些 = 完全匿名。
ANONYMOUS_ONLY_NAMES = frozenset({"VISITOR_INFO1_LIVE", "YSC", "PREF", "GPS", "SOCS", "VISITOR_PRIVACY_METADATA"})

LOGGED_IN_URL = "https://www.youtube.com/"
LOGGED_IN_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class CookieHealth:
    present: bool
    path: str
    filename: str | None = None
    size_bytes: int = 0
    format_ok: bool = False
    format_note: str = ""
    cookie_count: int = 0
    domains: dict[str, int] = field(default_factory=dict)
    youtube_domain_count: int = 0
    auth_cookie_names: list[str] = field(default_factory=list)
    missing_auth_cookie_names: list[str] = field(default_factory=list)
    anonymous_only: bool = False
    expired_count: int = 0
    verdict: str = ""
    next_steps: list[str] = field(default_factory=list)
    logged_in: bool | None = None
    logged_in_detail: str | None = None
    checked_at: float = 0.0

    def to_detail(self) -> dict[str, object]:
        return {
            "present": self.present,
            "path": self.path,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "format_ok": self.format_ok,
            "format_note": self.format_note,
            "cookie_count": self.cookie_count,
            "domains": self.domains,
            "youtube_domain_count": self.youtube_domain_count,
            "auth_cookie_names": self.auth_cookie_names,
            "missing_auth_cookie_names": self.missing_auth_cookie_names,
            "anonymous_only": self.anonymous_only,
            "expired_count": self.expired_count,
            "verdict": self.verdict,
            "next_steps": self.next_steps,
            "logged_in": self.logged_in,
            "logged_in_detail": self.logged_in_detail,
            "checked_at": self.checked_at,
        }


def inspect_cookies_file(path: Path) -> CookieHealth:
    """纯离线体检：格式、域名、鉴权项、过期。不联网。"""
    checked_at = time.time()
    if not path.exists():
        return CookieHealth(
            present=False,
            path=str(path),
            filename=path.name,
            verdict="没有 cookies 文件",
            next_steps=[
                "在「解析链接」面板点「从浏览器导入」，或运行 python scripts/export_cookies_via_cdp.py",
                "也可以从浏览器扩展导出 Netscape 格式后另存为 data/cookies.txt",
            ],
            checked_at=checked_at,
        )

    text = path.read_text(encoding="utf-8", errors="replace")
    domains: dict[str, int] = {}
    names: set[str] = set()
    expired = 0
    malformed = 0
    now = time.time()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split("\t")
        if len(parts) < 7:
            malformed += 1
            continue
        domain = parts[0].lower().lstrip(".")
        domains[domain] = domains.get(domain, 0) + 1
        names.add(parts[5])
        try:
            expiry = int(parts[4])
        except ValueError:
            expiry = 0
        if expiry and expiry < now:
            expired += 1

    cookie_count = sum(domains.values())
    youtube_domain_count = sum(
        count for domain, count in domains.items() if domain == "youtube.com" or domain.endswith(".youtube.com")
    )
    auth_names = sorted(names.intersection(AUTH_COOKIE_NAMES))
    missing = sorted(set(AUTH_COOKIE_NAMES) - names)
    format_ok = cookie_count > 0 and malformed == 0
    format_note = (
        "Netscape 格式（Tab 分隔 7 列）"
        if format_ok
        else ("文件里没有可解析的 cookie 行，可能导出成了 JSON 或 HTML" if cookie_count == 0 else f"有 {malformed} 行不是 7 列，格式可能被改过")
    )

    health = CookieHealth(
        present=True,
        path=str(path),
        filename=path.name,
        size_bytes=path.stat().st_size,
        format_ok=format_ok,
        format_note=format_note,
        cookie_count=cookie_count,
        domains=domains,
        youtube_domain_count=youtube_domain_count,
        auth_cookie_names=auth_names,
        missing_auth_cookie_names=missing,
        anonymous_only=bool(names) and not auth_names,
        expired_count=expired,
        checked_at=checked_at,
    )
    verdict, steps = _offline_verdict(health)
    return replace(health, verdict=verdict, next_steps=steps)


def _offline_verdict(health: CookieHealth) -> tuple[str, list[str]]:
    if health.cookie_count == 0:
        return (
            "文件里没有任何 cookie 行：导出格式不对",
            ["确认导出的是 Netscape 格式（Tab 分隔、7 列），而不是 JSON/HTML"],
        )
    if health.youtube_domain_count == 0:
        return (
            "cookie 全部落在非 youtube.com 域上 —— 对 www.youtube.com 一律无效",
            [
                "这是「只导出到 .google.com」的典型症状：SID 虽然存在，但 .google.com 的 cookie 不会发给 youtube.com",
                "用 python scripts/export_cookies_via_cdp.py 重新导出（它会校验域名，拿不到就拒绝写文件）",
                "导出窗口必须是有头窗口：无头模式下 YouTube 不会签发 .youtube.com 的鉴权 cookie",
            ],
        )
    if not health.auth_cookie_names:
        return (
            "只有匿名 cookie（缺 SID/HSID/SSID/SAPISID 等鉴权项）",
            ["在导出窗口里真正登录 Google 账号后再导出一次", "登录后在 youtube.com 首页停留几秒，等鉴权 cookie 落地"],
        )
    if health.expired_count == health.cookie_count:
        return (
            "所有 cookie 都已过期",
            ["重新导出 cookies；长期不用的账号登录态会失效"],
        )
    return (
        "包含 youtube.com 域下的鉴权 cookie，格式与域名都正常",
        ["如需确认登录态真的生效，可点「校验 cookies（联网）」检查 LOGGED_IN"],
    )


def probe_logged_in(
    path: Path,
    resolution: ProxyResolution,
    timeout: float = LOGGED_IN_TIMEOUT_SECONDS,
) -> tuple[bool | None, str]:
    """把 cookie jar 发到 ``https://www.youtube.com/``，看返回页面里的 ``LOGGED_IN``。

    这是唯一能证明「这份 cookies 真的能登录」的办法：只看文件内容无法区分
    「登录态有效」与「登录态已失效但 cookie 还在」。
    """
    jar = http.cookiejar.MozillaCookieJar(str(path))
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except Exception as exc:  # noqa: BLE001
        return None, f"无法读取 cookie 文件：{type(exc).__name__}: {exc}"

    opener = build_opener(resolution)
    opener.add_handler(urllib.request.HTTPCookieProcessor(jar))
    try:
        request = urllib.request.Request(
            LOGGED_IN_URL,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) cascade-cookie-check/1.0"},
        )
        with opener.open(request, timeout=timeout) as response:
            page = response.read(400_000).decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return None, f"联网校验失败：{type(exc).__name__}: {exc}"

    if '"LOGGED_IN":true' in page:
        return True, "YouTube 返回的页面里出现了 \"LOGGED_IN\":true"
    if '"LOGGED_IN":false' in page:
        return False, "YouTube 返回的页面里是 \"LOGGED_IN\":false —— 请求被当成匿名"
    return None, "返回页面里既没有 LOGGED_IN:true 也没有 :false（可能被拦了或页面结构变了）"


def verify_cookies(
    path: Path,
    resolution: ProxyResolution,
    *,
    deep: bool = True,
    timeout: float = LOGGED_IN_TIMEOUT_SECONDS,
) -> CookieHealth:
    """离线体检 + （可选）联网校验，并把两者合成一句结论。"""
    health = inspect_cookies_file(path)
    if not health.present or not deep:
        return health

    logged_in, detail = probe_logged_in(path, resolution, timeout=timeout)
    steps = list(health.next_steps)
    if logged_in is True:
        verdict = "登录态有效：YouTube 已识别为已登录"
    elif logged_in is False and health.youtube_domain_count == 0:
        verdict = "登录态无效：cookie 没有落在 youtube.com 域上，所以请求被当成匿名"
    elif logged_in is False:
        verdict = "登录态无效：cookie 在 youtube.com 域上，但 YouTube 仍认为你是匿名（多为登录态过期）"
        steps = ["重新导出 cookies：登录态过期是最常见原因", "确认导出后浏览器没有退出登录该账号"]
    else:
        verdict = f"登录态未知：{detail}"
    return replace(health, verdict=verdict, next_steps=steps, logged_in=logged_in, logged_in_detail=detail)
