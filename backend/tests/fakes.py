import queue
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from app.schemas import AnalyzeResponse, DownloadOptions, FormatOption, SubtitleOption, VideoEntry
from app.ytdlp_service import BrowserCookieImportError, DownloadCancelled
class FakeYtDlpService:
    def __init__(self):
        self.downloads = []

    def get_ffmpeg_status(self):
        return {"ffmpeg": True, "ffprobe": True}

    def get_dependency_status(self):
        return {
            "ffmpeg": True,
            "ffprobe": True,
            "impersonation_available": True,
            "impersonation_targets": ["safari"],
            "aria2c_available": False,
            "aria2c_enabled": False,
            "aria2c_path": None,
            "aria2c_connections": 1,
            "po_token_provider_available": True,
            "po_token_provider": "yt-dlp-getpot-wpc",
            "po_token_provider_version": "1.0.0",
            "youtube_po_browser_path_configured": False,
            "youtube_po_token_configured": False,
            "youtube_visitor_data_configured": False,
            "js_runtime": True,
            "js_runtime_name": "node",
            "js_runtime_version": "v20.11.1",
            "yt_dlp_version": "test",
        }

    def extract_metadata(self, url, cookies_path=None):
        if "playlist" in url:
            return AnalyzeResponse(
                url=url,
                title="Batch",
                is_playlist=True,
                entries=[
                    VideoEntry(index=1, id="one", title="One", url="https://youtu.be/one"),
                    VideoEntry(index=2, id="two", title="Two", url="https://youtu.be/two"),
                ],
                formats=[FormatOption(format_id="22", label="720p mp4", height=720, ext="mp4")],
                subtitles=[SubtitleOption(language="en", formats=["vtt"])],
                automatic_subtitles=[],
                ffmpeg={"ffmpeg": True, "ffprobe": True},
            )
        return AnalyzeResponse(
            url=url,
            title="Single",
            is_playlist=False,
            entries=[],
            formats=[
                FormatOption(format_id="22", label="720p mp4", height=720, ext="mp4"),
                FormatOption(format_id="18", label="360p mp4", height=360, ext="mp4"),
            ],
            subtitles=[],
            automatic_subtitles=[],
            ffmpeg={"ffmpeg": True, "ffprobe": True},
        )

    def download(self, url, options, progress_hook, should_cancel, cookies_path=None, download_dir=None):
        self.downloads.append({"url": url, "download_dir": download_dir})
        progress_hook({"status": "finished", "filename": f"{url}.mp4"})

    def prepare_download(self, url, options, cookies_path=None):
        return SimpleNamespace(is_selectable=True, width=None, height=None, actual_format=None)

    def resolution_from_progress_payload(self, payload):
        return None

    def actual_format_from_progress_payload(self, payload):
        return None

    def detect_file_resolution(self, file_path):
        return None


class AverageSpeedYtDlpService(FakeYtDlpService):
    def download(self, url, options, progress_hook, should_cancel, cookies_path=None, download_dir=None):
        progress_hook({"status": "downloading", "downloaded_bytes": 0, "total_bytes": 8192, "speed": 4096})
        progress_hook({"status": "downloading", "downloaded_bytes": 4096, "total_bytes": 8192, "speed": 2048})
        progress_hook({"status": "finished", "downloaded_bytes": 8192, "total_bytes": 8192, "filename": f"{url}.mp4"})


