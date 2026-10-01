from collections.abc import Callable, Sequence
from dataclasses import dataclass
import importlib.metadata
import logging
import os
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
from .error_advice import JS_CHALLENGE_HINTS, advise, exception_chain
from .log_safety import sanitize_log_message
from .proxy import ProxyResolution, has_no_proxy_bypass, redact_proxy_credentials, resolve_proxy
from .schemas import AnalyzeResponse, DownloadOptions, FormatOption, SubtitleOption, VideoEntry
from .stall_guard import DownloadStalled, StallGuard
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
YTDLP_SOCKET_TIMEOUT_SECONDS = 30
YTDLP_FRAGMENT_RETRIES = 20
YTDLP_FILE_ACCESS_RETRIES = 5
YTDLP_EXTRACTOR_RETRIES = 5
YTDLP_CONCURRENT_FRAGMENT_DOWNLOADS = 1
DEFAULT_ANTI403_HTTP_CHUNK_SIZE_MB = 16
# 0 = 关闭节流守卫。> 0 会让 yt-dlp 在单条流速度低于该值时抛出 ThrottledDownload
# （ReExtractInfo 子类），被其无计数重提取循环接住，表现为每约 5 秒中断重启一次。
DEFAULT_THROTTLED_RATE_KBPS = 0
DEFAULT_STALL_TIMEOUT_SECONDS = 90.0
# 字幕语言为空时的兜底。**不能**用 yt-dlp 的 ["all"]：那会去拉该视频的全部字幕轨
# （人工 + 自动，常见 20+ 条），密集请求立刻触发 HTTP 429，而 429 会让整个 item 判失败，
# 连视频本体都不会被下载（真实验收里实测到过）。所以兜底必须是有界的小集合。
FALLBACK_SUBTITLE_LANGUAGES = ("en",)
DEFAULT_ARIA2C_CONNECTIONS = 2
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
    # yt-dlp 在部分路径上直接透出 playability status 原文（LOGIN_REQUIRED），
    # 小写化后是 login_required，与 "login required" 并不等价。
    "login_required",
    "only available for registered users",
    "confirm your age",
    "age-restricted",
)
MIN_AUTO_FALLBACK_HEIGHT = DEFAULT_MIN_AUTO_FALLBACK_HEIGHT
# Chromium 内核浏览器：yt-dlp-getpot-wpc 靠它启动一个带 WebPoClient 的页面来铸 PO token。
CHROMIUM_EXECUTABLE_NAMES = ("msedge", "chrome", "chromium", "brave", "vivaldi")
# (Windows 安装根目录下的相对路径, 可执行文件名)，按「优先 Edge」的顺序探测。
_CHROMIUM_INSTALL_SUBPATHS = (
    ("Microsoft", "Edge", "Application", "msedge.exe"),
    ("Google", "Chrome", "Application", "chrome.exe"),
)
NODE_EXECUTABLE_NAMES = ("node", "nodejs")
_DENO_INSTALL_SUBPATHS = (("deno", "deno.exe"),)
_NODE_INSTALL_SUBPATHS = (("nodejs", "node.exe"),)
_POSIX_FALLBACKS = {
    "deno": ("/usr/local/bin/deno", "/opt/homebrew/bin/deno", "/usr/bin/deno"),
    "node": ("/usr/local/bin/node", "/opt/homebrew/bin/node", "/usr/bin/node"),
}
# JS 运行时自检用的哨兵串。必须是「跑得通才有」的输出，不能只看退出码：
# 宿主注入的 NODE_OPTIONS 会让 node 在加载阶段就退出，退出码非 0 但 stderr 与
# JS 运行时本身无关，所以失败原因要原样带出去给用户看。
JS_RUNTIME_PROBE_MARKER = "cascade-js-runtime-ok"
JS_RUNTIME_PROBE_TIMEOUT_SECONDS = 20
logger = logging.getLogger(__name__)


