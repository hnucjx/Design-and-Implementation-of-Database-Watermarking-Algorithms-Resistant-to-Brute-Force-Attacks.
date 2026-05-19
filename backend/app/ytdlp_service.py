from collections.abc import Callable
from contextlib import suppress
from copy import copy
from dataclasses import dataclass
from http.cookiejar import Cookie
import importlib.metadata
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time
from typing import Any
import json
import urllib.request

import yt_dlp
from yt_dlp.cookies import YoutubeDLCookieJar, extract_cookies_from_browser
from yt_dlp.networking.impersonate import ImpersonateTarget
from yt_dlp.version import __version__ as yt_dlp_version

from .schemas import AnalyzeResponse, DownloadOptions, FormatOption, SubtitleOption, VideoEntry


AUTO_BROWSER_COOKIE_CANDIDATES = ["edge", "chrome", "firefox", "brave", "chromium", "vivaldi", "opera"]
YOUTUBE_COOKIE_DOMAIN_SUFFIXES = ("youtube.com", "google.com")
YTDLP_REQUEST_SLEEP_SECONDS = 1.0
YTDLP_DOWNLOAD_SLEEP_SECONDS = 2.0
YTDLP_MAX_DOWNLOAD_SLEEP_SECONDS = 5.0
YTDLP_SOCKET_TIMEOUT_SECONDS = 30
YTDLP_FRAGMENT_RETRIES = 20
YTDLP_FILE_ACCESS_RETRIES = 5
YTDLP_EXTRACTOR_RETRIES = 5
YTDLP_CONCURRENT_FRAGMENT_DOWNLOADS = 1
DEFAULT_ANTI403_HTTP_CHUNK_SIZE_MB = 16
YOUTUBE_DOWNLOAD_PROFILES = ("default", "mweb_pot_chrome", "safari_hls", "chrome_default")
YOUTUBE_ANTI403_PROFILES = frozenset(YOUTUBE_DOWNLOAD_PROFILES[1:])
YOUTUBE_PROFILE_ALIASES = {"anti403": "safari_hls"}
POT_PROVIDER_DISTRIBUTION = "yt-dlp-getpot-wpc"
POT_PROVIDER_EXTRACTOR = "youtubepot-wpc"
COOKIE_REQUIRED_AUTH_HINTS = (
    "sign in to confirm",
    "confirm you're not a bot",
    "confirm you’re not a bot",
    "not a bot",
    "login required",
    "only available for registered users",
    "confirm your age",
    "age-restricted",
)
MIN_AUTO_FALLBACK_HEIGHT = 720


class DownloadCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class BrowserCookieImportResult:
    browser: str
    imported_count: int
    filename: str