class SplitStreamProgressYtDlpService(FakeYtDlpService):
    def __init__(self):
        super().__init__()
        self.stages: queue.Queue[str] = queue.Queue()
        self.release = threading.Event()

    def continue_download(self) -> None:
        self.release.set()

    def download(self, url, options, progress_hook, should_cancel, cookies_path=None, download_dir=None):
        video_info = {
            "format_id": "137",
            "ext": "mp4",
            "width": 1920,
            "height": 1080,
            "vcodec": "avc1.640028",
            "acodec": "none",
        }
        audio_info = {
            "format_id": "140",
            "ext": "m4a",
            "vcodec": "none",
            "acodec": "mp4a.40.2",
        }
        self._emit(
            "video_started",
            {
                "status": "downloading",
                "downloaded_bytes": 0,
                "total_bytes": 100,
                "tmpfilename": "video.f137.mp4.part",
                "filename": "video.f137.mp4",
                "speed": 100,
                "info_dict": video_info,
            },
            progress_hook,
        )
        self._emit(
            "video_finished",
            {
                "status": "finished",
                "downloaded_bytes": 100,
                "total_bytes": 100,
                "filename": "video.f137.mp4",
                "info_dict": video_info,
            },
            progress_hook,
        )
        self._emit(
            "audio_started",
            {
                "status": "downloading",
                "downloaded_bytes": 0,
                "total_bytes": 20,
                "tmpfilename": "video.f140.m4a.part",
                "filename": "video.f140.m4a",
                "speed": 20,
                "info_dict": audio_info,
            },
            progress_hook,
        )
        self._emit(
            "audio_finished",
            {
                "status": "finished",
                "downloaded_bytes": 20,
                "total_bytes": 20,
                "filename": "video.f140.m4a",
                "info_dict": audio_info,
            },
            progress_hook,
            wait=False,
        )
        if download_dir is not None:
            final_file = Path(download_dir) / "video.mp4"
            final_file.parent.mkdir(parents=True, exist_ok=True)
            final_file.write_text("video", encoding="utf-8")

    def _emit(self, stage, payload, progress_hook, wait=True):
        progress_hook(payload)
        self.stages.put(stage)
        if wait:
            self.release.wait(timeout=5)
            self.release.clear()


class BlockingYtDlpService(FakeYtDlpService):
    def __init__(self):
        super().__init__()
        self.started: queue.Queue[str] = queue.Queue()
        self.release = threading.Event()

    def download(self, url, options, progress_hook, should_cancel, cookies_path=None, download_dir=None):
        self.started.put(url)
        self.release.wait(timeout=5)
        progress_hook({"status": "finished", "filename": f"{url}.mp4"})


class RuntimeRestartYtDlpService(FakeYtDlpService):
    def __init__(self):
        super().__init__()
        self.started: queue.Queue[DownloadOptions] = queue.Queue()
        self.release = threading.Event()

    def download(self, url, options, progress_hook, should_cancel, cookies_path=None, download_dir=None):
        self.started.put(options)
        while not self.release.is_set():
            if should_cancel():
                raise DownloadCancelled("Download options changed.")
            time.sleep(0.02)
        progress_hook({"status": "finished", "filename": f"{url}.mp4"})


class PreparedBlockingYtDlpService(BlockingYtDlpService):
    def prepare_download(self, url, options, cookies_path=None):
        return SimpleNamespace(
            is_selectable=True,
            width=1920,
            height=1080,
            actual_format="mp4 · avc1 + mp4a",
            filesize=10_485_760,
        )


class SingleAutoFallbackYtDlpService(FakeYtDlpService):
    def __init__(self):
        super().__init__()
        self.download_options: list[DownloadOptions] = []

    def extract_metadata(self, url, cookies_path=None):
        return AnalyzeResponse(
            url=url,
            title="Unsupported resolution",
            is_playlist=False,
            entries=[],
            formats=[
                FormatOption(format_id="22", label="720p mp4", height=720, ext="mp4"),
                FormatOption(format_id="18", label="360p mp4", height=360, ext="mp4"),
            ],
            subtitles=[],
            automatic_subtitles=[],
            ffmpeg={"ffmpeg": True, "ffprobe": True},
        )

    def download(self, url, options, progress_hook, should_cancel, cookies_path=None, download_dir=None):
        self.download_options.append(options)
        progress_hook(
            {
                "status": "finished",
                "filename": f"{url}.mp4",
                "info_dict": {
                    "requested_formats": [
                        {
                            "format_id": "22",
                            "ext": "mp4",
                            "vcodec": "avc1.64001f",
                            "acodec": "none",
                            "width": 1280,
                            "height": 720,
                        },
                        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"},
                    ]
                },
            }
        )

    def resolution_from_progress_payload(self, payload):
        return (1280, 720)

    def actual_format_from_progress_payload(self, payload):
        return "mp4 · avc1 + mp4a"


