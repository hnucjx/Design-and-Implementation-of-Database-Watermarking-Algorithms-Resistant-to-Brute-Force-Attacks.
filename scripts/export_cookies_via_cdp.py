"""Export a YouTube ``cookies.txt`` from a dedicated, automation-owned browser profile.

Why this exists
---------------
``yt-dlp --cookies-from-browser edge`` can no longer read the *real* Edge profile
on this machine:

* modern Edge/Chrome (v20+, "app-bound" encryption) can only be decrypted by the
  running browser process itself -- the key is bound to the browser binary, so any
  offline DPAPI attempt fails with ``Failed to decrypt with DPAPI``;
* Chromium refuses ``--remote-debugging-port`` when ``--user-data-dir`` points at
  the *default* profile directory, so attaching DevTools to the real profile is
  also off the table;
* and pointing CDP at the real profile is actively dangerous -- it can wipe the
  live cookie store.

This script therefore never touches the real profile.  It launches a *second*
browser instance with its own ``--user-data-dir`` (Chromium happily allows CDP
there), lets the user log in once, reads the cookies over CDP, and writes them in
Netscape format for yt-dlp.  The profile directory is kept, so every later refresh
is fully unattended -- only the very first login needs a human.

Usage::

    python scripts/export_cookies_via_cdp.py                  # wait 5 min for login
    python scripts/export_cookies_via_cdp.py --timeout 600    # wait 10 min
    python scripts/export_cookies_via_cdp.py --check-only     # probe, do not write
    python scripts/export_cookies_via_cdp.py --keep-open      # leave the window up

Exit codes: 0 = cookies written, 2 = not logged in / timed out, 3 = setup failure.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Any
import urllib.error
import urllib.request

from yt_dlp.cookies import YoutubeDLCookieJar

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.browser_cookies import BrowserCookieImporter  # noqa: E402

# ``_cdp_cookie`` / ``_is_youtube_related_cookie`` are pure helpers that happen to
# live on the importer class; reuse them so the exported file is byte-compatible
# with what the app itself writes.
_COOKIE_HELPERS = BrowserCookieImporter()
_cdp_cookie = _COOKIE_HELPERS._cdp_cookie  # noqa: SLF001
_is_youtube_related_cookie = _COOKIE_HELPERS._is_youtube_related_cookie  # noqa: SLF001

DEFAULT_PROFILE_DIR = REPO_ROOT / "data" / "cdp-profile"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "cookies.txt"

# Cookies that only exist on an authenticated session.  SID / __Secure-*PSID are
# issued by Google at login time and are the ones yt-dlp actually needs.
STRONG_AUTH_COOKIES = frozenset({"SID", "__Secure-1PSID", "__Secure-3PSID"})
WEAK_AUTH_COOKIES = frozenset(
    {"HSID", "SSID", "APISID", "SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID", "LOGIN_INFO"}
)

BROWSER_EXECUTABLES = {
    "edge": [
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
        r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe",
    ],
    "chrome": [
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    ],
}

REAL_PROFILE_DIRS = {
    "edge": Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "User Data",
    "chrome": Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data",
}


class SetupError(RuntimeError):
    pass


def resolve_browser(requested: str) -> tuple[str, Path]:
    names = ["edge", "chrome"] if requested == "auto" else [requested]
    for name in names:
        for template in BROWSER_EXECUTABLES[name]:
            expanded = Path(os.path.expandvars(template))
            if expanded.exists():
                return name, expanded
    raise SetupError(f"no supported browser executable found (requested: {requested})")


def assert_not_real_profile(browser: str, profile_dir: Path) -> None:
    """Hard guard: refuse to run CDP against the live browser profile."""
    real = REAL_PROFILE_DIRS.get(browser)
    if not real:
        return
    try:
        if profile_dir.resolve() == real.resolve():
            raise SetupError(
                f"refusing to run against the live {browser} profile at {real}; "
                "use a dedicated --profile-dir"
            )
    except OSError:
        pass
    if profile_dir.resolve().is_relative_to(real.resolve()):
        raise SetupError(f"--profile-dir must not live inside the live {browser} profile")


def free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def launch_browser(
    executable: Path, profile_dir: Path, port: int, url: str, headless: bool = False
) -> subprocess.Popen:
    profile_dir.mkdir(parents=True, exist_ok=True)
    args = [
        str(executable),
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={profile_dir}",
        "--profile-directory=Default",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=msEdgeStartupBoost,msImplicitSignin,msSmartScreenProtection",
    ]
    args += ["--headless=new", "--disable-gpu"] if headless else ["--new-window"]
    args.append(url)
    return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def browser_websocket_url(port: int, process: subprocess.Popen, deadline: float) -> str:
    url = f"http://127.0.0.1:{port}/json/version"
    last_error: Exception | None = None
    while time.time() < deadline:
        if process.poll() is not None:
            raise SetupError(f"browser exited early with code {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                payload = json.loads(response.read().decode("utf-8"))
            websocket_url = payload.get("webSocketDebuggerUrl")
            if websocket_url:
                return str(websocket_url)
        except Exception as exc:  # noqa: BLE001 - probing, retry until deadline
            last_error = exc
        time.sleep(0.3)
    raise SetupError(f"timed out waiting for DevTools endpoint on port {port}: {last_error}")


class CdpSession:
    """Minimal CDP client: send one command, return its result."""

    def __init__(self, websocket_url: str) -> None:
        from websockets.sync.client import connect

        self._websocket = connect(
            websocket_url, open_timeout=10, close_timeout=2, max_size=None, proxy=None
        ).__enter__()
        self._next_id = 0

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        self._websocket.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self._websocket.recv(timeout=20))
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise SetupError(f"CDP {method} failed: {message['error']}")
            return message.get("result") or {}

    def read_all_cookies(self) -> list[dict[str, Any]]:
        """Browser-wide cookie read (``Storage.getCookies`` needs the browser target)."""
        result = self.call("Storage.getCookies")
        cookies = result.get("cookies")
        if not isinstance(cookies, list):
            raise SetupError("DevTools returned an invalid cookies payload")
        return [cookie for cookie in cookies if isinstance(cookie, dict)]

    def close_browser(self) -> None:
        try:
            self.call("Browser.close")
        except Exception:  # noqa: BLE001 - the socket usually dies together with the browser
            pass

    def close(self) -> None:
        try:
            self._websocket.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass


def auth_cookie_names(cookies: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    strong: set[str] = set()
    weak: set[str] = set()
    for cookie in cookies:
        domain = str(cookie.get("domain") or "")
        if not _is_youtube_related_cookie(domain):
            continue
        name = str(cookie.get("name") or "")
        if name in STRONG_AUTH_COOKIES:
            strong.add(name)
        elif name in WEAK_AUTH_COOKIES:
            weak.add(name)
    return strong, weak


def is_logged_in(cookies: list[dict[str, Any]]) -> bool:
    strong, weak = auth_cookie_names(cookies)
    return bool(strong) or len(weak) >= 3


def export_jar(cookies: list[dict[str, Any]], output: Path) -> int:
    jar = YoutubeDLCookieJar(str(output))
    imported = 0
    for value in cookies:
        if not _is_youtube_related_cookie(str(value.get("domain") or "")):
            continue
        cookie = _cdp_cookie(value)
        if cookie is None:
            continue
        jar.set_cookie(cookie)
        imported += 1
    if imported == 0:
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    # ignore_discard: YouTube's SID family are session cookies -- keep them anyway.
    jar.save(str(output), ignore_discard=True, ignore_expires=True)
    return imported


def terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=creationflags,
        )
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--browser", default="auto", choices=["auto", "edge", "chrome"])
    parser.add_argument("--profile-dir", default=str(DEFAULT_PROFILE_DIR))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--timeout", type=float, default=300.0, help="seconds to wait for the user to log in")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--port", type=int, default=0, help="0 = pick a free port")
    parser.add_argument("--url", default="https://www.youtube.com/")
    parser.add_argument("--check-only", action="store_true", help="probe login state but never write cookies.txt")
    parser.add_argument("--keep-open", action="store_true", help="leave the browser window open on exit")
    parser.add_argument("--headless", action="store_true", help="for CI: cannot log in, only validates the plumbing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    profile_dir = Path(args.profile_dir).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()

    try:
        browser, executable = resolve_browser(args.browser)
        assert_not_real_profile(browser, profile_dir)
    except SetupError as exc:
        print(json.dumps({"ok": False, "stage": "setup", "error": str(exc)}, ensure_ascii=False))
        return 3

    port = args.port or free_tcp_port()
    print(
        json.dumps(
            {
                "browser": browser,
                "executable": str(executable),
                "profile_dir": str(profile_dir),
                "output": str(output),
                "port": port,
                "live_profile_untouched": str(REAL_PROFILE_DIRS.get(browser, "")),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    process = launch_browser(executable, profile_dir, port, args.url, headless=args.headless)
    session: CdpSession | None = None
    try:
        websocket_url = browser_websocket_url(port, process, time.time() + 30)
        session = CdpSession(websocket_url)

        deadline = time.time() + args.timeout
        cookies: list[dict[str, Any]] = []
        logged_in = False
        while time.time() < deadline:
            cookies = session.read_all_cookies()
            if is_logged_in(cookies):
                logged_in = True
                break
            if args.headless:
                break
            time.sleep(args.poll_interval)

        strong, weak = auth_cookie_names(cookies)
        summary: dict[str, Any] = {
            "ok": logged_in,
            "logged_in": logged_in,
            "auth_cookies": sorted(strong | weak),
            "total_cookies_seen": len(cookies),
            "profile_dir": str(profile_dir),
        }

        if not logged_in:
            summary["error"] = (
                "no authenticated YouTube session in this profile yet -- "
                "log in inside the opened window and re-run"
            )
            if not args.check_only and output.exists():
                summary["note"] = f"existing {output.name} left untouched"
            print(json.dumps(summary, ensure_ascii=False))
            return 2

        if args.check_only:
            print(json.dumps(summary, ensure_ascii=False))
            return 0

        imported = export_jar(cookies, output)
        summary["imported_cookies"] = imported
        summary["output"] = str(output)
        summary["output_bytes"] = output.stat().st_size if output.exists() else 0
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except SetupError as exc:
        print(json.dumps({"ok": False, "stage": "cdp", "error": str(exc)}, ensure_ascii=False))
        return 3
    finally:
        if session is not None and not args.keep_open:
            session.close_browser()
            time.sleep(1.0)
            session.close()
        if not args.keep_open:
            terminate(process)


if __name__ == "__main__":
    raise SystemExit(main())