class BrowserCookieImportError(RuntimeError):
    def __init__(self, code: str, browser: str | None, message: str, raw_detail: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.browser = browser
        self.message = message
        self.raw_detail = raw_detail

    @classmethod
    def browser_locked(cls, browser: str, raw_detail: str | None = None) -> "BrowserCookieImportError":
        if browser == "edge":
            message = "Edge 正在运行，cookies 数据库被锁定。请关闭 Edge 后重试，或确认由应用关闭 Edge 并重新导入。"
        else:
            message = f"{browser} 正在运行，cookies 数据库被锁定。请关闭浏览器后重试。"
        return cls("browser_locked", browser, message, raw_detail)

    def to_detail(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "browser": self.browser,
            "message": self.message,
            "raw_detail": self.raw_detail,
        }


class YtDlpService:
    def __init__(
        self,
        download_dir: Path,
        youtube_po_token: str | None = None,
        youtube_visitor_data: str | None = None,
        youtube_po_browser_path: str | None = None,
        anti403_http_chunk_size_mb: int = DEFAULT_ANTI403_HTTP_CHUNK_SIZE_MB,
    ) -> None:
        self.download_dir = download_dir
        self.youtube_po_token = youtube_po_token
        self.youtube_visitor_data = youtube_visitor_data
        self.youtube_po_browser_path = youtube_po_browser_path
        self.anti403_http_chunk_size_mb = max(1, anti403_http_chunk_size_mb)

    def get_ffmpeg_status(self) -> dict[str, bool]:
        return {"ffmpeg": self._ffmpeg_executable() is not None, "ffprobe": shutil.which("ffprobe") is not None}

    def get_dependency_status(self) -> dict[str, bool | str | None | list[str]]:
        ffmpeg = self.get_ffmpeg_status()
        runtime = self._detect_js_runtime()
        impersonation_targets = self._available_impersonation_targets()
        provider_version = self._po_token_provider_version()
        return {
            **ffmpeg,
            "impersonation_available": bool(impersonation_targets),
            "impersonation_targets": impersonation_targets,
            "po_token_provider_available": provider_version is not None,
            "po_token_provider": POT_PROVIDER_DISTRIBUTION if provider_version else None,
            "po_token_provider_version": provider_version,
            "youtube_po_browser_path_configured": bool(self.youtube_po_browser_path),
            "youtube_po_token_configured": bool(self.youtube_po_token),
            "youtube_visitor_data_configured": bool(self.youtube_visitor_data),
            "js_runtime": runtime is not None,
            "js_runtime_name": runtime[0] if runtime else None,
            "js_runtime_version": runtime[2] if runtime else None,
            "yt_dlp_version": yt_dlp_version,
        }

    def import_browser_cookies(
        self,
        browser: str,
        target_path: Path,
        close_browser_if_locked: bool = False,
    ) -> BrowserCookieImportResult:
        candidates = AUTO_BROWSER_COOKIE_CANDIDATES if browser == "auto" else [browser]
        errors: list[str] = []
        locked_error: BrowserCookieImportError | None = None

        for candidate in candidates:
            try:
                imported = self._extract_browser_cookie_jar_with_fallback(candidate, close_browser_if_locked)
            except BrowserCookieImportError as exc:
                if exc.code == "browser_locked":
                    locked_error = exc
                errors.append(f"{candidate}: {exc.raw_detail or exc.message}")
                continue
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")
                continue

            filtered = YoutubeDLCookieJar(target_path)
            imported_count = 0
            for cookie in imported:
                if self._is_youtube_related_cookie(cookie.domain):
                    filtered.set_cookie(copy(cookie))
                    imported_count += 1

            if imported_count == 0:
                errors.append(f"{candidate}: no YouTube or Google cookies found")
                continue

            target_path.parent.mkdir(parents=True, exist_ok=True)
            filtered.save(target_path, ignore_discard=True, ignore_expires=True)
            return BrowserCookieImportResult(
                browser=candidate,
                imported_count=imported_count,
                filename=target_path.name,
            )

        if locked_error:
            raise locked_error
        detail = "; ".join(errors) if errors else "no supported browser candidates were available"
        raise RuntimeError(f"Could not import YouTube cookies from browser: {detail}")

    def _extract_browser_cookie_jar(self, browser: str) -> YoutubeDLCookieJar:
        return extract_cookies_from_browser(browser)

    def _extract_browser_cookie_jar_with_fallback(
        self,
        browser: str,
        close_browser_if_locked: bool,
    ) -> YoutubeDLCookieJar:
        try:
            return self._extract_browser_cookie_jar(browser)
        except Exception as exc:
            if self._is_browser_cookie_database_locked(browser, exc):
                if not (close_browser_if_locked and browser == "edge"):
                    raise BrowserCookieImportError.browser_locked(browser, str(exc)) from exc
                self._close_browser_for_cookie_import(browser)
                try:
                    return self._extract_browser_cookie_jar(browser)
                except Exception as retry_exc:
                    if self._is_browser_cookie_database_locked(browser, retry_exc):
                        raise BrowserCookieImportError.browser_locked(browser, str(retry_exc)) from retry_exc
                    if self._is_edge_dpapi_decrypt_error(browser, retry_exc):
                        return self._extract_edge_cookies_via_cdp()
                    raise
            if self._is_edge_dpapi_decrypt_error(browser, exc):
                return self._extract_edge_cookies_via_cdp()
            raise

    def _close_browser_for_cookie_import(self, browser: str) -> None:
        if browser != "edge":
            return
        if os.name != "nt":
            raise BrowserCookieImportError.browser_locked(browser, "Automatic Edge closing is only supported on Windows.")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(
            ["taskkill", "/IM", "msedge.exe", "/F", "/T"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=creationflags,
        )
        time.sleep(1.0)

    def _is_browser_cookie_database_locked(self, browser: str, exc: Exception) -> bool:
        return browser == "edge" and "could not copy chrome cookie database" in str(exc).lower()

    def _is_edge_dpapi_decrypt_error(self, browser: str, exc: Exception) -> bool:
        return browser == "edge" and "failed to decrypt with dpapi" in str(exc).lower()

    def _extract_edge_cookies_via_cdp(self) -> YoutubeDLCookieJar:
        edge = self._edge_executable()
        if not edge:
            raise RuntimeError("Microsoft Edge executable was not found.")
        port = self._free_tcp_port()
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            [
                edge,
                f"--remote-debugging-port={port}",
                "--remote-allow-origins=*",
                f"--user-data-dir={self._edge_user_data_dir()}",
                "--profile-directory=Default",
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--disable-default-apps",
                "https://www.youtube.com/",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        try:
            websocket_url = self._wait_for_cdp_websocket_url(port)
            cookies = self._read_cdp_cookies(websocket_url)
        finally:
            self._terminate_edge_process(process)

        jar = YoutubeDLCookieJar()
        for value in cookies:
            cookie = self._cdp_cookie(value)
            if cookie:
                jar.set_cookie(cookie)
        return jar

    def _terminate_edge_process(self, process: Any) -> None:
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            with suppress(Exception):
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/F", "/T"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    creationflags=creationflags,
                )
            return
        with suppress(Exception):
            process.terminate()
            process.wait(timeout=5)
        with suppress(Exception):
            process.kill()

    def _edge_executable(self) -> str | None:
        candidates = [
            shutil.which("msedge"),
            str(Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
            str(Path(os.environ.get("ProgramFiles", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists():
                return candidate
        return None

    def _edge_user_data_dir(self) -> Path:
        return Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "User Data"

    def _free_tcp_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _wait_for_cdp_websocket_url(self, port: int) -> str:
        url = f"http://127.0.0.1:{port}/json/list"
        deadline = time.time() + 15
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, list):
                    raise RuntimeError("Edge DevTools returned an invalid target list.")
                pages = [target for target in payload if isinstance(target, dict) and target.get("type") == "page"]
                target = next((page for page in pages if "youtube.com" in str(page.get("url", ""))), None)
                target = target or (pages[0] if pages else None)
                websocket_url = target.get("webSocketDebuggerUrl") if target else None
                if websocket_url:
                    return str(websocket_url)
            except Exception as exc:
                last_error = exc
                time.sleep(0.2)
        raise RuntimeError(f"Timed out waiting for Edge DevTools endpoint: {last_error}")

    def _read_cdp_cookies(self, websocket_url: str) -> list[dict[str, Any]]:
        from websockets.sync.client import connect

        with connect(websocket_url, open_timeout=5, close_timeout=2, max_size=None) as websocket:
            websocket.send(
                json.dumps(
                    {
                        "id": 1,
                        "method": "Network.getCookies",
                        "params": {
                            "urls": [
                                "https://www.youtube.com/",
                                "https://youtube.com/",
                                "https://accounts.google.com/",
                                "https://www.google.com/",
                            ]
                        },
                    }
                )
            )
            deadline = time.time() + 10
            while time.time() < deadline:
                message = json.loads(websocket.recv(timeout=10))
                if message.get("id") != 1:
                    continue
                result = message.get("result") or {}
                cookies = result.get("cookies") or []
                if not isinstance(cookies, list):
                    raise RuntimeError("Edge DevTools returned an invalid cookies payload.")
                return [cookie for cookie in cookies if isinstance(cookie, dict)]
        raise RuntimeError("Timed out reading cookies from Edge DevTools.")

    def _cdp_cookie(self, value: dict[str, Any]) -> Cookie | None:
        name = value.get("name")
        cookie_value = value.get("value")
        domain = value.get("domain")
        if not name or cookie_value is None or not domain:
            return None
        expires = value.get("expires")
        parsed_expires = int(expires) if isinstance(expires, (int, float)) and expires > 0 else None
        path = str(value.get("path") or "/")
        return Cookie(
            version=0,
            name=str(name),
            value=str(cookie_value),
            port=None,
            port_specified=False,
            domain=str(domain),
            domain_specified=True,
            domain_initial_dot=str(domain).startswith("."),
            path=path,
            path_specified=True,
            secure=bool(value.get("secure")),
            expires=parsed_expires,
            discard=parsed_expires is None,
            comment=None,
            comment_url=None,
            rest={"HttpOnly": None} if value.get("httpOnly") else {},
            rfc2109=False,
        )

    def extract_metadata(self, url: str, cookies_path: Path | None = None) -> AnalyzeResponse:
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "ignoreconfig": True,
            "ignoreerrors": False,
            "extract_flat": "in_playlist",
            "skip_download": True,
            "color": "no_color",
            "sleep_interval_requests": YTDLP_REQUEST_SLEEP_SECONDS,
        }
        opts.update(self._javascript_runtime_options())
        if cookies_path:
            opts["cookiefile"] = str(cookies_path)

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            raise ValueError("Unable to extract metadata for this URL.")

        entries = self._map_entries(info)
        is_playlist = bool(entries) or info.get("_type") == "playlist"
        title = info.get("title") or "Untitled"
        return AnalyzeResponse(
            url=info.get("webpage_url") or url,
            title=title,
            is_playlist=is_playlist,
            duration=info.get("duration"),
            thumbnail=info.get("thumbnail"),
            entries=entries,
            formats=self._map_formats(info.get("formats") or []),
            subtitles=self._map_subtitles(info.get("subtitles") or {}),
            automatic_subtitles=self._map_subtitles(info.get("automatic_captions") or {}),
            ffmpeg=self.get_ffmpeg_status(),
        )

    def build_download_options(
        self,
        options: DownloadOptions,
        cookies_path: Path | None,
        download_dir: Path | None = None,
        youtube_profile: str = "default",
    ) -> dict[str, Any]:
        target_dir = download_dir or self.download_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        ydl_opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "ignoreconfig": True,
            "ignoreerrors": False,
            "noplaylist": True,
            "retries": options.retries,
            "continuedl": True,
            "overwrites": not options.skip_existing,
            "fragment_retries": YTDLP_FRAGMENT_RETRIES,
            "file_access_retries": YTDLP_FILE_ACCESS_RETRIES,
            "extractor_retries": YTDLP_EXTRACTOR_RETRIES,
            "socket_timeout": YTDLP_SOCKET_TIMEOUT_SECONDS,
            "concurrent_fragment_downloads": YTDLP_CONCURRENT_FRAGMENT_DOWNLOADS,
            "retry_sleep_functions": self._retry_sleep_functions(),
            "outtmpl": str(target_dir / "%(title).200B [%(id)s].%(ext)s"),
            "color": "no_color",
            "sleep_interval_requests": YTDLP_REQUEST_SLEEP_SECONDS,
            "sleep_interval": YTDLP_DOWNLOAD_SLEEP_SECONDS,
            "max_sleep_interval": YTDLP_MAX_DOWNLOAD_SLEEP_SECONDS,
        }
        youtube_profile = self._normalize_youtube_profile(youtube_profile)
        ydl_opts.update(self._javascript_runtime_options())
        extractor_args = self._extractor_args(youtube_profile)
        if extractor_args:
            ydl_opts["extractor_args"] = extractor_args
        impersonate_target = self._impersonation_target(youtube_profile)
        if impersonate_target:
            ydl_opts["impersonate"] = ImpersonateTarget.from_str(impersonate_target)
        if youtube_profile in YOUTUBE_ANTI403_PROFILES:
            ydl_opts["http_chunk_size"] = self.anti403_http_chunk_size_mb * 1024 * 1024

        if options.speed_limit_kbps:
            ydl_opts["ratelimit"] = options.speed_limit_kbps * 1024
        if cookies_path:
            ydl_opts["cookiefile"] = str(cookies_path)
        if options.write_metadata:
            ydl_opts["writedescription"] = True
            ydl_opts["writeinfojson"] = True
        if options.write_thumbnail:
            ydl_opts["writethumbnail"] = True

        ydl_opts["skip_download"] = options.mode == "subtitles_only"
        if options.mode != "subtitles_only":
            ffmpeg_path = self._ffmpeg_executable()
            ffmpeg_available = ffmpeg_path is not None
            if not ffmpeg_available and self._requires_ffmpeg(options):
                requested = options.format_id or options.resolution
                raise RuntimeError(
                    f"ffmpeg is required to download {requested} without silently falling back to a lower resolution."
                )
            ydl_opts["format"] = self._format_selector(options, allow_merge=ffmpeg_available)
            if ffmpeg_available:
                ydl_opts["ffmpeg_location"] = ffmpeg_path
                ydl_opts["merge_output_format"] = "mp4"

        if options.mode in {"video_subtitles", "subtitles_only"}:
            ydl_opts.update(self._subtitle_options(options))

        return ydl_opts

    def download(
        self,
        url: str,
        options: DownloadOptions,
        progress_hook: Callable[[dict[str, Any]], None],
        should_cancel: Callable[[], bool],
        cookies_path: Path | None = None,
        download_dir: Path | None = None,
    ) -> None:
        first_retryable_error: Exception | None = None
        last_error: Exception | None = None
        for youtube_profile in YOUTUBE_DOWNLOAD_PROFILES:
            try:
                self._download_once(
                    url,
                    options,
                    progress_hook,
                    should_cancel,
                    cookies_path,
                    download_dir,
                    youtube_profile,
                )
                return
            except DownloadCancelled:
                raise
            except Exception as exc:
                if youtube_profile == "default" and not self.is_media_stream_blocked_error(exc):
                    raise
                if first_retryable_error is None:
                    first_retryable_error = exc
                last_error = exc
                continue

        if last_error is not None:
            if first_retryable_error is not None and last_error is not first_retryable_error:
                raise last_error from first_retryable_error
            raise last_error

    def _download_once(
        self,
        url: str,
        options: DownloadOptions,
        progress_hook: Callable[[dict[str, Any]], None],
        should_cancel: Callable[[], bool],
        cookies_path: Path | None,
        download_dir: Path | None,
        youtube_profile: str,
    ) -> None:
        ydl_opts = self.build_download_options(
            options,
            cookies_path,
            download_dir=download_dir,
            youtube_profile=youtube_profile,
        )

        def guarded_hook(payload: dict[str, Any]) -> None:
            if should_cancel():
                raise DownloadCancelled("Download was cancelled.")
            progress_hook(payload)

        ydl_opts["progress_hooks"] = [guarded_hook]
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

    def resolution_from_progress_payload(self, payload: dict[str, Any]) -> tuple[int, int] | None:
        info = payload.get("info_dict")
        if not isinstance(info, dict):
            return None

        direct = self._resolution_from_mapping(info)
        if direct:
            return direct

        requested_formats = info.get("requested_formats")
        if not isinstance(requested_formats, list):
            return None

        candidates = [
            resolution
            for fmt in requested_formats
            if isinstance(fmt, dict)
            and fmt.get("vcodec") not in {None, "none"}
            and (resolution := self._resolution_from_mapping(fmt))
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda resolution: resolution[0] * resolution[1])

    def actual_format_from_progress_payload(self, payload: dict[str, Any]) -> str | None:
        info = payload.get("info_dict")
        if not isinstance(info, dict):
            return None

        requested_formats = info.get("requested_formats")
        if isinstance(requested_formats, list):
            video = next(
                (
                    fmt
                    for fmt in requested_formats
                    if isinstance(fmt, dict) and fmt.get("vcodec") not in {None, "none"}
                ),
                None,
            )
            audio = next(
                (
                    fmt
                    for fmt in requested_formats
                    if isinstance(fmt, dict) and fmt.get("acodec") not in {None, "none"}
                ),
                None,
            )
            if video:
                ext = str(video.get("ext") or info.get("ext") or "").strip()
                codecs = [self._short_codec(video.get("vcodec"))]
                if audio:
                    codecs.append(self._short_codec(audio.get("acodec")))
                codec_label = " + ".join(codec for codec in codecs if codec)
                return " · ".join(part for part in [ext, codec_label] if part) or None

        ext = str(info.get("ext") or "").strip()
        codecs = [
            self._short_codec(info.get("vcodec")),
            self._short_codec(info.get("acodec")),
        ]
        codec_label = " + ".join(codec for codec in codecs if codec)
        return " · ".join(part for part in [ext, codec_label] if part) or None

    def detect_file_resolution(self, file_path: Path) -> tuple[int, int] | None:
        ffmpeg_path = self._ffmpeg_executable()
        if not ffmpeg_path or not file_path.exists():
            return None
        try:
            completed = subprocess.run(
                [ffmpeg_path, "-hide_banner", "-i", str(file_path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        output = f"{completed.stdout}\n{completed.stderr}"
        match = re.search(r"Video:.*?\b(\d{2,5})x(\d{2,5})\b", output)
        if not match:
            return None
        return int(match.group(1)), int(match.group(2))

    @staticmethod
    def suggest_lower_resolution(
        requested_resolution: str,
        formats: list[FormatOption],
        min_height: int = MIN_AUTO_FALLBACK_HEIGHT,
    ) -> str | None:
        requested_height = YtDlpService._resolution_height(requested_resolution)
        if requested_height is None:
            return None
        lower_heights = {
            int(format.height)
            for format in formats
            if format.height is not None and min_height <= int(format.height) < requested_height
        }
        if not lower_heights:
            return None
        return f"{max(lower_heights)}p"

    @staticmethod
    def is_requested_format_unavailable_error(exc: Exception) -> bool:
        return "requested format is not available" in str(exc).lower()

    @staticmethod
    def is_http_403_error(exc: BaseException) -> bool:
        for current in YtDlpService._exception_chain(exc):
            message = str(current).lower()
            if "http error 403" in message or ("403" in message and "forbidden" in message):
                return True
        return False

    @staticmethod
    def is_media_stream_blocked_error(exc: BaseException) -> bool:
        return YtDlpService.is_http_403_error(exc) or YtDlpService.is_connection_reset_error(exc)

    @staticmethod
    def is_connection_reset_error(exc: BaseException) -> bool:
        reset_hints = (
            "connection reset",
            "connectionreseterror",
            "connection was reset",
            "recv failure",
            "curl: (35)",
            "10054",
            "远程主机强迫关闭",
        )
        return any(
            any(hint in str(current).lower() for hint in reset_hints)
            for current in YtDlpService._exception_chain(exc)
        )

    @staticmethod
    def readable_error_message(exc: BaseException) -> str:
        for current in YtDlpService._exception_chain(exc):
            message = str(current).strip()
            if message:
                return message
        return f"{type(exc).__name__}（底层错误没有提供具体信息）"

    @staticmethod
    def _exception_chain(exc: BaseException):
        seen: set[int] = set()
        pending: list[BaseException | None] = [exc]
        while pending:
            current = pending.pop(0)
            if current is None or id(current) in seen:
                continue
            seen.add(id(current))
            yield current
            pending.extend([current.__cause__, current.__context__])

    @staticmethod
    def is_cookie_required_error(exc: Exception) -> bool:
        for current in YtDlpService._exception_chain(exc):
            message = str(current).lower()
            cookie_hint = "cookies-from-browser" in message or "--cookies" in message or "cookie" in message
            if cookie_hint and any(hint in message for hint in COOKIE_REQUIRED_AUTH_HINTS):
                return True
        return False

    def _format_selector(self, options: DownloadOptions, allow_merge: bool = True) -> str:
        if not allow_merge:
            return self._single_file_format_selector(options)
        if options.format_id:
            return f"{options.format_id}+ba/{options.format_id}"
        if options.resolution == "best":
            return "bv*+ba/b"
        if options.resolution.endswith("p") and options.resolution[:-1].isdigit():
            height = int(options.resolution[:-1])
            return (
                f"bv*[height={height}][ext=mp4][vcodec^=avc1]+ba[ext=m4a][acodec^=mp4a]/"
                f"bv*[height={height}][ext=mp4]+ba[ext=m4a]/"
                f"bv*[height={height}]+ba/"
                f"b[height={height}][protocol^=m3u8]/"
                f"b[height={height}]"
            )
        return "bv*+ba/b"

    def _single_file_format_selector(self, options: DownloadOptions) -> str:
        if options.format_id:
            return options.format_id
        if options.resolution.endswith("p") and options.resolution[:-1].isdigit():
            height = int(options.resolution[:-1])
            return f"b[height={height}][ext=mp4]/b[height={height}]"
        return "best[ext=mp4]/best"

    def _requires_ffmpeg(self, options: DownloadOptions) -> bool:
        return bool(options.format_id)

    def _normalize_youtube_profile(self, youtube_profile: str) -> str:
        return YOUTUBE_PROFILE_ALIASES.get(youtube_profile, youtube_profile)

    def _extractor_args(self, youtube_profile: str) -> dict[str, dict[str, list[str]]]:
        args: dict[str, dict[str, list[str]]] = {}
        youtube_args = self._youtube_extractor_args(youtube_profile)
        if youtube_args:
            args["youtube"] = youtube_args
        provider_args = self._po_token_provider_args(youtube_profile)
        if provider_args:
            args[POT_PROVIDER_EXTRACTOR] = provider_args
        return args

    def _youtube_extractor_args(self, youtube_profile: str) -> dict[str, list[str]]:
        args: dict[str, list[str]] = {}
        if youtube_profile == "mweb_pot_chrome":
            args["player_client"] = ["mweb", "default"]
        elif youtube_profile == "safari_hls":
            args["player_client"] = ["web_safari", "default"]
        elif youtube_profile == "chrome_default":
            args["player_client"] = ["default"]
        if self.youtube_po_token:
            args["po_token"] = [f"web.gvs+{self.youtube_po_token}"]
        if self.youtube_visitor_data:
            args["visitor_data"] = [self.youtube_visitor_data]
        return args

    def _po_token_provider_args(self, youtube_profile: str) -> dict[str, list[str]]:
        if youtube_profile != "mweb_pot_chrome" or not self.youtube_po_browser_path:
            return {}
        return {"browser_path": [self.youtube_po_browser_path]}

    def _impersonation_target(self, youtube_profile: str) -> str | None:
        if youtube_profile in {"mweb_pot_chrome", "chrome_default"}:
            return "chrome"
        if youtube_profile == "safari_hls":
            return "safari"
        return None

    def _po_token_provider_version(self) -> str | None:
        try:
            return importlib.metadata.version(POT_PROVIDER_DISTRIBUTION)
        except importlib.metadata.PackageNotFoundError:
            return None

    def _retry_sleep_functions(self) -> dict[str, Callable[[int], float]]:
        return {
            "http": self._bounded_retry_sleep,
            "fragment": self._bounded_retry_sleep,
            "file_access": self._short_retry_sleep,
            "extractor": self._bounded_retry_sleep,
        }

    @staticmethod
    def _bounded_retry_sleep(attempt: int) -> float:
        return min(30.0, max(1, attempt) * 2.0)

    @staticmethod
    def _short_retry_sleep(attempt: int) -> float:
        return min(10.0, max(1, attempt) * 1.0)

    def _available_impersonation_targets(self) -> list[str]:
        try:
            from yt_dlp.networking._curlcffi import CurlCFFIRH
        except Exception:
            return []
        return sorted({target.client for target in CurlCFFIRH._SUPPORTED_IMPERSONATE_TARGET_MAP})

    def _ffmpeg_executable(self) -> str | None:
        system_ffmpeg = shutil.which("ffmpeg")
        if system_ffmpeg:
            return system_ffmpeg
        return self._bundled_ffmpeg_executable()

    def _bundled_ffmpeg_executable(self) -> str | None:
        try:
            import imageio_ffmpeg
        except Exception:
            return None
        try:
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None

    def _javascript_runtime_options(self) -> dict[str, Any]:
        runtime = self._detect_js_runtime()
        if not runtime:
            return {}
        name, path, _version = runtime
        return {"js_runtimes": {name: {"path": path}}}

    def _detect_js_runtime(self) -> tuple[str, str, str | None] | None:
        deno_path = shutil.which("deno")
        if deno_path:
            return ("deno", deno_path, self._runtime_version(deno_path))

        node_path = shutil.which("node")
        if node_path:
            version = self._runtime_version(node_path)
            if self._node_version_supported(version):
                return ("node", node_path, version)
        return None

    def _runtime_version(self, executable: str) -> str | None:
        try:
            completed = subprocess.run(
                [executable, "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        output = (completed.stdout or completed.stderr).strip().splitlines()
        return output[0] if output else None

    def _resolution_from_mapping(self, value: dict[str, Any]) -> tuple[int, int] | None:
        width = self._positive_int(value.get("width"))
        height = self._positive_int(value.get("height"))
        if width is None or height is None:
            return None
        return width, height

    def _positive_int(self, value: Any) -> int | None:
        if value is None:
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    def _short_codec(self, value: Any) -> str | None:
        if not value or value == "none":
            return None
        return str(value).split(".", 1)[0]

    @staticmethod
    def _resolution_height(resolution: str) -> int | None:
        if not resolution.endswith("p"):
            return None
        value = resolution[:-1]
        if not value.isdigit():
            return None
        return int(value)

    def _is_youtube_related_cookie(self, domain: str) -> bool:
        normalized = domain.lower().lstrip(".")
        return any(normalized == suffix or normalized.endswith(f".{suffix}") for suffix in YOUTUBE_COOKIE_DOMAIN_SUFFIXES)

    def _node_version_supported(self, version: str | None) -> bool:
        if not version:
            return False
        normalized = version.strip().lstrip("v")
        major = normalized.split(".", 1)[0]
        return major.isdigit() and int(major) >= 20

    def _subtitle_options(self, options: DownloadOptions) -> dict[str, Any]:
        languages = options.subtitle_languages or ["all"]
        subtitle_opts: dict[str, Any] = {
            "writesubtitles": options.subtitle_source in {"human", "both"},
            "writeautomaticsub": options.subtitle_source in {"auto", "both"},
            "subtitleslangs": languages,
        }
        if options.subtitle_format != "best":
            subtitle_opts["subtitlesformat"] = options.subtitle_format
        return subtitle_opts

    def _map_entries(self, info: dict[str, Any]) -> list[VideoEntry]:
        raw_entries = info.get("entries") or []
        entries: list[VideoEntry] = []
        for index, entry in enumerate(raw_entries, start=1):
            if not entry:
                continue
            entry_url = entry.get("webpage_url") or entry.get("url") or ""
            if entry_url and entry_url.startswith("http") is False and entry.get("id"):
                entry_url = f"https://www.youtube.com/watch?v={entry['id']}"
            entries.append(
                VideoEntry(
                    index=index,
                    id=entry.get("id"),
                    title=entry.get("title") or f"Video {index}",
                    url=entry_url,
                    duration=entry.get("duration"),
                    thumbnail=entry.get("thumbnail"),
                )
            )
        return entries

    def _map_formats(self, formats: list[dict[str, Any]]) -> list[FormatOption]:
        mapped: list[FormatOption] = []
        seen: set[str] = set()
        for fmt in formats:
            if self._is_storyboard_or_image_format(fmt):
                continue
            format_id = str(fmt.get("format_id") or "")
            if not format_id or format_id in seen:
                continue
            seen.add(format_id)
            height = fmt.get("height")
            ext = fmt.get("ext")
            fps = fmt.get("fps")
            filesize = fmt.get("filesize") or fmt.get("filesize_approx")
            label_bits = [format_id]
            if height:
                label_bits.append(f"{height}p")
            if fps:
                label_bits.append(f"{fps:g}fps")
            if ext:
                label_bits.append(ext)
            mapped.append(
                FormatOption(
                    format_id=format_id,
                    label=" ".join(label_bits),
                    height=height,
                    ext=ext,
                    fps=fps,
                    filesize=filesize,
                )
            )
        return mapped

    def _is_storyboard_or_image_format(self, fmt: dict[str, Any]) -> bool:
        vcodec = fmt.get("vcodec")
        acodec = fmt.get("acodec")
        return vcodec == "none" and acodec == "none"

    def _map_subtitles(self, subtitles: dict[str, list[dict[str, Any]]]) -> list[SubtitleOption]:
        mapped: list[SubtitleOption] = []
        for language, tracks in sorted(subtitles.items()):
            formats = sorted({track.get("ext", "unknown") for track in tracks if track})
            mapped.append(SubtitleOption(language=language, formats=formats))
        return mapped