class AutoFallbackYtDlpService(FakeYtDlpService):
    def __init__(self):
        super().__init__()
        self.download_options: list[DownloadOptions] = []

    def extract_metadata(self, url, cookies_path=None):
        if "playlist" in url:
            return AnalyzeResponse(
                url=url,
                title="Mixed quality playlist",
                is_playlist=True,
                entries=[
                    VideoEntry(index=1, id="one", title="Part one", url="https://youtu.be/one"),
                    VideoEntry(index=2, id="two", title="Part two", url="https://youtu.be/two"),
                ],
                formats=[],
                subtitles=[],
                automatic_subtitles=[],
                ffmpeg={"ffmpeg": True, "ffprobe": True},
            )
        if "two" in url:
            return AnalyzeResponse(
                url=url,
                title="Part two",
                is_playlist=False,
                entries=[],
                formats=[
                    FormatOption(format_id="22", label="720p mp4", height=720, ext="mp4"),
                    FormatOption(format_id="18", label="360p mp4", height=360, ext="mp4"),
                ],
                subtitles=[],
                automatic_subtitles=[],
                ffmpeg={"ffmpeg": True, "ffprobe": True},
            )
        return AnalyzeResponse(
            url=url,
            title="Part one",
            is_playlist=False,
            entries=[],
            formats=[
                FormatOption(format_id="137", label="1080p mp4", height=1080, ext="mp4"),
                FormatOption(format_id="22", label="720p mp4", height=720, ext="mp4"),
            ],
            subtitles=[],
            automatic_subtitles=[],
            ffmpeg={"ffmpeg": True, "ffprobe": True},
        )

    def download(self, url, options, progress_hook, should_cancel, cookies_path=None, download_dir=None):
        self.download_options.append(options)
        if "two" in url and options.resolution == "1080p":
            raise RuntimeError(
                "ERROR: [youtube] -lp1p-gcx9I: Requested format is not available. "
                "Use --list-formats for a list of available formats"
            )
        height = int(options.resolution[:-1]) if options.resolution.endswith("p") else 720
        progress_hook(
            {
                "status": "finished",
                "filename": f"{url}.mp4",
                "info_dict": {
                    "requested_formats": [
                        {
                            "format_id": "137",
                            "ext": "mp4",
                            "vcodec": "avc1.640028",
                            "acodec": "none",
                            "width": int(height * 16 / 9),
                            "height": height,
                        },
                        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"},
                    ]
                },
            }
        )

    def resolution_from_progress_payload(self, payload):
        return (1280, 720) if self.download_options[-1].resolution == "720p" else (1920, 1080)

    def actual_format_from_progress_payload(self, payload):
        return "mp4 · avc1 + mp4a"

    def detect_file_resolution(self, file_path):
        return None


class NoLowerResolutionYtDlpService(FakeYtDlpService):
    def __init__(self):
        super().__init__()
        self.download_called = False

    def extract_metadata(self, url, cookies_path=None):
        return AnalyzeResponse(
            url=url,
            title="No lower quality",
            is_playlist=False,
            entries=[],
            formats=[FormatOption(format_id="18", label="360p mp4", height=360, ext="mp4")],
            subtitles=[],
            automatic_subtitles=[],
            ffmpeg={"ffmpeg": True, "ffprobe": True},
        )

    def download(self, url, options, progress_hook, should_cancel, cookies_path=None, download_dir=None):
        self.download_called = True
        raise RuntimeError(
            "ERROR: [youtube] no-lower: Requested format is not available. "
            "Use --list-formats for a list of available formats"
        )

    def resolution_from_progress_payload(self, payload):
        return None

    def actual_format_from_progress_payload(self, payload):
        return None

    def detect_file_resolution(self, file_path):
        return None


