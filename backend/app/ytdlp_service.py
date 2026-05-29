from collections.abc import Callable
from dataclasses import dataclass
import importlib.metadata
import logging
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

import yt_dlp
from yt_dlp.cookies import YoutubeDLCookieJar, extract_cookies_from_browser
from yt_dlp.networking.impersonate import ImpersonateTarget
from yt_dlp.version import __version__ as yt_dlp_version

from .browser_cookies import (
    AUTO_BROWSER_COOKIE_CANDIDATES,
    BrowserCookieImporter,
    BrowserCookieImportError,
    BrowserCookieImportResult,
)
from .log_safety import sanitize_log_message
from .schemas import AnalyzeResponse, DownloadOptions, FormatOption, SubtitleOption, VideoEntry
from .ytdlp_formats import (
    DEFAULT_MIN_AUTO_FALLBACK_HEIGHT,
    actual_format_from_info_dict,
    filesize_from_info_dict,
    format_selector,
    has_resolution_at_or_above,
    positive_int,
    requires_ffmpeg,
    resolution_from_info_dict,
    resolution_from_mapping,
    resolution_height,
    short_codec,
    single_file_format_selector,
    suggest_lower_resolution,
)


YTDLP_REQUEST_SLEEP_SECONDS = 1.0
YTDLP_DOWNLOAD_SLEEP_SECONDS = 2.0
YTDLP_MAX_DOWNLOAD_SLEEP_SECONDS = 5.0
YTDLP_SOCKET_TIMEOUT_SECONDS = 30
YTDLP_FRAGMENT_RETRIES = 20
YTDLP_FILE_ACCESS_RETRIES = 5
YTDLP_EXTRACTOR_RETRIES = 5
YTDLP_CONCURRENT_FRAGMENT_DOWNLOADS = 1
DEFAULT_ANTI403_HTTP_CHUNK_SIZE_MB = 16
DEFAULT_THROTTLED_RATE_KBPS = 64
DEFAULT_ARIA2C_CONNECTIONS = 1
DEFAULT_ARIA2C_MIN_SPLIT_SIZE_MB = 16
DEFAULT_ARIA2C_RETRY_WAIT_SECONDS = 5
YOUTUBE_DOWNLOAD_PROFILES = ("default", "default_aria2c", "mweb_pot_chrome", "safari_hls", "chrome_default")
YOUTUBE_ANTI403_PROFILES = frozenset(("mweb_pot_chrome", "safari_hls", "chrome_default"))
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
MIN_AUTO_FALLBACK_HEIGHT = DEFAULT_MIN_AUTO_FALLBACK_HEIGHT
logger = logging.getLogger(__name__)


class DownloadCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class DownloadPreparation:
    is_selectable: bool
    width: int | None = None
    height: int | None = None
    actual_format: str | None = None
    filesize: int | None = None