def _detect_executable(
    names: tuple[str, ...],
    install_subpaths: tuple[tuple[str, ...], ...] = (),
    fallback_paths: tuple[str, ...] = (),
) -> str | None:
    """按「PATH → Windows 安装目录 → 固定路径」的顺序定位一个可执行文件。

    只返回**确实存在**的路径：调用方（yt-dlp 插件 / yt-dlp 本体）都会自己校验
    文件是否存在，返回不存在的路径只会把「不可用」伪装成「可用」，所以这里
    宁可返回 ``None``。
    """
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    local_app_data = os.environ.get("LOCALAPPDATA")
    roots = (
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramFiles"),
        local_app_data,
        str(Path(local_app_data) / "Programs") if local_app_data else None,
    )
    for root in roots:
        if not root:
            continue
        for parts in install_subpaths:
            candidate = Path(root).joinpath(*parts)
            if candidate.exists():
                return str(candidate)
    for path in fallback_paths:
        if Path(path).exists():
            return path
    return None


def detect_chromium_executable() -> str | None:
    """定位一个可用的 Chromium 内核浏览器可执行文件（Edge / Chrome）。

    为什么必须自动探测：``yt-dlp-getpot-wpc`` 的 ``is_available()`` 要求
    ``browser_path`` 指向一个**存在**的文件，否则 provider 直接不可用
    （verbose 日志里显示 ``PO Token Providers: ... (external, unavailable)``），
    于是 ``pyproject.toml`` 里声明的依赖等于白装。让用户去配一个绝对路径
    是不现实的默认值，所以这里按「PATH → 常见安装目录」的顺序探测。
    """
    return _detect_executable(CHROMIUM_EXECUTABLE_NAMES, _CHROMIUM_INSTALL_SUBPATHS)


def detect_node_executable() -> str | None:
    """定位 Node，优先 PATH，其次常见安装目录。

    yt-dlp 需要它来解 YouTube 的 nsig（JS challenge）。服务进程的 PATH 里经常
    没有 node（本机装在 ``C:\\Program Files\\nodejs``，而服务由其它 shell 拉起），
    只查 PATH 会把 provider 判成 unavailable。
    """
    return _detect_executable(
        NODE_EXECUTABLE_NAMES,
        _NODE_INSTALL_SUBPATHS,
        _POSIX_FALLBACKS["node"] + (str(Path.home() / ".nvm" / "current" / "bin" / "node"),),
    )