class LowOnlyFallbackYtDlpService(FakeYtDlpService):
    def __init__(self):
        super().__init__()
        self.download_options: list[DownloadOptions] = []

    def extract_metadata(self, url, cookies_path=None):
        return AnalyzeResponse(
            url=url,
            title="Low source",
            is_playlist=False,
            entries=[],
            formats=[FormatOption(format_id="18", label="360p mp4", height=360, ext="mp4")],
            subtitles=[],
            automatic_subtitles=[],
            ffmpeg={"ffmpeg": True, "ffprobe": True},
        )

    def prepare_download(self, url, options, cookies_path=None):
        if options.resolution == "360p":
            return SimpleNamespace(is_selectable=True, width=640, height=360, actual_format="mp4 · avc1 + mp4a")
        return SimpleNamespace(is_selectable=False, width=None, height=None, actual_format=None)

    def download(self, url, options, progress_hook, should_cancel, cookies_path=None, download_dir=None):
        self.download_options.append(options)
        progress_hook(
            {
                "status": "finished",
                "filename": f"{url}.mp4",
                "info_dict": {"ext": "mp4", "vcodec": "avc1.42001E", "acodec": "mp4a.40.2", "width": 640, "height": 360},
            }
        )

    def resolution_from_progress_payload(self, payload):
        return (640, 360)

    def actual_format_from_progress_payload(self, payload):
        return "mp4 · avc1 + mp4a"


class UnselectableHighWithSafeFallbackService(FakeYtDlpService):
    def __init__(self):
        super().__init__()
        self.download_options: list[DownloadOptions] = []

    def extract_metadata(self, url, cookies_path=None):
        return AnalyzeResponse(
            url=url,
            title="Unselectable high",
            is_playlist=False,
            entries=[],
            formats=[
                FormatOption(format_id="137", label="1080p mp4", height=1080, ext="mp4"),
                FormatOption(format_id="22", label="720p mp4", height=720, ext="mp4"),
            ],
            subtitles=[],
            automatic_subtitles=[],
            ffmpeg={"ffmpeg": True, "ffprobe": True},
        )

    def prepare_download(self, url, options, cookies_path=None):
        if options.resolution == "1080p":
            return SimpleNamespace(is_selectable=False, width=None, height=None, actual_format=None)
        return SimpleNamespace(is_selectable=True, width=1280, height=720, actual_format="mp4 · avc1 + mp4a")

    def download(self, url, options, progress_hook, should_cancel, cookies_path=None, download_dir=None):
        self.download_options.append(options)
        progress_hook(
            {
                "status": "finished",
                "filename": f"{url}.mp4",
                "info_dict": {
                    "requested_formats": [
                        {"format_id": "22", "ext": "mp4", "vcodec": "avc1.64001f", "acodec": "none", "width": 1280, "height": 720},
                        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"},
                    ]
                },
            }
        )

    def resolution_from_progress_payload(self, payload):
        return (1280, 720)

    def actual_format_from_progress_payload(self, payload):
        return "mp4 · avc1 + mp4a"


class UnselectableHighWithoutSafeFallbackService(UnselectableHighWithSafeFallbackService):
    def extract_metadata(self, url, cookies_path=None):
        return AnalyzeResponse(
            url=url,
            title="Unsafe low fallback",
            is_playlist=False,
            entries=[],
            formats=[
                FormatOption(format_id="137", label="1080p mp4", height=1080, ext="mp4"),
                FormatOption(format_id="18", label="360p mp4", height=360, ext="mp4"),
            ],
            subtitles=[],
            automatic_subtitles=[],
            ffmpeg={"ffmpeg": True, "ffprobe": True},
        )