class YtDlpService:
    def __init__(
        self,
        download_dir: Path,
        youtube_po_token: str | None = None,
        youtube_visitor_data: str | None = None,
        youtube_po_browser_path: str | None = None,
        anti403_http_chunk_size_mb: int = DEFAULT_ANTI403_HTTP_CHUNK_SIZE_MB,
        throttled_rate_kbps: int = DEFAULT_THROTTLED_RATE_KBPS,
        aria2c_enabled: bool = False,
        aria2c_path: str | None = None,
        aria2c_connections: int = DEFAULT_ARIA2C_CONNECTIONS,
    ) -> None:
        self.download_dir = download_dir
        self.youtube_po_token = youtube_po_token
        self.youtube_visitor_data = youtube_visitor_data
        self.youtube_po_browser_path = youtube_po_browser_path
        self.anti403_http_chunk_size_mb = max(1, anti403_http_chunk_size_mb)
        self.throttled_rate_kbps = max(0, throttled_rate_kbps)
        self.aria2c_enabled = aria2c_enabled
        self.aria2c_path = aria2c_path
        self.aria2c_connections = max(1, min(4, aria2c_connections))

    def get_ffmpeg_status(self) -> dict[str, bool]:
        return {"ffmpeg": self._ffmpeg_executable() is not None, "ffprobe": shutil.which("ffprobe") is not None}

    def get_dependency_status(self) -> dict[str, bool | int | str | None | list[str]]:
        ffmpeg = self.get_ffmpeg_status()
        runtime = self._detect_js_runtime()
        impersonation_targets = self._available_impersonation_targets()
        provider_version = self._po_token_provider_version()
        aria2c_executable = self._aria2c_executable()
        return {
            **ffmpeg,
            "impersonation_available": bool(impersonation_targets),
            "impersonation_targets": impersonation_targets,
            "aria2c_available": aria2c_executable is not None,
            "aria2c_enabled": self.aria2c_enabled,
            "aria2c_path": aria2c_executable,
            "aria2c_connections": self.aria2c_connections,
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
        return BrowserCookieImporter(
            candidates=AUTO_BROWSER_COOKIE_CANDIDATES,
            extract_browser_cookie_jar=self._extract_browser_cookie_jar,
            close_browser_for_cookie_import=self._close_browser_for_cookie_import,
            extract_edge_cookies_via_cdp=self._extract_edge_cookies_via_cdp,
        ).import_browser_cookies(browser, target_path, close_browser_if_locked)

    def _extract_browser_cookie_jar(self, browser: str) -> YoutubeDLCookieJar:
        return extract_cookies_from_browser(browser)

    def _close_browser_for_cookie_import(self, browser: str) -> None:
        BrowserCookieImporter()._close_browser_for_cookie_import(browser)

    def _extract_edge_cookies_via_cdp(self) -> YoutubeDLCookieJar:
        return BrowserCookieImporter()._extract_edge_cookies_via_cdp()

    def _terminate_edge_process(self, process: Any) -> None:
        BrowserCookieImporter()._terminate_edge_process(process)

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

    def prepare_download(
        self,
        url: str,
        options: DownloadOptions,
        cookies_path: Path | None = None,
    ) -> DownloadPreparation:
        if options.mode == "subtitles_only":
            return DownloadPreparation(is_selectable=True)

        ydl_opts = self.build_download_options(options, cookies_path)
        ydl_opts["skip_download"] = True

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return DownloadPreparation(is_selectable=False)
            formats = info.get("formats") or []
            selector = ydl.build_format_selector(str(ydl_opts.get("format") or "best"))
            selected = list(ydl._select_formats(formats, selector))

        if not selected:
            return DownloadPreparation(is_selectable=False)

        selected_format = selected[0]
        resolution = self._resolution_from_info_dict(selected_format)
        actual_format = self._actual_format_from_info_dict(selected_format)
        filesize = self._filesize_from_info_dict(selected_format)
        return DownloadPreparation(
            is_selectable=True,
            width=resolution[0] if resolution else None,
            height=resolution[1] if resolution else None,
            actual_format=actual_format,
            filesize=filesize,
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
        if options.mode != "subtitles_only":
            ydl_opts["http_chunk_size"] = self.anti403_http_chunk_size_mb * 1024 * 1024
            if self.throttled_rate_kbps > 0:
                ydl_opts["throttledratelimit"] = self.throttled_rate_kbps * 1024

        if options.speed_limit_kbps:
            ydl_opts["ratelimit"] = options.speed_limit_kbps * 1024
        if options.mode != "subtitles_only" and youtube_profile == "default_aria2c":
            aria2c_executable = self._aria2c_executable() if self.aria2c_enabled else None
            if aria2c_executable:
                ydl_opts["external_downloader"] = {"http": aria2c_executable, "https": aria2c_executable}
                ydl_opts["external_downloader_args"] = {"aria2c": self._aria2c_args(options)}
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
            ydl_opts["format"] = self._format_selector(
                options,
                allow_merge=ffmpeg_available,
                prefer_hls=youtube_profile == "safari_hls",
            )
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
        for youtube_profile in self._download_profiles():
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
                logger.warning(
                    "yt-dlp profile failed: profile=%s resolution=%s error_class=%s error=%s",
                    youtube_profile,
                    options.resolution,
                    type(exc).__name__,
                    sanitize_log_message(self.readable_error_message(exc)),
                )
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
        return self._resolution_from_info_dict(info)

    def _resolution_from_info_dict(self, info: dict[str, Any]) -> tuple[int, int] | None:
        return resolution_from_info_dict(info)

    def actual_format_from_progress_payload(self, payload: dict[str, Any]) -> str | None:
        info = payload.get("info_dict")
        if not isinstance(info, dict):
            return None
        return self._actual_format_from_info_dict(info)

    def _actual_format_from_info_dict(self, info: dict[str, Any]) -> str | None:
        return actual_format_from_info_dict(info)

    def _filesize_from_info_dict(self, info: dict[str, Any]) -> int | None:
        return filesize_from_info_dict(info)

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
        allow_below_min_if_source_below_min: bool = False,
    ) -> str | None:
        return suggest_lower_resolution(
            requested_resolution,
            formats,
            min_height=min_height,
            allow_below_min_if_source_below_min=allow_below_min_if_source_below_min,
        )

    @staticmethod
    def has_resolution_at_or_above(
        formats: list[FormatOption],
        min_height: int = MIN_AUTO_FALLBACK_HEIGHT,
    ) -> bool:
        return has_resolution_at_or_above(formats, min_height=min_height)

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
            "read operation timed out",
            "read timed out",
            "timed out",
            "remote end closed",
            "remote host closed",
            "recv failure",
            "curl: (35)",
            "curl: (56)",
            "incompleteread",
            "tls",
            "ssl",
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

    def _format_selector(self, options: DownloadOptions, allow_merge: bool = True, prefer_hls: bool = False) -> str:
        return format_selector(options, allow_merge=allow_merge, prefer_hls=prefer_hls)

    def _single_file_format_selector(self, options: DownloadOptions) -> str:
        return single_file_format_selector(options)

    def _requires_ffmpeg(self, options: DownloadOptions) -> bool:
        return requires_ffmpeg(options)

    def _normalize_youtube_profile(self, youtube_profile: str) -> str:
        return YOUTUBE_PROFILE_ALIASES.get(youtube_profile, youtube_profile)

    def _download_profiles(self) -> tuple[str, ...]:
        if self.aria2c_enabled and self._aria2c_executable():
            return YOUTUBE_DOWNLOAD_PROFILES
        return tuple(profile for profile in YOUTUBE_DOWNLOAD_PROFILES if profile != "default_aria2c")

    def _aria2c_executable(self) -> str | None:
        if self.aria2c_path:
            configured = Path(self.aria2c_path)
            if configured.exists():
                return str(configured)
            return shutil.which(self.aria2c_path)
        return shutil.which("aria2c")

    def _aria2c_args(self, options: DownloadOptions) -> list[str]:
        connections = str(self.aria2c_connections)
        return [
            "-x",
            connections,
            "-s",
            connections,
            "-j",
            "1",
            "--min-split-size",
            f"{DEFAULT_ARIA2C_MIN_SPLIT_SIZE_MB}M",
            "--max-tries",
            str(max(1, options.retries)),
            "--retry-wait",
            str(DEFAULT_ARIA2C_RETRY_WAIT_SECONDS),
            "--timeout",
            str(YTDLP_SOCKET_TIMEOUT_SECONDS),
            "--connect-timeout",
            str(YTDLP_SOCKET_TIMEOUT_SECONDS),
        ]

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

    def _retry_sleep_functions(self) -> dict[str, Callable[..., float]]:
        return {
            "http": self._bounded_retry_sleep,
            "fragment": self._bounded_retry_sleep,
            "file_access": self._short_retry_sleep,
            "extractor": self._bounded_retry_sleep,
        }

    @staticmethod
    def _bounded_retry_sleep(n: int = 0, **_: Any) -> float:
        return min(30.0, max(1, n) * 2.0)

    @staticmethod
    def _short_retry_sleep(n: int = 0, **_: Any) -> float:
        return min(10.0, max(1, n) * 1.0)

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
        return resolution_from_mapping(value)

    def _positive_int(self, value: Any) -> int | None:
        return positive_int(value)

    def _short_codec(self, value: Any) -> str | None:
        return short_codec(value)

    @staticmethod
    def _resolution_height(resolution: str) -> int | None:
        return resolution_height(resolution)

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
