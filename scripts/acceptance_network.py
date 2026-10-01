"""Network acceptance for the two things users get stuck on: **proxy** and **cookies**.

Why this script exists
----------------------
``scripts/acceptance_real.py`` measures throughput/concurrency. It deliberately
injects ``HTTP_PROXY``/``HTTPS_PROXY`` into the process environment, which is
exactly the mechanism that used to *silently override the Windows system proxy*
(see ``ai/bug-fix/006-proxy-is-not-configurable.md``). So it cannot prove that
the proxy the user configured in the app is the proxy that is actually used.

This script drives the **product code path** instead:

* every probe goes through ``YtDlpService(proxy=...)`` / ``resolve_proxy()`` —
  the same resolution the downloader uses;
* nothing is written into ``os.environ``;
* the result of each probe is printed as raw line-oriented facts, so the log can
  be pasted into an issue without interpretation.

Two layers, on purpose
----------------------
1. **Reachability** — a raw HTTPS GET of ``https://www.youtube.com/robots.txt``
   (a few hundred bytes, no retries). Fast, deterministic, and it isolates
   "can we get a TCP/TLS + HTTP response at all" from anything yt-dlp does.
2. **End-to-end** — a real ``extract_metadata()`` on a real video, with and
   without ``cookies.txt``, plus a ``LOGGED_IN`` probe on the returned page.

The proxy matrix is designed to be *falsifiable*: ``direct`` is expected to
**fail** on a machine where direct egress is blocked. If ``direct`` succeeds the
script says so out loud instead of quietly reporting a green run — the value of
the test is precisely that the proxy is what makes the difference.

The last section is an A/B on the **host environment**: it injects a
``NODE_OPTIONS=--require=<shim>`` (exactly the shape a shell/IDE/desktop shell
leaks in), watches the cookie path fail with ``The page needs to be reloaded.``,
then re-runs after ``runtime_env.sanitize_environment()`` to show it pass. That
is the only way to prove the fix without relying on a machine that happens to be
broken.

Usage::

    python scripts/acceptance_network.py
    python scripts/acceptance_network.py --video https://www.youtube.com/watch?v=aqz-KE-bpKQ
    python scripts/acceptance_network.py --cookies data/cookies.txt --timeout 12
    python scripts/acceptance_network.py --skip-end-to-end      # fast, reachability only

Exit code is 0 when the proxy matrix behaved as the machine's own configuration
implies, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.proxy import (  # noqa: E402  (path bootstrap must run first)
    PROXY_SOURCE_NONE,
    redact_proxy_credentials,
    resolve_proxy,
)
from app.runtime_env import describe_environment_risks, sanitize_environment  # noqa: E402

PROBE_URL = "https://www.youtube.com/robots.txt"
LOGGED_IN_URL = "https://www.youtube.com/"
DEFAULT_VIDEO = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


def _yt_dlp_version() -> str:
    try:
        from yt_dlp.version import __version__

        return __version__
    except Exception:  # noqa: BLE001 - 只是报告信息，拿不到就不显示
        return "unknown"

# YouTube's own auth cookies are the ones that make a jar worth anything; a jar
# with only VISITOR_INFO1_LIVE / YSC looks fine and is still anonymous.
YOUTUBE_AUTH_COOKIE_NAMES = (
    "SID",
    "HSID",
    "SSID",
    "APISID",
    "SAPISID",
    "LOGIN_INFO",
    "__Secure-1PSID",
    "__Secure-3PSID",
)


@dataclass
class Probe:
    """One line of evidence. Everything here ends up in the printed report."""

    name: str
    proxy_setting: str | None = None
    resolved_source: str = ""
    resolved_url: str | None = None
    writes_ydl_option: bool = False
    ok: bool = False
    elapsed_s: float = 0.0
    http_status: int | None = None
    detail: str = ""
    error: str = ""

    def to_row(self) -> dict:
        return asdict(self)


@dataclass
class CookieReport:
    path: str = ""
    exists: bool = False
    bytes: int = 0
    cookie_count: int = 0
    domains: dict[str, int] = field(default_factory=dict)
    youtube_domain_count: int = 0
    auth_cookie_names: list[str] = field(default_factory=list)
    missing_auth_cookie_names: list[str] = field(default_factory=list)
    verdict: str = ""
    next_step: str = ""

    def to_row(self) -> dict:
        return asdict(self)


def _opener_for(proxy_url: str | None):
    """Build an opener that uses exactly ``proxy_url`` (``None``/``""`` = direct)."""
    handlers: list[urllib.request.BaseHandler] = []
    if proxy_url:
        handlers.append(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    else:
        # An empty ProxyHandler is what disables proxy use entirely, including any
        # inherited *_proxy environment variables.
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


def probe_reachability(name: str, setting: str | None, timeout: float) -> Probe:
    resolution = resolve_proxy(setting)
    probe = Probe(
        name=name,
        proxy_setting=setting,
        resolved_source=resolution.source,
        resolved_url=resolution.url,
        writes_ydl_option=resolution.writes_ydl_option,
    )
    started = time.perf_counter()
    try:
        request = urllib.request.Request(PROBE_URL, headers={"User-Agent": "cascade-acceptance/1.0"})
        with _opener_for(resolution.url).open(request, timeout=timeout) as response:
            body = response.read(4096)
        probe.http_status = getattr(response, "status", None)
        probe.ok = probe.http_status == 200 and len(body) > 0
        probe.detail = f"{len(body)} bytes from {PROBE_URL}"
    except urllib.error.HTTPError as exc:  # server answered, but not 200
        probe.http_status = exc.code
        probe.error = f"HTTP {exc.code} {exc.reason}"
    except Exception as exc:  # noqa: BLE001 - the point is to record whatever happened
        probe.error = f"{type(exc).__name__}: {exc}"
    finally:
        probe.elapsed_s = round(time.perf_counter() - started, 2)
    return probe


def probe_logged_in(cookies_path: Path, setting: str | None, timeout: float) -> tuple[bool | None, str]:
    """Send the jar to youtube.com and look for ``"LOGGED_IN":true``.

    A ``.google.com``-only jar yields ``false`` even though it contains ``SID`` —
    ``.google.com`` cookies are never sent to ``www.youtube.com``. That is why the
    caller must also look at the cookie domains, not just the count.
    """
    import http.cookiejar

    jar = http.cookiejar.MozillaCookieJar(str(cookies_path))
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except Exception as exc:  # noqa: BLE001
        return None, f"could not load cookie jar: {type(exc).__name__}: {exc}"

    resolution = resolve_proxy(setting)
    handlers: list[urllib.request.BaseHandler] = [urllib.request.HTTPCookieProcessor(jar)]
    if resolution.url:
        handlers.append(urllib.request.ProxyHandler({"http": resolution.url, "https": resolution.url}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)
    try:
        request = urllib.request.Request(
            LOGGED_IN_URL,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) cascade-acceptance/1.0"},
        )
        with opener.open(request, timeout=timeout) as response:
            page = response.read(400_000).decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"

    if '"LOGGED_IN":true' in page:
        return True, "page contains \"LOGGED_IN\":true"
    if '"LOGGED_IN":false' in page:
        return False, "page contains \"LOGGED_IN\":false"
    return None, "neither LOGGED_IN:true nor :false found in the returned page"


def inspect_cookies(cookies_path: Path) -> CookieReport:
    report = CookieReport(path=str(cookies_path))
    if not cookies_path.exists():
        report.verdict = "没有 cookies 文件"
        report.next_step = (
            "运行 python scripts/export_cookies_via_cdp.py 导出；"
            "或从浏览器扩展导出后另存为 data/cookies.txt。"
        )
        return report

    report.exists = True
    report.bytes = cookies_path.stat().st_size
    text = cookies_path.read_text(encoding="utf-8", errors="replace")
    domains: dict[str, int] = {}
    names: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split("\t")
        if len(parts) < 7:
            continue
        domain = parts[0].lower().lstrip(".")
        domains[domain] = domains.get(domain, 0) + 1
        names.add(parts[5])
        report.cookie_count += 1

    report.domains = domains
    report.youtube_domain_count = sum(
        count for domain, count in domains.items() if domain == "youtube.com" or domain.endswith(".youtube.com")
    )
    report.auth_cookie_names = sorted(names.intersection(YOUTUBE_AUTH_COOKIE_NAMES))
    report.missing_auth_cookie_names = sorted(set(YOUTUBE_AUTH_COOKIE_NAMES) - names)

    if report.cookie_count == 0:
        report.verdict = "文件里没有任何 cookie 行"
        report.next_step = "确认导出的是 Netscape 格式（Tab 分隔的 7 列），而不是 JSON。"
    elif report.youtube_domain_count == 0:
        report.verdict = "cookie 全部落在非 youtube.com 域上 —— 对 www.youtube.com 一律无效"
        report.next_step = (
            "这是「.google.com 域」的典型症状：必须用有头窗口登录 youtube.com 后重新导出，"
            "只导出 .google.com 的 SID 是没用的。"
        )
    elif not report.auth_cookie_names:
        report.verdict = "只有匿名 cookie（缺 SID/HSID/SSID/SAPISID 等鉴权项）"
        report.next_step = "在导出窗口里真正登录 Google 账号后再导出一次。"
    else:
        report.verdict = "包含 youtube.com 域下的鉴权 cookie，可用于鉴权请求"
        report.next_step = ""
    return report


def run_proxy_matrix(timeout: float, explicit_proxy: str | None) -> list[Probe]:
    auto = resolve_proxy(None)
    probes = [
        probe_reachability("A. 自动（系统代理优先）", None, timeout),
        probe_reachability("B. 强制直连", "direct", timeout),
    ]
    candidate = explicit_proxy or auto.system_proxy or auto.environment_proxy
    if candidate:
        probes.append(probe_reachability(f"C. 显式设置 {candidate}", candidate, timeout))
    probes.append(probe_reachability("D. 指向不存在的代理（错误路径）", "http://127.0.0.1:1", timeout))
    return probes


def run_end_to_end(
    video: str,
    cookies_path: Path,
    explicit_proxy: str | None,
    timeout: float,
) -> dict:
    """The real product path: YtDlpService.extract_metadata with/without cookies."""
    from app.ytdlp_service import YtDlpService

    setting = explicit_proxy  # None = auto, same as leaving the field empty in the UI
    resolution = resolve_proxy(setting)
    service = YtDlpService(download_dir=REPO_ROOT / "tmp_acceptance" / "network", proxy=setting)
    result: dict = {
        "video": video,
        "proxy_setting": setting,
        "resolved_source": resolution.source,
        "resolved_url": redact_proxy_credentials(resolution.url),
        "with_cookies": None,
        "without_cookies": None,
        "login_with_cookies": None,
        "login_without_cookies": None,
        "notes": [],
    }

    for label, path in (("with_cookies", cookies_path if cookies_path.exists() else None), ("without_cookies", None)):
        started = time.perf_counter()
        entry = {"cookies": str(path) if path else None}
        try:
            analysis = service.extract_metadata(video, cookies_path=path)
            entry["ok"] = True
            entry["title"] = analysis.title
            entry["duration_s"] = analysis.duration
            entry["formats"] = len(analysis.formats)
        except Exception as exc:  # noqa: BLE001
            entry["ok"] = False
            entry["error"] = f"{type(exc).__name__}: {exc}"
        entry["elapsed_s"] = round(time.perf_counter() - started, 2)
        result[label] = entry

    if cookies_path.exists():
        logged_in, detail = probe_logged_in(cookies_path, setting, timeout)
        result["login_with_cookies"] = {"logged_in": logged_in, "detail": detail}
    # Anonymous run: an empty jar, built in a throwaway file so data/ is never touched.
    empty_jar = REPO_ROOT / "tmp_acceptance" / "network" / "empty-cookies.txt"
    empty_jar.parent.mkdir(parents=True, exist_ok=True)
    empty_jar.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    logged_in_anon, detail_anon = probe_logged_in(empty_jar, setting, timeout)
    result["login_without_cookies"] = {"logged_in": logged_in_anon, "detail": detail_anon}
    return result


def run_environment_experiment(video: str, cookies_path: Path, explicit_proxy: str | None, timeout: float) -> dict:
    """E. 宿主环境净化 A/B：证明「带 cookies 反而失败」的根因与修复。

    步骤刻意做得可复现：写一个空的 ``.cjs``（内容无关紧要，重点是它会被 ``--require``
    加载），把它塞进 ``NODE_OPTIONS``，再跑一次带 cookies 的提取。预期是失败且报出
    ``The page needs to be reloaded.``；随后调用产品里那个 ``sanitize_environment()``
    再跑一次，预期成功。
    """
    from app.ytdlp_service import YtDlpService

    workdir = REPO_ROOT / "tmp_acceptance" / "network"
    workdir.mkdir(parents=True, exist_ok=True)
    shim = workdir / "fake-node-options-shim.cjs"
    shim.write_text("// intentionally empty: only the act of loading it matters\n", encoding="utf-8")

    original = os.environ.get("NODE_OPTIONS")
    result: dict = {"shim": str(shim), "original_node_options": original, "before_fix": None, "after_fix": None}

    def extract() -> dict:
        service = YtDlpService(download_dir=workdir, proxy=explicit_proxy)
        entry: dict = {}
        started = time.perf_counter()
        try:
            analysis = service.extract_metadata(video, cookies_path=cookies_path if cookies_path.exists() else None)
            entry["ok"] = True
            entry["title"] = analysis.title
        except Exception as exc:  # noqa: BLE001
            entry["ok"] = False
            entry["error"] = f"{type(exc).__name__}: {exc}"
        entry["elapsed_s"] = round(time.perf_counter() - started, 2)
        entry["js_runtime"] = service.get_dependency_status().get("js_runtime")
        entry["js_runtime_error"] = service.get_dependency_status().get("js_runtime_error")
        return entry

    try:
        os.environ["NODE_OPTIONS"] = f'--require="{shim}"'
        result["before_fix"] = extract()
        result["sanitized"] = [item.to_detail() for item in sanitize_environment()]
        result["after_fix"] = extract()
    finally:
        if original is None:
            os.environ.pop("NODE_OPTIONS", None)
        else:
            os.environ["NODE_OPTIONS"] = original
    return result


def verdict_for(probes: list[Probe], cookie_report: CookieReport, end_to_end: dict | None, environment: dict | None) -> tuple[bool, list[str]]:
    by_name = {p.name: p for p in probes}
    notes: list[str] = []
    ok = True

    auto = next(p for p in probes if p.name.startswith("A."))
    if not auto.ok:
        ok = False
        notes.append(
            "自动（系统代理）探测失败 —— 应用当前无法访问 YouTube。"
            "请检查代理软件是否在运行，或在设置里显式填写代理地址。"
        )
    else:
        notes.append(f"自动（系统代理）探测成功，来源={auto.resolved_source}。")

    direct = next(p for p in probes if p.name.startswith("B."))
    if direct.ok:
        notes.append(
            "注意：强制直连也能成功访问。本机存在可用的直连出口，"
            "因此本脚本无法用「直连失败」来反证代理确实生效；判断以 A/C 的耗时与来源字段为准。"
        )
    else:
        notes.append(
            "强制直连按预期失败 —— 这反证了 A/C 的成功确实来自代理配置，而不是巧合走通了直连。"
        )

    dead = next(p for p in probes if p.name.startswith("D."))
    if dead.ok:
        ok = False
        notes.append("异常：指向 127.0.0.1:1 的错误代理居然成功，说明代理参数没有被真正应用。")
    else:
        notes.append(f"错误代理按预期失败（{dead.error[:80]}），说明代理参数确实被应用。")

    for probe in probes:
        if probe.resolved_source == PROXY_SOURCE_NONE and probe.name.startswith("A."):
            notes.append("未发现任何代理来源（system/environment 均为空），A 的成败即直连的成败。")

    if end_to_end is not None:
        with_cookies = end_to_end.get("with_cookies") or {}
        without = end_to_end.get("without_cookies") or {}
        login = (end_to_end.get("login_with_cookies") or {}).get("logged_in")
        if not without.get("ok"):
            ok = False
            notes.append("端到端失败：**不带** cookies 的解析就没成功，先解决代理/网络再谈其它。")
        elif login is True and not with_cookies.get("ok"):
            ok = False
            notes.append(
                "端到端失败：cookies 的登录态有效（LOGGED_IN:true），但**带 cookies 的解析失败了**。"
                f" 原始错误={with_cookies.get('error')}"
            )
        elif login is True and with_cookies.get("ok"):
            notes.append("端到端通过：带 cookies 与不带 cookies 都能解析，且登录态有效。")
        else:
            notes.append(f"端到端：不带 cookies 能解析，登录态未确认（logged_in={login}）。")

    if environment is not None:
        before = environment.get("before_fix") or {}
        after = environment.get("after_fix") or {}
        if after.get("ok") and not before.get("ok"):
            notes.append(
                "环境净化 A/B 通过：注入 NODE_OPTIONS=--require 后带 cookies 解析失败，"
                "摘除后成功 —— 根因与修复都被复现了。"
            )
        elif after.get("ok") and before.get("ok"):
            notes.append("环境净化 A/B：注入后仍然成功，本机上这条路径没有复现，说明该变量在本机未被 JS 运行时读取。")
        else:
            ok = False
            notes.append(
                f"环境净化 A/B 失败：摘除后依然不成功（before={before.get('error')!r} / after={after.get('error')!r}）。"
            )
    return ok, notes


def print_report(
    probes: list[Probe],
    cookie_report: CookieReport,
    end_to_end: dict | None,
    environment: dict | None,
    notes: list[str],
    passed: bool,
    removed: list[dict[str, str]],
) -> None:
    print("\n================ 启动环境快照 ================")
    if removed:
        for item in removed:
            print(f"已摘除 {item['name']}：{item['value']}")
            print(f"  原因：{item['reason']}")
    else:
        print("没有发现需要摘除的宿主环境变量。")
    remaining = describe_environment_risks()
    print(f"仍存在的环境风险：{remaining if remaining else '无'}")

    print("\n================ 代理探测矩阵 ================")
    for probe in probes:
        status = "OK  " if probe.ok else "FAIL"
        print(
            f"[{status}] {probe.name}\n"
            f"        setting={probe.proxy_setting!r} source={probe.resolved_source} "
            f"proxy={redact_proxy_credentials(probe.resolved_url) or '<direct>'}\n"
            f"        writes_ydl_option={probe.writes_ydl_option} "
            f"http_status={probe.http_status} elapsed={probe.elapsed_s}s"
        )
        if probe.detail:
            print(f"        detail={probe.detail}")
        if probe.error:
            print(f"        error={probe.error}")

    print("\n================ cookies 体检 ================")
    print(f"path={cookie_report.path}")
    print(f"exists={cookie_report.exists} bytes={cookie_report.bytes} cookies={cookie_report.cookie_count}")
    print(f"domains={cookie_report.domains}")
    print(f"youtube.com 域上的 cookie 数={cookie_report.youtube_domain_count}")
    print(f"命中的鉴权 cookie={cookie_report.auth_cookie_names}")
    print(f"缺失的鉴权 cookie={cookie_report.missing_auth_cookie_names}")
    print(f"结论：{cookie_report.verdict}")
    if cookie_report.next_step:
        print(f"下一步：{cookie_report.next_step}")

    if end_to_end:
        print("\n================ 端到端（真实 yt-dlp）================")
        print(f"video={end_to_end['video']}")
        print(
            f"proxy setting={end_to_end['proxy_setting']!r} source={end_to_end['resolved_source']} "
            f"proxy={end_to_end['resolved_url'] or '<direct>'}"
        )
        for label in ("with_cookies", "without_cookies"):
            entry = end_to_end.get(label) or {}
            print(
                f"  {label:16s} ok={entry.get('ok')} elapsed={entry.get('elapsed_s')}s "
                f"title={entry.get('title')!r} error={entry.get('error', '')}"
            )
        for label in ("login_with_cookies", "login_without_cookies"):
            entry = end_to_end.get(label) or {}
            print(f"  {label:20s} logged_in={entry.get('logged_in')} ({entry.get('detail')})")

    if environment:
        print("\n================ 环境净化 A/B（NODE_OPTIONS）================")
        print(f"注入的 shim={environment['shim']}")
        print(f"原有的 NODE_OPTIONS={environment['original_node_options']!r}")
        before = environment.get("before_fix") or {}
        after = environment.get("after_fix") or {}
        print(f"  注入后 ok={before.get('ok')} elapsed={before.get('elapsed_s')}s error={before.get('error', '')}")
        print(f"          js_runtime={before.get('js_runtime')} error={before.get('js_runtime_error')}")
        print(f"  净化后 ok={after.get('ok')} elapsed={after.get('elapsed_s')}s error={after.get('error', '')}")
        print(f"          js_runtime={after.get('js_runtime')} error={after.get('js_runtime_error')}")
        print(f"  摘除动作={environment.get('sanitized')}")

    print("\n================ 结论 ================")
    for note in notes:
        print(f"- {note}")
    print(f"\n总体：{'PASS' if passed else 'FAIL'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--video", default=DEFAULT_VIDEO)
    parser.add_argument("--cookies", default=str(REPO_ROOT / "data" / "cookies.txt"))
    parser.add_argument("--explicit-proxy", default=None, help="default: the machine's system proxy, if any")
    parser.add_argument("--timeout", type=float, default=15.0, help="per-request timeout in seconds")
    parser.add_argument("--skip-end-to-end", action="store_true", help="reachability matrix only")
    parser.add_argument("--skip-env-experiment", action="store_true", help="skip the NODE_OPTIONS A/B")
    parser.add_argument("--json", default=str(REPO_ROOT / "tmp_acceptance" / "network-acceptance.json"))
    args = parser.parse_args(argv)

    # 与应用启动时同一个动作：先摘掉会打坏 JS 运行时的宿主环境变量，再开始验收。
    removed = [item.to_detail() for item in sanitize_environment()]

    print(f"cwd={os.getcwd()}")
    print(f"probe_url={PROBE_URL} timeout={args.timeout}s")
    print(f"python={sys.executable} yt_dlp={_yt_dlp_version()}")
    print(
        "环境里的 *_proxy（仅供参考，本脚本不会写入）："
        f"HTTPS_PROXY={os.environ.get('HTTPS_PROXY')} NO_PROXY={os.environ.get('NO_PROXY')}"
    )

    probes = run_proxy_matrix(args.timeout, args.explicit_proxy)
    cookie_report = inspect_cookies(Path(args.cookies))
    end_to_end = None
    environment = None
    if not args.skip_end_to_end:
        end_to_end = run_end_to_end(args.video, Path(args.cookies), args.explicit_proxy, args.timeout)
        if not args.skip_env_experiment:
            environment = run_environment_experiment(args.video, Path(args.cookies), args.explicit_proxy, args.timeout)

    passed, notes = verdict_for(probes, cookie_report, end_to_end, environment)
    print_report(probes, cookie_report, end_to_end, environment, notes, passed, removed)

    payload = {
        "sanitized_environment": removed,
        "environment_risks_remaining": describe_environment_risks(),
        "proxy_probes": [p.to_row() for p in probes],
        "cookies": cookie_report.to_row(),
        "end_to_end": end_to_end,
        "environment_experiment": environment,
        "notes": notes,
        "passed": passed,
    }
    json_path = Path(args.json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON: {json_path}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
