import queue
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.config import AppSettings
from app.db import create_app_engine, init_db
from app.main import create_app
from app.models import Job, JobItem, Setting
from app.schemas import AnalyzeResponse, DownloadOptions, FormatOption, SubtitleOption, VideoEntry
from app.ytdlp_service import BrowserCookieImportError, YtDlpService


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

    def resolution_from_progress_payload(self, payload):
        return None

    def actual_format_from_progress_payload(self, payload):
        return None

    def detect_file_resolution(self, file_path):
        return None


class BlockingYtDlpService(FakeYtDlpService):
    def __init__(self):
        super().__init__()
        self.started: queue.Queue[str] = queue.Queue()
        self.release = threading.Event()

    def download(self, url, options, progress_hook, should_cancel, cookies_path=None, download_dir=None):
        self.started.put(url)
        self.release.wait(timeout=5)
        progress_hook({"status": "finished", "filename": f"{url}.mp4"})


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


def make_settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
        default_concurrency=1,
    )


def make_client(tmp_path: Path, service=None, directory_picker=None):
    settings = make_settings(tmp_path)
    return TestClient(
        create_app(settings=settings, ytdlp_service=service or FakeYtDlpService(), directory_picker=directory_picker)
    )


def seed_job(
    tmp_path: Path,
    job_id: str,
    status: str = "queued",
    output_path: Path | None = None,
    download_dir: Path | None = None,
    options: DownloadOptions | None = None,
) -> None:
    engine = create_app_engine(make_settings(tmp_path))
    init_db(engine)
    with Session(engine) as session:
        session.add(
            Job(
                id=job_id,
                url=f"https://youtu.be/{job_id}",
                title=f"Job {job_id}",
                status=status,
                options_json=(options or DownloadOptions(mode="video_subtitles", resolution="1080p")).model_dump_json(),
                total_items=1,
                download_dir=str(download_dir or tmp_path / "downloads"),
            )
        )
        session.add(
            JobItem(
                id=f"{job_id}-item",
                job_id=job_id,
                source_url=f"https://youtu.be/{job_id}",
                title=f"Item {job_id}",
                index=1,
                status=status,
                output_path=str(output_path) if output_path else None,
            )
        )
        session.commit()


def wait_for_job_status(client: TestClient, job_id: str, status: str) -> dict:
    for _ in range(60):
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] == status:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not reach {status}")


def test_default_concurrency_uses_cpu_core_count(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("app.config.os.cpu_count", lambda: 12)

    settings = AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
    )

    assert settings.default_concurrency == 12