def detect_deno_executable() -> str | None:
    """定位 Deno（yt-dlp 首选的 JS 运行时）。"""
    return _detect_executable(
        ("deno",),
        _DENO_INSTALL_SUBPATHS,
        _POSIX_FALLBACKS["deno"] + (str(Path.home() / ".deno" / "bin" / "deno"),),
    )


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
        stall_timeout_seconds: float = DEFAULT_STALL_TIMEOUT_SECONDS,
        aria2c_enabled: bool = False,
        aria2c_path: str | None = None,
        aria2c_connections: int = DEFAULT_ARIA2C_CONNECTIONS,
        js_runtime_path: str | None = None,
        default_subtitle_languages: Sequence[str] | None = None,
        proxy: str | None = None,
    ) -> None:
        self.download_dir = download_dir
        self.youtube_po_token = youtube_po_token
        self.youtube_visitor_data = youtube_visitor_data
        self.youtube_po_browser_path = youtube_po_browser_path
        self.anti403_http_chunk_size_mb = max(1, anti403_http_chunk_size_mb)
        self.throttled_rate_kbps = max(0, throttled_rate_kbps)
        self.stall_timeout_seconds = max(0.0, float(stall_timeout_seconds))
        self.aria2c_enabled = aria2c_enabled
        self.aria2c_path = aria2c_path
        self.aria2c_connections = max(1, min(4, aria2c_connections))
        # 显式指定的 JS 运行时路径（YTDL_JS_RUNTIME_PATH）；为空时自动探测。
        self.js_runtime_path = js_runtime_path
        # 请求没指定字幕语言时的兜底集合（永远有界，绝不使用 yt-dlp 的 ["all"]）。
        self.default_subtitle_languages = list(default_subtitle_languages or FALLBACK_SUBTITLE_LANGUAGES)
        # 代理：None / "auto" = 自动（优先 Windows 系统代理，其次环境变量）；
        # "direct" 等 = 强制直连；其余按 URL 处理。解析逻辑见 app/proxy.py。
        self.proxy = proxy
        # JS 运行时自检的结论。``_detect_js_runtime`` 会写入「找到了但跑不起来」的原因，
        # 没有它的话「检测到 node 却解不出 n challenge」是查不出来的（见 error_advice）。
        self._js_runtime_rejections: list[str] = []
        self._js_runtime_error: str | None = None
        # (js_runtime_path 键, 探测结果)：探测要起子进程，必须记忆化。
        self._js_runtime_cache: tuple[str, tuple[str, str, str | None] | None] | None = None
        resolution = self.proxy_resolution()
        # 启动时留一行日志：代理相关的故障最难查的就是「到底走没走代理」，
        # 而这行日志与 /api/diagnostics 用的是同一份解析结果。
        logger.info(
            "proxy resolved: source=%s proxy=%s system=%s environment=%s",
            resolution.source,
            redact_proxy_credentials(resolution.url) or "<none>",
            redact_proxy_credentials(resolution.system_proxy) or "<none>",
            redact_proxy_credentials(resolution.environment_proxy) or "<none>",
        )
        # 依赖一句话总览：出问题时先看这行就能排除掉大半「环境没装齐」的可能。
        runtime = self._detect_js_runtime()
        self._log_dependency_summary(runtime)

    def _log_dependency_summary(self, runtime: tuple[str, str, str | None] | None) -> None:
        ffmpeg_ok = self._ffmpeg_executable() is not None
        if runtime:
            logger.info(
                "js runtime ready: name=%s path=%s version=%s",
                runtime[0],
                runtime[1],
                runtime[2] or "unknown",
            )
        else:
            logger.warning(
                "js runtime unavailable: 没有可用的 Deno/Node —— YouTube 的 n challenge 将无法求解，"
                "登录态下可能直接报 “The page needs to be reloaded.”。候选失败原因=%s",
                "; ".join(self._js_runtime_rejections) or "未发现任何候选（PATH 与常见安装目录都没有）",
            )
        logger.info(
            "dependencies ready: ffmpeg=%s po_token_provider=%s chromium=%s aria2c=%s",
            ffmpeg_ok,
            self._po_token_provider_version() is not None,
            self._po_token_browser_path() or "<none>",
            self._aria2c_executable() or "<none>",
        )

    def reset_js_runtime_cache(self) -> None:
        """清掉 JS 运行时的探测缓存（测试与「重新自检」都用它）。"""
        self._js_runtime_cache = None

    def refresh_js_runtime(self) -> None:
        """清掉缓存并立即重新探测，让界面拿到最新结论。

        用户装完 Deno/Node、或修好 ``NODE_OPTIONS`` 之后需要一个明确的重新自检入口，
        否则记忆化会让界面一直显示旧结论。
        """
        self.reset_js_runtime_cache()
        self._detect_js_runtime()

    def proxy_resolution(self) -> ProxyResolution:
        """当前实际会使用的代理（含来源）。每次调用重新解析，设置改了立刻生效。"""
        return resolve_proxy(self.proxy)

    def _proxy_options(self) -> dict[str, str]:
        return self.proxy_resolution().to_ydl_options()

    def get_ffmpeg_status(self) -> dict[str, bool]:
        return {"ffmpeg": self._ffmpeg_executable() is not None, "ffprobe": shutil.which("ffprobe") is not None}

    def get_dependency_status(self) -> dict[str, bool | int | str | None | list[str]]:
        ffmpeg = self.get_ffmpeg_status()
        runtime = self._detect_js_runtime()
        impersonation_targets = self._available_impersonation_targets()
        provider_version = self._po_token_provider_version()
        aria2c_executable = self._aria2c_executable()
        resolution = self.proxy_resolution()
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
            # provider 实际拿到的浏览器路径（显式配置或自动探测）。为 None 时
            # yt-dlp 会把它列为 unavailable，PO token 铸不出来。
            "po_token_browser_path": self._po_token_browser_path(),
            "youtube_po_token_configured": bool(self.youtube_po_token),
            "youtube_visitor_data_configured": bool(self.youtube_visitor_data),
            "js_runtime": runtime is not None,
            "js_runtime_name": runtime[0] if runtime else None,
            "js_runtime_version": runtime[2] if runtime else None,
            # provider 实际拿到的运行时路径（显式配置或自动探测）。
            "js_runtime_path": runtime[1] if runtime else None,
            # 「找到了但跑不起来」的原因。只暴露 js_runtime=False 是不够的：用户会以为
            # 自己明明装了 Node，这里必须把子进程的原始报错带出来。
            "js_runtime_error": self._js_runtime_error,
            "js_runtime_candidates_rejected": list(self._js_runtime_rejections),
            # 代理：proxy 是**实际生效值**（"" = 强制直连），proxy_source 说明它从哪来。
            # 把来源一并暴露出来，是因为「浏览器能上网、应用不能」这种故障里最难查的
            # 恰恰是「到底走了哪个代理」——之前没有任何地方能看到。
            "proxy": redact_proxy_credentials(resolution.url),
            "proxy_source": resolution.source,
            "proxy_writes_ydl_option": resolution.writes_ydl_option,
            "system_proxy": redact_proxy_credentials(resolution.system_proxy),
            "environment_proxy": redact_proxy_credentials(resolution.environment_proxy),
            "no_proxy_bypass_set": has_no_proxy_bypass(),
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
        """Edge cookies 无法离线读取，见 ``BrowserCookieImporter._extract_edge_cookies_via_cdp``。"""
        return BrowserCookieImporter()._extract_edge_cookies_via_cdp()

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
        opts.update(self._proxy_options())
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
        """探测「目标清晰度在当前 selector 下能不能选出可下载组合」。

        返回 `is_selectable=False` 表示**不可选**，调用方据此降级重试。

        注意：yt-dlp 把「选不出格式」实现成**抛异常**而不是返回空结果 ——
        格式选择就发生在 `extract_info` 内部，匹配不到直接抛
        `DownloadError: Requested format is not available`。但预检的语义是
        「这个清晰度**能不能**选」，选不出来是**正常结论**，不是调用失败。
        因此这里必须把该异常转成 `is_selectable=False`；否则调用方
        （`JobManager._prepare_download`）写的「不可选就降一级重试」分支永远不会
        被执行，症状是界面提示「已自动降级到 1080p」而一个字节都没下。见 ai/bug-fix/010。
        """
        if options.mode == "subtitles_only":
            return DownloadPreparation(is_selectable=True)

        ydl_opts = self.build_download_options(options, cookies_path)
        ydl_opts["skip_download"] = True

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
            except Exception as exc:
                # 只把「格式选不出来」当作否定结论；其余异常（网络、JS challenge、
                # cookies 失效）必须继续抛出，否则真实的失败原因会被降级逻辑掩盖。
                if not self.is_requested_format_unavailable_error(exc):
                    raise
                return DownloadPreparation(is_selectable=False)
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
        }
        youtube_profile = self._normalize_youtube_profile(youtube_profile)
        ydl_opts.update(self._javascript_runtime_options())
        ydl_opts.update(self._proxy_options())
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
            except DownloadStalled:
                # 停滞不是某个 profile 的问题：继续换 profile 只会重复等待同样的时间。
                raise
            except Exception as exc:
                logger.warning(
                    "yt-dlp profile failed: profile=%s resolution=%s error_class=%s error=%s",
                    youtube_profile,
                    options.resolution,
                    type(exc).__name__,
                    sanitize_log_message(self.readable_error_message(exc)),
                )
                # 原始报错往往指不到病因（例如 “The page needs to be reloaded.”），
                # 这里在同一处补一条可执行的诊断，用户只看日志就能知道下一步。
                # 被保护起来：诊断本身出错绝不该改变重试/失败的行为。
                try:
                    advice = self.advise_failure(exc, cookies_path)
                except Exception:  # noqa: BLE001
                    advice = None
                if advice:
                    logger.warning(
                        "yt-dlp profile failed advice: profile=%s\n%s",
                        youtube_profile,
                        advice.to_log_block(),
                    )
                if youtube_profile == "default" and not self.should_try_next_profile(exc):
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

        stall_guard = StallGuard(self.stall_timeout_seconds)

        def guarded_hook(payload: dict[str, Any]) -> None:
            if should_cancel():
                raise DownloadCancelled("Download was cancelled.")
            stall_guard.observe(payload)
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
    def is_youtube_auth_blocked_error(exc: BaseException) -> bool:
        """YouTube 在提取阶段要求登录 / 过人机校验。

        这类错误发生在媒体流开始之前（``Sign in to confirm you're not a bot.``、
        ``LOGIN_REQUIRED``、年龄门槛等），既不是 403 也不是连接重置，但换一个
        player_client 组合（web_safari / mweb + PO token）往往就能过，所以必须
        让它继续走 anti403 profile 阶梯，而不是在 ``default`` 档直接放弃。
        """
        return any(
            any(hint in str(current).lower() for hint in COOKIE_REQUIRED_AUTH_HINTS)
            for current in YtDlpService._exception_chain(exc)
        )

    @staticmethod
    def is_js_challenge_error(exc: BaseException) -> bool:
        """JS challenge（n 参数）解不出来 —— 典型表现是 ``The page needs to be reloaded.``。

        这类错误只在**带 cookies 的登录取数路径**上稳定出现：登录态会走需要 nsig 的
        client，解不出来时那些格式被判为「没有可用 URL」，最终抛出的却是完全指不到
        病因的 ``The page needs to be reloaded.``。
        """
        return any(
            any(hint in str(current).lower() for hint in JS_CHALLENGE_HINTS)
            for current in YtDlpService._exception_chain(exc)
        )

    @classmethod
    def should_try_next_profile(cls, exc: BaseException) -> bool:
        """判断当前 profile 失败后是否值得再换一个 profile 重试。

        覆盖三类可自愈的失败：媒体流被挡（403 / 连接重置）、提取阶段被要求登录
        （bot 校验）、以及 JS challenge 解不出来。其余错误（格式不可用、参数非法、
        文件系统问题）换 profile 无意义，应尽快把真实原因暴露给用户。

        其中 JS challenge 一类属于**尽力而为**的兜底：换 client 有可能绕开需要 nsig 的
        那条路径，但若根因是 JS 运行时本身坏了，换 profile 也救不回来 —— 真正的修复是
        ``runtime_env.sanitize_environment``。这里不放弃任何一次机会，因为代价只是一次重试。
        """
        return (
            cls.is_media_stream_blocked_error(exc)
            or cls.is_youtube_auth_blocked_error(exc)
            or cls.is_js_challenge_error(exc)
        )

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

    def advise_failure(self, exc: BaseException, cookies_path: Path | None = None):
        """把一次失败翻译成「发生了什么 + 该做什么」。无法归类时返回 ``None``。

        上下文（JS 运行时自检结果、cookies、代理来源）由服务自己提供，因为只有服务
        知道当前配置；调用方只需要把日志或错误消息展示出去。
        """
        resolution = self.proxy_resolution()
        return advise(
            exc,
            js_runtime_available=self._detect_js_runtime() is not None,
            js_runtime_error=self._js_runtime_error,
            cookies_configured=bool(cookies_path and Path(cookies_path).exists()),
            proxy_source=resolution.source,
            proxy_url=redact_proxy_credentials(resolution.url) or "<direct>",
        )

    @staticmethod
    def _exception_chain(exc: BaseException):
        # 实现搬到 error_advice，保证「分类」和「给用户的建议」看的是同一条异常链。
        return exception_chain(exc)

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

    def _po_token_browser_path(self) -> str | None:
        """wpc provider 要用的浏览器路径：显式配置优先，否则自动探测 Edge/Chrome。"""
        if self.youtube_po_browser_path:
            return self.youtube_po_browser_path
        return self._detect_chromium_executable()

    def _detect_chromium_executable(self) -> str | None:
        return detect_chromium_executable()

    def _po_token_provider_args(self, youtube_profile: str) -> dict[str, list[str]]:
        if youtube_profile != "mweb_pot_chrome":
            return {}
        browser_path = self._po_token_browser_path()
        if not browser_path:
            return {}
        return {"browser_path": [browser_path]}

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
        # 上限 10s：retries=10 时单 profile 的最长静默等待从约 110s 降到约 80s，
        # 失败更快浮出水面；由停滞看门狗（stall_guard）兜底，不再靠长等待自愈。
        return min(10.0, max(1, n) * 2.0)

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
        # 必须带完整路径：``js_runtimes: {"node": {}}``（不给 path）在 yt-dlp 里是
        # 静默空操作 —— 它不会去 PATH 里找，只会认为该运行时不可用。
        return {"js_runtimes": {name: {"path": path}}}

    def _detect_js_runtime(self) -> tuple[str, str, str | None] | None:
        """探测**真正能跑起来**的 JS 运行时（显式配置 → Deno → Node）。

        只查 PATH 是不够的：服务进程的 PATH 里常常没有 node（本机装在
        ``C:\\Program Files\\nodejs``，而服务由别的 shell 拉起），yt-dlp 就会把
        ``JS Challenge Providers`` 里的 node 标成 unavailable，nsig 解不出来、
        提取直接失败。``YTDL_JS_RUNTIME_PATH`` 可显式指定，优先级最高。

        第二件同样重要的事：**能跑起来** ≠ **文件存在**。宿主环境里的 ``NODE_OPTIONS``
        会把外部脚本强加载进每个 node 进程，而 yt-dlp 用 ``--experimental-permission``
        启动 node 时必然拒绝读取它 —— 结果是「检测到 node、版本也合规，却解不出 n
        challenge」，报错还只是 ``The page needs to be reloaded.``。所以这里在返回候选
        之前会**真的执行一次**（并按 yt-dlp 的方式带上 ``--experimental-permission``），
        跑不通就换下一个候选，并把失败原因留在 ``self._js_runtime_error`` 里。

        探测会起子进程，因此按 ``js_runtime_path`` 做了记忆化 —— 否则每次构建 ydl_opts
        都要重跑一遍。
        """
        cache_key = self.js_runtime_path or ""
        if self._js_runtime_cache is not None and self._js_runtime_cache[0] == cache_key:
            return self._js_runtime_cache[1]

        rejections: list[str] = []
        chosen: tuple[str, str, str | None] | None = None
        for candidate in (self.js_runtime_path, detect_deno_executable(), detect_node_executable()):
            if not candidate:
                continue
            # 候选探测整体被保护：这里跑的是外部进程，任何异常都只能算「这个候选不可用」，
            # 绝不能把服务构造或下载流程带崩。
            try:
                runtime = self._js_runtime_from_executable(candidate)
                if not runtime:
                    name = "deno" if Path(candidate).name.lower().startswith("deno") else "node"
                    version = self._runtime_version(candidate)
                    rejections.append(f"{name} {candidate}：版本不可用或无法启动（version={version!r}）")
                    continue
                failure = self._probe_js_runtime(runtime[0], runtime[1])
            except Exception as exc:  # noqa: BLE001
                rejections.append(f"{candidate}：探测时抛出 {type(exc).__name__}: {exc}")
                continue
            if failure:
                rejections.append(f"{runtime[0]} {candidate}：{failure}")
                continue
            chosen = runtime
            break

        self._js_runtime_rejections = rejections
        self._js_runtime_error = "；".join(rejections) if (rejections and not chosen) else None
        self._js_runtime_cache = (cache_key, chosen)
        return chosen

    def _probe_js_runtime(self, name: str, executable: str) -> str | None:
        """真的执行一次 JS 运行时。可用返回 ``None``，否则返回失败原因。

        对 Node 会先按 yt-dlp 的方式（``--experimental-permission``）跑一次，再退回普通
        方式：老版本 Node 不认这个开关，若只试受限方式，会把一个完全可用的 Node 误判为
        不可用 —— 那比不检测更糟。
        """
        script = f"process.stdout.write({JS_RUNTIME_PROBE_MARKER!r})"
        attempts: list[tuple[list[str], str]] = []
        if name == "node":
            attempts.append(
                (
                    [executable, "--experimental-permission", "--no-warnings=ExperimentalWarning", "-e", script],
                    "以 yt-dlp 相同的权限模型启动",
                )
            )
            attempts.append(([executable, "-e", script], "普通启动"))
        else:
            attempts.append(([executable, "eval", f"console.log({JS_RUNTIME_PROBE_MARKER!r})"], "deno eval"))

        first_failure: str | None = None
        for command, label in attempts:
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=JS_RUNTIME_PROBE_TIMEOUT_SECONDS,
                )
            except Exception as exc:  # noqa: BLE001
                # 刻意捕获一切：自检**绝不能**把服务带崩。宿主环境里的猴子补丁、
                # 被替换掉的 subprocess、奇怪的权限都只能算「这个候选不可用」。
                failure = f"{label}失败：{type(exc).__name__}: {exc}"
                first_failure = first_failure or failure
                continue
            if completed.returncode == 0 and JS_RUNTIME_PROBE_MARKER in (completed.stdout or ""):
                return None
            output = (completed.stderr or completed.stdout or "").strip().splitlines()
            head = output[0] if output else "(没有任何输出)"
            tail = output[1] if len(output) > 1 else ""
            failure = f"{label}失败（returncode={completed.returncode}）：{head}"
            if tail:
                failure += f" / {tail}"
            first_failure = first_failure or failure
        return first_failure

    def _js_runtime_from_executable(self, executable: str) -> tuple[str, str, str | None] | None:
        name = "deno" if Path(executable).name.lower().startswith("deno") else "node"
        version = self._runtime_version(executable)
        if name == "node" and not self._node_version_supported(version):
            # 版本过低或根本跑不起来：当作没有这个运行时，让调用方继续找下一个候选。
            return None
        return (name, executable, version)

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

    def _subtitle_languages(self, options: DownloadOptions) -> list[str]:
        """决定本次下载要拉哪些字幕语言。

        空列表**绝不能**退化成 yt-dlp 的 ``["all"]``：那会让 yt-dlp 去拉该视频的
        **全部**字幕轨（人工 + 自动，常见 20+ 条），密集请求立刻触发 HTTP 429，
        而 429 会让整个 item 判定失败——连视频本体都不会被下载（真实验收里实测到）。
        所以空列表改为回退到**有界**默认值（设置里的 ``default_subtitle_languages``）。
        """
        if options.subtitle_languages:
            return list(options.subtitle_languages)
        if self.default_subtitle_languages:
            return list(self.default_subtitle_languages)
        return list(FALLBACK_SUBTITLE_LANGUAGES)

    def _subtitle_options(self, options: DownloadOptions) -> dict[str, Any]:
        languages = self._subtitle_languages(options)
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
