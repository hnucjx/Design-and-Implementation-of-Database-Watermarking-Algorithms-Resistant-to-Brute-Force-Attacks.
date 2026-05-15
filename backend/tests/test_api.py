from pathlib import Path

from fastapi.testclient import TestClient

from app.config import AppSettings
from app.main import create_app
from app.schemas import AnalyzeResponse, FormatOption, SubtitleOption, VideoEntry


class FakeYtDlpService:
    def __init__(self):
        self.downloads = []

    def get_ffmpeg_status(self):
        return {"ffmpeg": True, "ffprobe": True}

    def get_dependency_status(self):
        return {
            "ffmpeg": True,
            "ffprobe": True,
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
            formats=[FormatOption(format_id="18", label="360p mp4", height=360, ext="mp4")],
            subtitles=[],
            automatic_subtitles=[],
            ffmpeg={"ffmpeg": True, "ffprobe": True},
        )

    def download(self, url, options, progress_hook, should_cancel):
        progress_hook({"status": "finished", "filename": f"{url}.mp4"})


def make_client(tmp_path: Path):
    settings = AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
        default_concurrency=1,
    )
    return TestClient(create_app(settings=settings, ytdlp_service=FakeYtDlpService()))


def test_analyze_returns_video_metadata(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.post("/api/analyze", json={"url": "https://youtu.be/single"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["title"] == "Single"
    assert payload["formats"][0]["format_id"] == "18"
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


def test_settings_and_cookies_endpoints_do_not_expose_cookie_body(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    settings_response = client.put(
        "/api/settings",
        json={"download_dir": str(tmp_path / "custom"), "default_concurrency": 3},
    )
    assert settings_response.status_code == 200
    assert settings_response.json()["default_concurrency"] == 3

    cookie_response = client.post(
        "/api/cookies",
        files={"file": ("cookies.txt", b"SECRET_COOKIE_VALUE", "text/plain")},
    )

    assert cookie_response.status_code == 200
    payload = cookie_response.json()
    assert payload["enabled"] is True
    assert "SECRET_COOKIE_VALUE" not in str(payload)


def test_diagnostics_returns_runtime_and_cookie_status(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/api/diagnostics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["cookies_enabled"] is False
    assert payload["dependencies"]["ffmpeg"] is True
    assert payload["dependencies"]["js_runtime_name"] == "node"
