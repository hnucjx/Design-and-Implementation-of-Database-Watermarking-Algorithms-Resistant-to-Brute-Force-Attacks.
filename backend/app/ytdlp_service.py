from collections.abc import Callable
from copy import copy
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

import yt_dlp
from yt_dlp.cookies import YoutubeDLCookieJar, extract_cookies_from_browser
from yt_dlp.version import __version__ as yt_dlp_version

from .schemas import AnalyzeResponse, DownloadOptions, FormatOption, SubtitleOption, VideoEntry


AUTO_BROWSER_COOKIE_CANDIDATES = ["edge", "chrome", "firefox", "brave", "chromium", "vivaldi", "opera"]
YOUTUBE_COOKIE_DOMAIN_SUFFIXES = ("youtube.com", "google.com")


class DownloadCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class BrowserCookieImportResult:
    browser: str
    imported_count: int
    filename: str


class YtDlpService:
    def __init__(self, download_dir: Path) -> None:
        self.download_dir = download_dir

    def get_ffmpeg_status(self) -> dict[str, bool]:
        return {"ffmpeg": self._ffmpeg_executable() is not None, "ffprobe": shutil.which("ffprobe") is not None}

    def get_dependency_status(self) -> dict[str, bool | str | None]:
        ffmpeg = self.get_ffmpeg_status()
        runtime = self._detect_js_runtime()
        return {
            **ffmpeg,
            "js_runtime": runtime is not None,
            "js_runtime_name": runtime[0] if runtime else None,
            "js_runtime_version": runtime[2] if runtime else None,
            "yt_dlp_version": yt_dlp_version,
        }

    def import_browser_cookies(self, browser: str, target_path: Path) -> BrowserCookieImportResult:
        candidates = AUTO_BROWSER_COOKIE_CANDIDATES if browser == "auto" else [browser]
        errors: list[str] = []

        for candidate in candidates:
            try:
                imported = extract_cookies_from_browser(candidate)
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

        detail = "; ".join(errors) if errors else "no supported browser candidates were available"
        raise RuntimeError(f"Could not import YouTube cookies from browser: {detail}")

    def extract_metadata(self, url: str, cookies_path: Path | None = None) -> AnalyzeResponse:
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
            "extract_flat": False,
            "skip_download": True,
            "color": "no_color",
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
    ) -> dict[str, Any]:
        target_dir = download_dir or self.download_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        ydl_opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
            "noplaylist": True,
            "retries": options.retries,
            "continuedl": options.skip_existing,
            "overwrites": not options.skip_existing,
            "outtmpl": str(target_dir / "%(title).200B [%(id)s].%(ext)s"),
            "color": "no_color",
        }
        ydl_opts.update(self._javascript_runtime_options())

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
        ydl_opts = self.build_download_options(options, cookies_path, download_dir=download_dir)

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
    def suggest_lower_resolution(requested_resolution: str, formats: list[FormatOption]) -> str | None:
        requested_height = YtDlpService._resolution_height(requested_resolution)
        if requested_height is None:
            return None
        lower_heights = {
            int(format.height)
            for format in formats
            if format.height is not None and int(format.height) < requested_height
        }
        if not lower_heights:
            return None
        return f"{max(lower_heights)}p"

    @staticmethod
    def is_requested_format_unavailable_error(exc: Exception) -> bool:
        return "requested format is not available" in str(exc).lower()

    def _format_selector(self, options: DownloadOptions, allow_merge: bool = True) -> str:
        if not allow_merge:
            return self._single_file_format_selector(options)
        if options.format_id:
            return f"{options.format_id}+ba/{options.format_id}"
        if options.resolution == "best":
            return "bv*+ba/b"
        if options.resolution.endswith("p") and options.resolution[:-1].isdigit():
            height = int(options.resolution[:-1])
            return f"bv*[height={height}][ext=mp4]+ba[ext=m4a]/bv*[height={height}]+ba/b[height={height}]"
        return "bv*+ba/b"

    def _single_file_format_selector(self, options: DownloadOptions) -> str:
        if options.format_id:
            return options.format_id
        if options.resolution.endswith("p") and options.resolution[:-1].isdigit():
            height = int(options.resolution[:-1])
            return f"best[height={height}][ext=mp4]/best[height={height}]"
        return "best[ext=mp4]/best"

    def _requires_ffmpeg(self, options: DownloadOptions) -> bool:
        return bool(options.format_id) or (
            options.resolution.endswith("p") and options.resolution[:-1].isdigit()
        )

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

    def _map_subtitles(self, subtitles: dict[str, list[dict[str, Any]]]) -> list[SubtitleOption]:
        mapped: list[SubtitleOption] = []
        for language, tracks in sorted(subtitles.items()):
            formats = sorted({track.get("ext", "unknown") for track in tracks if track})
            mapped.append(SubtitleOption(language=language, formats=formats))
        return mapped