class BrowserImportYtDlpService(FakeYtDlpService):
    def __init__(self):
        super().__init__()
        self.imports: list[str] = []

    def import_browser_cookies(self, browser, target_path, close_browser_if_locked=False):
        self.imports.append(browser)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text("SECRET_COOKIE_VALUE", encoding="utf-8")
        return {
            "enabled": True,
            "filename": target_path.name,
            "source": "browser",
            "browser": "edge",
            "imported_count": 4,
        }


class BotChallengeYtDlpService(BrowserImportYtDlpService):
    def __init__(self):
        super().__init__()
        self.extract_calls: list[Path | None] = []

    def extract_metadata(self, url, cookies_path=None):
        self.extract_calls.append(cookies_path)
        if cookies_path is None or not Path(cookies_path).exists():
            raise RuntimeError("Sign in to confirm you’re not a bot. Use --cookies-from-browser or --cookies")
        return super().extract_metadata(url, cookies_path=cookies_path)


class LockedEdgeBrowserImportService(BotChallengeYtDlpService):
    def import_browser_cookies(self, browser, target_path, close_browser_if_locked=False):
        self.imports.append((browser, close_browser_if_locked))
        if not close_browser_if_locked:
            raise BrowserCookieImportError.browser_locked("edge", "Could not copy Chrome cookie database.")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text("SECRET_COOKIE_VALUE", encoding="utf-8")
        return {
            "enabled": True,
            "filename": target_path.name,
            "source": "browser",
            "browser": "edge",
            "imported_count": 4,
        }


class DownloadBotChallengeYtDlpService(BrowserImportYtDlpService):
    def __init__(self):
        super().__init__()
        self.cookie_snapshots: list[str | None] = []

    def resolution_from_progress_payload(self, payload):
        return None

    def actual_format_from_progress_payload(self, payload):
        return None

    def detect_file_resolution(self, file_path):
        return None

    def download(self, url, options, progress_hook, should_cancel, cookies_path=None, download_dir=None):
        cookie_snapshot = Path(cookies_path).read_text(encoding="utf-8") if cookies_path else None
        self.cookie_snapshots.append(cookie_snapshot)
        if len(self.cookie_snapshots) == 1:
            raise RuntimeError(
                "ERROR: [youtube] G9MxNwUoSt0: Sign in to confirm you’re not a bot. "
                "Use --cookies-from-browser or --cookies for the authentication."
            )
        progress_hook({"status": "finished", "filename": f"{url}.mp4"})


class DownloadHttp403ThenAnti403SuccessService(FakeYtDlpService):
    def __init__(self):
        super().__init__()
        self.profile_attempts: list[str] = []

    def download(self, url, options, progress_hook, should_cancel, cookies_path=None, download_dir=None):
        self.profile_attempts.extend(["default", "anti403"])
        progress_hook(
            {
                "status": "finished",
                "filename": f"{url}.mp4",
                "info_dict": {
                    "requested_formats": [
                        {
                            "format_id": "22",
                            "ext": "mp4",
                            "vcodec": "avc1.64001f",
                            "acodec": "none",
                            "width": 1280,
                            "height": 720,
                        },
                        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"},
                    ]
                },
            }
        )

    def resolution_from_progress_payload(self, payload):
        return (1280, 720)

    def actual_format_from_progress_payload(self, payload):
        return "mp4 · avc1 + mp4a"