def test_saved_concurrency_is_loaded_before_workers_start(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    engine = create_app_engine(settings)
    init_db(engine)
    with Session(engine) as session:
        session.add(Setting(key="default_concurrency", value="2"))
        session.commit()
    service = BlockingYtDlpService()

    with TestClient(create_app(settings=settings, ytdlp_service=service)) as client:
        for url in ["https://youtu.be/first", "https://youtu.be/second"]:
            response = client.post(
                "/api/jobs",
                json={"url": url, "options": {"mode": "video_subtitles", "resolution": "720p"}},
            )
            assert response.status_code == 201

        started = {service.started.get(timeout=2), service.started.get(timeout=2)}
        service.release.set()

    assert started == {"https://youtu.be/first", "https://youtu.be/second"}


def test_updating_concurrency_starts_additional_worker_without_restart(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.default_concurrency = 1
    service = BlockingYtDlpService()

    with TestClient(create_app(settings=settings, ytdlp_service=service)) as client:
        for url in ["https://youtu.be/first", "https://youtu.be/second"]:
            response = client.post(
                "/api/jobs",
                json={"url": url, "options": {"mode": "video_subtitles", "resolution": "720p"}},
            )
            assert response.status_code == 201

        assert service.started.get(timeout=2) == "https://youtu.be/first"
        with pytest.raises(queue.Empty):
            service.started.get(timeout=0.2)

        update_response = client.put("/api/settings", json={"default_concurrency": 2})

        assert update_response.status_code == 200
        assert update_response.json()["default_concurrency"] == 2
        assert service.started.get(timeout=2) == "https://youtu.be/second"
        service.release.set()


def test_analyze_returns_video_metadata(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.post("/api/analyze", json={"url": "https://youtu.be/single"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "Single"
    assert payload["formats"][0]["format_id"] == "22"
    assert payload["ffmpeg"] == {"ffmpeg": True, "ffprobe": True}


def test_create_playlist_job_filters_selected_entries(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.post(
        "/api/jobs",
        json={
            "url": "https://youtube.com/playlist?list=abc",
            "options": {
                "mode": "video_subtitles",
                "resolution": "720p",
                "playlist_items": [2],
            },
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["total_items"] == 1
    assert payload["items"][0]["title"] == "Two"
    assert payload["download_dir"] == str(tmp_path / "downloads" / "Batch")


def test_create_single_video_job_uses_root_download_dir(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.post(
        "/api/jobs",
        json={
            "url": "https://youtu.be/single",
            "options": {"mode": "video_subtitles", "resolution": "720p"},
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["title"] == "Single"
    assert payload["download_dir"] == str(tmp_path / "downloads")


def test_select_download_directory_updates_root_and_playlist_subfolder(tmp_path: Path) -> None:
    selected_dir = tmp_path / "custom videos"
    client = make_client(tmp_path, directory_picker=lambda current: selected_dir)

    settings_response = client.post("/api/settings/download-dir/select")

    assert settings_response.status_code == 200
    assert settings_response.json()["download_dir"] == str(selected_dir)
    assert selected_dir.exists()

    job_response = client.post(
        "/api/jobs",
        json={
            "url": "https://youtube.com/playlist?list=abc",
            "options": {
                "mode": "video_subtitles",
                "resolution": "720p",
                "playlist_items": [1, 2],
            },
        },
    )

    assert job_response.status_code == 201
    assert job_response.json()["download_dir"] == str(selected_dir / "Batch")


def test_settings_and_cookies_endpoints_do_not_expose_cookie_body(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    settings_response = client.put(
        "/api/settings",
        json={"download_dir": str(tmp_path / "custom"), "default_concurrency": 12},
    )
    assert settings_response.status_code == 200
    assert settings_response.json()["default_concurrency"] == 12
    assert settings_response.json()["default_resolution"] == "1080p"

    cookie_response = client.post(
        "/api/cookies",
        files={"file": ("cookies.txt", b"SECRET_COOKIE_VALUE", "text/plain")},
    )

    assert cookie_response.status_code == 200
    payload = cookie_response.json()
    assert payload["enabled"] is True
    assert "SECRET_COOKIE_VALUE" not in str(payload)


def test_import_browser_cookies_endpoint_does_not_expose_cookie_body(tmp_path: Path) -> None:
    service = BrowserImportYtDlpService()
    client = make_client(tmp_path, service=service)

    response = client.post("/api/cookies/from-browser", json={"browser": "auto"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["source"] == "browser"
    assert payload["browser"] == "edge"
    assert payload["imported_count"] == 4
    assert "SECRET_COOKIE_VALUE" not in str(payload)
    assert service.imports == ["auto"]
    assert (tmp_path / "data" / "cookies.txt").exists()


def test_import_browser_cookies_endpoint_reports_locked_edge_database(tmp_path: Path) -> None:
    service = LockedEdgeBrowserImportService()
    client = make_client(tmp_path, service=service)

    response = client.post("/api/cookies/from-browser", json={"browser": "edge"})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "browser_locked"
    assert detail["browser"] == "edge"
    assert "Edge" in detail["message"]
    assert "SECRET_COOKIE_VALUE" not in str(detail)
    assert service.imports == [("edge", False)]


def test_import_browser_cookies_endpoint_closes_edge_when_confirmed(tmp_path: Path) -> None:
    service = LockedEdgeBrowserImportService()
    client = make_client(tmp_path, service=service)

    response = client.post(
        "/api/cookies/from-browser",
        json={"browser": "edge", "close_browser_if_locked": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["source"] == "browser"
    assert payload["browser"] == "edge"
    assert payload["imported_count"] == 4
    assert "SECRET_COOKIE_VALUE" not in str(payload)
    assert service.imports == [("edge", True)]
    assert (tmp_path / "data" / "cookies.txt").exists()


def test_analyze_auto_imports_browser_cookies_for_playlist_bot_challenge(tmp_path: Path) -> None:
    service = BotChallengeYtDlpService()
    client = make_client(tmp_path, service=service)

    response = client.post("/api/analyze", json={"url": "https://youtube.com/playlist?list=abc"})

    assert response.status_code == 200
    assert response.json()["title"] == "Batch"
    assert service.imports == ["auto"]
    assert service.extract_calls[0] is None
    assert service.extract_calls[1] == tmp_path / "data" / "cookies.txt"


def test_analyze_returns_structured_locked_edge_cookie_error(tmp_path: Path) -> None:
    service = LockedEdgeBrowserImportService()
    client = make_client(tmp_path, service=service)

    response = client.post("/api/analyze", json={"url": "https://youtube.com/playlist?list=abc"})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "browser_locked"
    assert detail["browser"] == "edge"
    assert "Edge" in detail["message"]
    assert service.imports == [("auto", False)]


def test_job_download_refreshes_browser_cookies_and_retries_bot_challenge(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.ensure_directories()
    settings.cookies_path.write_text("STALE_COOKIE_VALUE", encoding="utf-8")
    service = DownloadBotChallengeYtDlpService()

    with TestClient(create_app(settings=settings, ytdlp_service=service)) as client:
        response = client.post(
            "/api/jobs",
            json={
                "url": "https://youtu.be/G9MxNwUoSt0",
                "options": {"mode": "video_subtitles", "resolution": "720p"},
            },
        )
        assert response.status_code == 201
        payload = wait_for_job_status(client, response.json()["id"], "succeeded")

    assert payload["items"][0]["status"] == "succeeded"
    assert service.imports == ["auto"]
    assert service.cookie_snapshots == ["STALE_COOKIE_VALUE", "SECRET_COOKIE_VALUE"]


def test_job_download_recovers_when_anti403_profile_succeeds(tmp_path: Path) -> None:
    service = DownloadHttp403ThenAnti403SuccessService()

    with TestClient(create_app(settings=make_settings(tmp_path), ytdlp_service=service)) as client:
        response = client.post(
            "/api/jobs",
            json={
                "url": "https://youtu.be/http403",
                "options": {"mode": "video_subtitles", "resolution": "720p"},
            },
        )
        assert response.status_code == 201
        payload = wait_for_job_status(client, response.json()["id"], "succeeded")

    assert service.profile_attempts == ["default", "anti403"]
    assert payload["items"][0]["status"] == "succeeded"
    assert payload["items"][0]["actual_width"] == 1280
    assert payload["items"][0]["actual_height"] == 720
    assert payload["items"][0]["actual_format"] == "mp4 · avc1 + mp4a"


def test_job_download_suggests_lower_resolution_after_http_403_without_auto_restart(tmp_path: Path) -> None:
    service = DownloadHttp403FallbackResolutionService()

    with TestClient(create_app(settings=make_settings(tmp_path), ytdlp_service=service)) as client:
        response = client.post(
            "/api/jobs",
            json={
                "url": "https://youtu.be/http403",
                "options": {"mode": "video_subtitles", "resolution": "1080p"},
            },
        )
        assert response.status_code == 201
        payload = wait_for_job_status(client, response.json()["id"], "failed")

    item = payload["items"][0]
    assert service.download_resolutions == ["1080p"]
    assert item["status"] == "failed"
    assert item["requested_resolution"] == "1080p"
    assert item["fallback_resolution"] == "720p"
    assert item["resolution_fallback"]["message"] == "当前下载未能在 1080p 下完成，可尝试以 720p 重启。"
    assert item["actual_width"] is None
    assert item["actual_height"] is None


def test_job_download_reports_clear_error_when_all_http_403_retries_fail(tmp_path: Path) -> None:
    service = DownloadHttp403FallbackResolutionService(always_forbidden=True)

    with TestClient(create_app(settings=make_settings(tmp_path), ytdlp_service=service)) as client:
        response = client.post(
            "/api/jobs",
            json={
                "url": "https://youtu.be/http403",
                "options": {"mode": "video_subtitles", "resolution": "1080p"},
            },
        )
        assert response.status_code == 201
        payload = wait_for_job_status(client, response.json()["id"], "failed")

    item = payload["items"][0]
    assert service.download_resolutions == ["1080p"]
    assert "YouTube 拒绝了媒体流下载（HTTP 403）" in item["error"]
    assert "浏览器 impersonation" in item["error"]
    assert "重新导入 cookies" in item["error"]
    assert item["requested_resolution"] == "1080p"
    assert item["fallback_resolution"] == "720p"


def test_job_download_does_not_store_empty_error_when_http_403_is_wrapped(tmp_path: Path) -> None:
    service = EmptyAssertionFromHttp403Service()

    with TestClient(create_app(settings=make_settings(tmp_path), ytdlp_service=service)) as client:
        response = client.post(
            "/api/jobs",
            json={
                "url": "https://youtu.be/wrapped403",
                "options": {"mode": "video_subtitles", "resolution": "1080p"},
            },
        )
        assert response.status_code == 201
        payload = wait_for_job_status(client, response.json()["id"], "failed")

    item = payload["items"][0]
    assert item["error"]
    assert "YouTube 拒绝了媒体流下载（HTTP 403）" in item["error"]


def test_job_download_does_not_auto_downgrade_after_mid_download_failure(tmp_path: Path) -> None:
    service = MediaStreamBlockedUntilLowerResolutionService()

    with TestClient(create_app(settings=make_settings(tmp_path), ytdlp_service=service)) as client:
        response = client.post(
            "/api/jobs",
            json={
                "url": "https://youtu.be/reset-until-lower",
                "options": {"mode": "video_subtitles", "resolution": "1080p"},
            },
        )
        assert response.status_code == 201
        payload = wait_for_job_status(client, response.json()["id"], "failed")

    item = payload["items"][0]
    assert service.download_resolutions == ["1080p"]
    assert item["status"] == "failed"
    assert item["downloaded_bytes"] == 5_242_880
    assert item["total_bytes"] == 10_485_760
    assert item["requested_resolution"] == "1080p"
    assert item["fallback_resolution"] == "720p"
    assert item["resolution_fallback"]["message"] == "当前下载未能在 1080p 下完成，可尝试以 720p 重启。"
    assert item["actual_width"] is None
    assert item["actual_height"] is None


def test_diagnostics_returns_runtime_and_cookie_status(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/api/diagnostics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["cookies_enabled"] is False
    assert payload["dependencies"]["ffmpeg"] is True
    assert payload["dependencies"]["js_runtime_name"] == "node"
    assert payload["dependencies"]["impersonation_available"] is True
    assert payload["dependencies"]["impersonation_targets"] == ["safari"]


def test_diagnostics_returns_impersonation_status_from_real_service(monkeypatch, tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr(service, "_available_impersonation_targets", lambda: ["safari", "chrome"])
    client = make_client(tmp_path, service=service)

    response = client.get("/api/diagnostics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["dependencies"]["impersonation_available"] is True
    assert payload["dependencies"]["impersonation_targets"] == ["safari", "chrome"]


def test_job_read_includes_realtime_progress_fields(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    seed_job(tmp_path, "job-progress", status="running")
    engine = create_app_engine(make_settings(tmp_path))
    with Session(engine) as session:
        job = session.get(Job, "job-progress")
        assert job is not None
        job.started_at = datetime(2026, 5, 15, 10, 0, tzinfo=UTC)
        job.finished_at = datetime(2026, 5, 15, 10, 1, tzinfo=UTC)
        session.add(job)
        item = session.get(JobItem, "job-progress-item")
        assert item is not None
        item.progress = 50.0
        item.downloaded_bytes = 5_242_880
        item.total_bytes = 10_485_760
        item.speed = 2048
        item.eta = 20
        item.started_at = datetime(2026, 5, 15, 10, 0, tzinfo=UTC)
        item.finished_at = datetime(2026, 5, 15, 10, 1, tzinfo=UTC)
        item.actual_width = 1920
        item.actual_height = 1080
        item.actual_format = "mp4 · avc1 + mp4a"
        session.add(item)
        session.commit()

    response = client.get("/api/jobs/job-progress")

    assert response.status_code == 200
    payload = response.json()
    for field in [
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "elapsed_seconds",
        "eta",
        "speed",
        "actual_resolution",
        "actual_format",
    ]:
        assert field in payload
    for field in [
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "elapsed_seconds",
        "actual_width",
        "actual_height",
        "actual_format",
    ]:
        assert field in payload["items"][0]
    assert payload["actual_resolution"] == "1920x1080"
    assert payload["actual_format"] == "mp4 · avc1 + mp4a"
    assert payload["items"][0]["progress"] == 50.0
    assert payload["items"][0]["downloaded_bytes"] == 5_242_880
    assert payload["items"][0]["total_bytes"] == 10_485_760
    assert payload["items"][0]["speed"] == 2048
    assert payload["items"][0]["eta"] == 20
    assert payload["items"][0]["actual_width"] == 1920
    assert payload["items"][0]["actual_height"] == 1080
    assert payload["items"][0]["actual_format"] == "mp4 · avc1 + mp4a"


def test_playlist_job_read_reports_mixed_actual_resolution(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    engine = create_app_engine(make_settings(tmp_path))
    with Session(engine) as session:
        session.add(
            Job(
                id="job-mixed",
                url="https://youtube.com/playlist?list=mixed",
                title="Mixed playlist",
                status="succeeded",
                options_json="{}",
                total_items=2,
                completed_items=2,
                progress=100.0,
                download_dir=str(tmp_path / "downloads" / "Mixed playlist"),
            )
        )
        session.add(
            JobItem(
                id="mixed-1080",
                job_id="job-mixed",
                source_url="https://youtu.be/1080",
                title="1080p item",
                index=1,
                status="succeeded",
                progress=100.0,
                actual_width=1920,
                actual_height=1080,
                actual_format="mp4 · avc1 + mp4a",
            )
        )
        session.add(
            JobItem(
                id="mixed-720",
                job_id="job-mixed",
                source_url="https://youtu.be/720",
                title="720p item",
                index=2,
                status="succeeded",
                progress=100.0,
                actual_width=1280,
                actual_height=720,
                actual_format="webm · vp9 + opus",
            )
        )
        session.commit()

    response = client.get("/api/jobs/job-mixed")

    assert response.status_code == 200
    payload = response.json()
    assert payload["actual_resolution"] == "混合分辨率"
    assert payload["actual_format"] == "混合格式"


def test_playlist_download_auto_falls_back_to_highest_lower_resolution(tmp_path: Path) -> None:
    service = AutoFallbackYtDlpService()

    with TestClient(create_app(settings=make_settings(tmp_path), ytdlp_service=service)) as client:
        response = client.post(
            "/api/jobs",
            json={
                "url": "https://youtube.com/playlist?list=abc",
                "options": {
                    "mode": "video_subtitles",
                    "resolution": "1080p",
                    "playlist_items": [1, 2],
                },
            },
        )
        assert response.status_code == 201
        payload = wait_for_job_status(client, response.json()["id"], "succeeded")

    assert [option.resolution for option in service.download_options] == ["1080p", "720p"]
    first, second = payload["items"]
    assert first["status"] == "succeeded"
    assert first["actual_format"] == "mp4 · avc1 + mp4a"
    assert second["status"] == "succeeded"
    assert second["requested_resolution"] == "1080p"
    assert second["fallback_resolution"] == "720p"
    assert second["resolution_fallback"]["message"] == "已从 1080p 自动降级到 720p。"
    assert second["actual_width"] == 1280
    assert second["actual_height"] == 720
    assert second["actual_format"] == "mp4 · avc1 + mp4a"


def test_single_download_auto_falls_back_to_highest_lower_resolution(tmp_path: Path) -> None:
    service = SingleAutoFallbackYtDlpService()

    with TestClient(create_app(settings=make_settings(tmp_path), ytdlp_service=service)) as client:
        response = client.post(
            "/api/jobs",
            json={
                "url": "https://youtu.be/unsupported",
                "options": {"mode": "video_subtitles", "resolution": "1080p"},
            },
        )
        assert response.status_code == 201
        payload = wait_for_job_status(client, response.json()["id"], "succeeded")

    item = payload["items"][0]
    assert [option.resolution for option in service.download_options] == ["720p"]
    assert item["requested_resolution"] == "1080p"
    assert item["fallback_resolution"] == "720p"
    assert item["resolution_fallback"] == {
        "requested_resolution": "1080p",
        "fallback_resolution": "720p",
        "message": "已从 1080p 自动降级到 720p。",
    }
    assert payload["resolution_fallback"] == item["resolution_fallback"]
    assert item["error"] is None
    assert item["actual_width"] == 1280
    assert item["actual_height"] == 720
    assert item["actual_format"] == "mp4 · avc1 + mp4a"


def test_unavailable_resolution_without_lower_format_fails_with_clear_error(tmp_path: Path) -> None:
    service = NoLowerResolutionYtDlpService()

    with TestClient(create_app(settings=make_settings(tmp_path), ytdlp_service=service)) as client:
        response = client.post(
            "/api/jobs",
            json={
                "url": "https://youtu.be/no-lower",
                "options": {"mode": "video_subtitles", "resolution": "1080p"},
            },
        )
        assert response.status_code == 201
        payload = wait_for_job_status(client, response.json()["id"], "failed")

    item = payload["items"][0]
    assert item["status"] == "failed"
    assert item["error"] == "当前没有 1080p 的视频，也没有 720p 或更高的可用降级清晰度。"
    assert service.download_called is False


def test_restart_job_with_resolution_updates_job_options(tmp_path: Path) -> None:
    service = BlockingYtDlpService()
    seed_job(tmp_path, "job-resolution", status="failed", options=DownloadOptions(resolution="1080p"))

    with TestClient(create_app(settings=make_settings(tmp_path), ytdlp_service=service)) as client:
        response = client.post("/api/jobs/job-resolution/restart", json={"resolution": "720p"})
        service.release.set()

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "queued"
    engine = create_app_engine(make_settings(tmp_path))
    with Session(engine) as session:
        job = session.get(Job, "job-resolution")
        assert job is not None
        options = DownloadOptions.model_validate_json(job.options_json)
        assert options.resolution == "720p"
        assert options.format_id is None
        item = session.get(JobItem, "job-resolution-item")
        assert item is not None
        assert item.options_json is None
        assert item.requested_resolution is None
        assert item.fallback_resolution is None


def test_restart_playlist_item_with_resolution_only_updates_item_options(tmp_path: Path) -> None:
    service = BlockingYtDlpService()
    settings = make_settings(tmp_path)
    engine = create_app_engine(settings)
    init_db(engine)
    with Session(engine) as session:
        session.add(
            Job(
                id="job-playlist-resolution",
                url="https://youtube.com/playlist?list=abc",
                title="Playlist",
                status="failed",
                options_json=DownloadOptions(resolution="1080p").model_dump_json(),
                total_items=2,
                failed_items=1,
                download_dir=str(tmp_path / "downloads" / "Playlist"),
            )
        )
        session.add(
            JobItem(
                id="item-1",
                job_id="job-playlist-resolution",
                source_url="https://youtu.be/one",
                title="One",
                index=1,
                status="succeeded",
                progress=100.0,
            )
        )
        session.add(
            JobItem(
                id="item-2",
                job_id="job-playlist-resolution",
                source_url="https://youtu.be/two",
                title="Two",
                index=2,
                status="failed",
                progress=0.0,
                requested_resolution="1080p",
                fallback_resolution="720p",
                error="当前没有 1080p 的视频，低于选定分辨率的最高可用分辨率是 720p。",
            )
        )
        session.commit()

    with TestClient(create_app(settings=settings, ytdlp_service=service)) as client:
        response = client.post("/api/jobs/job-playlist-resolution/items/item-2/restart", json={"resolution": "720p"})
        service.release.set()

    assert response.status_code == 200
    engine = create_app_engine(settings)
    with Session(engine) as session:
        job = session.get(Job, "job-playlist-resolution")
        assert job is not None
        assert DownloadOptions.model_validate_json(job.options_json).resolution == "1080p"
        item = session.get(JobItem, "item-2")
        assert item is not None
        assert item.options_json is not None
        assert DownloadOptions.model_validate_json(item.options_json).resolution == "720p"
        assert item.requested_resolution is None
        assert item.fallback_resolution is None


def test_job_can_be_paused_restarted_and_deleted(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    job_id = "job-single"
    seed_job(tmp_path, job_id)

    pause_response = client.post(f"/api/jobs/{job_id}/pause")
    assert pause_response.status_code == 200
    paused = client.get(f"/api/jobs/{job_id}").json()
    assert paused["status"] == "paused"
    assert paused["items"][0]["status"] == "paused"

    restart_response = client.post(f"/api/jobs/{job_id}/restart")
    assert restart_response.status_code == 200
    assert restart_response.json()["status"] == "queued"

    delete_response = client.delete(f"/api/jobs/{job_id}")
    assert delete_response.status_code == 204
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_playlist_item_can_be_restarted_individually(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    engine = create_app_engine(make_settings(tmp_path))
    with Session(engine) as session:
        session.add(
            Job(
                id="job-playlist",
                url="https://youtube.com/playlist?list=abc",
                title="Playlist",
                status="failed",
                options_json="{}",
                total_items=2,
                completed_items=1,
                failed_items=1,
                progress=50.0,
                download_dir=str(tmp_path / "downloads" / "Playlist"),
            )
        )
        session.add(
            JobItem(
                id="item-done",
                job_id="job-playlist",
                source_url="https://youtu.be/done",
                title="Done",
                index=1,
                status="succeeded",
                progress=100.0,
                output_path=str(tmp_path / "downloads" / "Playlist" / "done.mp4"),
            )
        )
        session.add(
            JobItem(
                id="item-failed",
                job_id="job-playlist",
                source_url="https://youtu.be/failed",
                title="Failed",
                index=2,
                status="failed",
                progress=42.0,
                downloaded_bytes=42,
                total_bytes=100,
                speed=2048,
                eta=10,
                output_path=str(tmp_path / "downloads" / "Playlist" / "failed.mp4"),
                error="boom",
            )
        )
        session.commit()

    response = client.post("/api/jobs/job-playlist/items/item-failed/restart")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["completed_items"] == 1
    assert payload["failed_items"] == 0
    restarted = next(item for item in payload["items"] if item["id"] == "item-failed")
    assert restarted["status"] == "queued"
    assert restarted["progress"] == 0.0
    assert restarted["downloaded_bytes"] is None
    assert restarted["total_bytes"] is None
    assert restarted["speed"] is None
    assert restarted["eta"] is None
    assert restarted["output_path"] is None
    assert restarted["error"] is None
    preserved = next(item for item in payload["items"] if item["id"] == "item-done")
    assert preserved["status"] == "succeeded"


def test_delete_job_keeps_downloaded_file_by_default(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    output_file = tmp_path / "downloads" / "video.mp4"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("video", encoding="utf-8")
    seed_job(tmp_path, "job-file-keep", status="succeeded", output_path=output_file)

    response = client.delete("/api/jobs/job-file-keep")

    assert response.status_code == 204
    assert output_file.exists()


def test_delete_job_can_delete_downloaded_file_and_empty_playlist_folder(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    playlist_dir = tmp_path / "downloads" / "Course"
    output_file = playlist_dir / "video.mp4"
    playlist_dir.mkdir(parents=True)
    output_file.write_text("video", encoding="utf-8")
    seed_job(
        tmp_path,
        "job-file-delete",
        status="succeeded",
        output_path=output_file,
        download_dir=playlist_dir,
    )

    response = client.delete("/api/jobs/job-file-delete?delete_files=true")

    assert response.status_code == 204
    assert not output_file.exists()
    assert not playlist_dir.exists()


def test_batch_job_actions_pause_restart_and_delete_multiple_jobs(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    first_id = "job-first"
    second_id = "job-second"
    seed_job(tmp_path, first_id)
    seed_job(tmp_path, second_id)

    pause_response = client.post(
        "/api/jobs/batch",
        json={"action": "pause", "job_ids": [first_id, second_id]},
    )
    assert pause_response.status_code == 200
    assert set(pause_response.json()["affected_job_ids"]) == {first_id, second_id}
    assert client.get(f"/api/jobs/{first_id}").json()["status"] == "paused"
    assert client.get(f"/api/jobs/{second_id}").json()["status"] == "paused"

    restart_response = client.post(
        "/api/jobs/batch",
        json={"action": "restart", "job_ids": [first_id, second_id]},
    )
    assert restart_response.status_code == 200
    assert set(restart_response.json()["affected_job_ids"]) == {first_id, second_id}

    delete_response = client.post(
        "/api/jobs/batch",
        json={"action": "delete", "job_ids": [first_id, second_id], "delete_files": True},
    )
    assert delete_response.status_code == 200
    assert set(delete_response.json()["affected_job_ids"]) == {first_id, second_id}
    assert client.get(f"/api/jobs/{first_id}").status_code == 404
    assert client.get(f"/api/jobs/{second_id}").status_code == 404
