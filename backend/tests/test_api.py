from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.config import AppSettings
from app.db import create_app_engine
from app.main import create_app
from app.models import Job, JobItem
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

    def download(self, url, options, progress_hook, should_cancel, cookies_path=None, download_dir=None):
        self.downloads.append({"url": url, "download_dir": download_dir})
        progress_hook({"status": "finished", "filename": f"{url}.mp4"})


def make_settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
        default_concurrency=1,
    )


def make_client(tmp_path: Path, service=None):
    settings = make_settings(tmp_path)
    return TestClient(create_app(settings=settings, ytdlp_service=service or FakeYtDlpService()))


def seed_job(tmp_path: Path, job_id: str, status: str = "queued") -> None:
    engine = create_app_engine(make_settings(tmp_path))
    with Session(engine) as session:
        session.add(
            Job(
                id=job_id,
                url=f"https://youtu.be/{job_id}",
                title=f"Job {job_id}",
                status=status,
                options_json="{}",
                total_items=1,
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
            )
        )
        session.commit()


def test_default_concurrency_uses_cpu_core_count(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("app.config.os.cpu_count", lambda: 12)

    settings = AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
    )

    assert settings.default_concurrency == 12


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


def test_diagnostics_returns_runtime_and_cookie_status(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.get("/api/diagnostics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["cookies_enabled"] is False
    assert payload["dependencies"]["ffmpeg"] is True
    assert payload["dependencies"]["js_runtime_name"] == "node"


def test_job_read_includes_realtime_progress_fields(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    seed_job(tmp_path, "job-progress", status="running")
    engine = create_app_engine(make_settings(tmp_path))
    with Session(engine) as session:
        item = session.get(JobItem, "job-progress-item")
        assert item is not None
        item.progress = 50.0
        item.downloaded_bytes = 5_242_880
        item.total_bytes = 10_485_760
        item.speed = 2048
        item.eta = 20
        session.add(item)
        session.commit()

    response = client.get("/api/jobs/job-progress")

    assert response.status_code == 200
    payload = response.json()
    for field in ["created_at", "updated_at", "started_at", "finished_at", "elapsed_seconds", "eta", "speed"]:
        assert field in payload
    for field in ["created_at", "updated_at", "started_at", "finished_at", "elapsed_seconds"]:
        assert field in payload["items"][0]
    assert payload["items"][0]["progress"] == 50.0
    assert payload["items"][0]["downloaded_bytes"] == 5_242_880
    assert payload["items"][0]["total_bytes"] == 10_485_760
    assert payload["items"][0]["speed"] == 2048
    assert payload["items"][0]["eta"] == 20


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
        json={"action": "delete", "job_ids": [first_id, second_id]},
    )
    assert delete_response.status_code == 200
    assert set(delete_response.json()["affected_job_ids"]) == {first_id, second_id}
    assert client.get(f"/api/jobs/{first_id}").status_code == 404
    assert client.get(f"/api/jobs/{second_id}").status_code == 404