class DownloadHttp403FallbackResolutionService(FakeYtDlpService):
    def __init__(self, always_forbidden: bool = False):
        super().__init__()
        self.always_forbidden = always_forbidden
        self.download_resolutions: list[str] = []

    def extract_metadata(self, url, cookies_path=None):
        return AnalyzeResponse(
            url=url,
            title="403 fallback",
            is_playlist=False,
            entries=[],
            formats=[
                FormatOption(format_id="137", label="1080p mp4", height=1080, ext="mp4"),
                FormatOption(format_id="22", label="720p mp4", height=720, ext="mp4"),
                FormatOption(format_id="18", label="360p mp4", height=360, ext="mp4"),
            ],
            subtitles=[],
            automatic_subtitles=[],
            ffmpeg={"ffmpeg": True, "ffprobe": True},
        )

    def download(self, url, options, progress_hook, should_cancel, cookies_path=None, download_dir=None):
        self.download_resolutions.append(options.resolution)
        if self.always_forbidden or options.resolution == "1080p":
            raise RuntimeError("ERROR: unable to download video data: HTTP Error 403: Forbidden")
        height = int(options.resolution[:-1])
        progress_hook(
            {
                "status": "finished",
                "filename": f"{url}.mp4",
                "info_dict": {
                    "requested_formats": [
                        {
                            "format_id": "22",
                            "ext": "mp4",
                            "vcodec": "avc1.64001f",
                            "acodec": "none",
                            "width": int(height * 16 / 9),
                            "height": height,
                        },
                        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"},
                    ]
                },
            }
        )

    def resolution_from_progress_payload(self, payload):
        height = int(self.download_resolutions[-1][:-1])
        return (int(height * 16 / 9), height)

    def actual_format_from_progress_payload(self, payload):
        return "mp4 · avc1 + mp4a"


class EmptyAssertionFromHttp403Service(FakeYtDlpService):
    def extract_metadata(self, url, cookies_path=None):
        return AnalyzeResponse(
            url=url,
            title="Empty assertion 403",
            is_playlist=False,
            entries=[],
            formats=[FormatOption(format_id="137", label="1080p mp4", height=1080, ext="mp4")],
            subtitles=[],
            automatic_subtitles=[],
            ffmpeg={"ffmpeg": True, "ffprobe": True},
        )

    def download(self, url, options, progress_hook, should_cancel, cookies_path=None, download_dir=None):
        try:
            raise RuntimeError("ERROR: unable to download video data: HTTP Error 403: Forbidden")
        except RuntimeError as exc:
            raise AssertionError() from exc


class MediaStreamBlockedUntilLowerResolutionService(FakeYtDlpService):
    def __init__(self):
        super().__init__()
        self.download_resolutions: list[str] = []

    def extract_metadata(self, url, cookies_path=None):
        return AnalyzeResponse(
            url=url,
            title="Media stream fallback",
            is_playlist=False,
            entries=[],
            formats=[
                FormatOption(format_id="137", label="1080p mp4", height=1080, ext="mp4"),
                FormatOption(format_id="22", label="720p mp4", height=720, ext="mp4"),
                FormatOption(format_id="18", label="360p mp4", height=360, ext="mp4"),
            ],
            subtitles=[],
            automatic_subtitles=[],
            ffmpeg={"ffmpeg": True, "ffprobe": True},
        )

    def download(self, url, options, progress_hook, should_cancel, cookies_path=None, download_dir=None):
        self.download_resolutions.append(options.resolution)
        if options.resolution == "1080p":
            progress_hook(
                {
                    "status": "downloading",
                    "downloaded_bytes": 5_242_880,
                    "total_bytes": 10_485_760,
                }
            )
            raise RuntimeError("ERROR: unable to download video data: HTTP Error 403: Forbidden")
        if options.resolution == "720p":
            raise RuntimeError(
                "ERROR: [download] Got error: ('Connection aborted.', "
                "ConnectionResetError(10054, '远程主机强迫关闭了一个现有的连接。'))"
            )
        progress_hook(
            {
                "status": "finished",
                "filename": f"{url}.mp4",
                "info_dict": {"ext": "mp4", "vcodec": "avc1.42001E", "acodec": "mp4a.40.2", "width": 640, "height": 360},
            }
        )

    def resolution_from_progress_payload(self, payload):
        return (640, 360)

    def actual_format_from_progress_payload(self, payload):
        return "mp4 · avc1 + mp4a"

