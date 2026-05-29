import queue
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session

from app.config import AppSettings
from app.db import create_app_engine, init_db
from app.main import create_app
from app.models import Job, JobItem, Setting
from app.schemas import DownloadOptions, FormatOption
from app.transfer_stats import TransferStats
from app.ytdlp_service import YtDlpService

from fakes import (
    AverageSpeedYtDlpService,
    AutoFallbackYtDlpService,
    BlockingYtDlpService,
    BotChallengeYtDlpService,
    BrowserImportYtDlpService,
    DownloadBotChallengeYtDlpService,
    DownloadHttp403FallbackResolutionService,
    DownloadHttp403ThenAnti403SuccessService,
    EmptyAssertionFromHttp403Service,
    FakeYtDlpService,
    LockedEdgeBrowserImportService,
    LowOnlyFallbackYtDlpService,
    MediaStreamBlockedUntilLowerResolutionService,
    PreparedBlockingYtDlpService,
    SingleAutoFallbackYtDlpService,
    SplitStreamProgressYtDlpService,
    UnselectableHighWithSafeFallbackService,
    UnselectableHighWithoutSafeFallbackService,
)


def make_settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
        default_concurrency=1,
    )


def make_client(tmp_path: Path, service=None, directory_picker=None, system_opener=None):
    settings = make_settings(tmp_path)
    return TestClient(
        create_app(
            settings=settings,
            ytdlp_service=service or FakeYtDlpService(),
            directory_picker=directory_picker,
            system_opener=system_opener,
        )
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


def test_job_item_shows_predetected_resolution_and_format_while_downloading(tmp_path: Path) -> None:
    service = PreparedBlockingYtDlpService()

    with TestClient(create_app(settings=make_settings(tmp_path), ytdlp_service=service)) as client:
        response = client.post(
            "/api/jobs",
            json={"url": "https://youtu.be/prepared", "options": {"mode": "video_subtitles", "resolution": "1080p"}},
        )
        assert response.status_code == 201
        job_id = response.json()["id"]
        assert service.started.get(timeout=2) == "https://youtu.be/prepared"
        try:
            for _ in range(20):
                payload = client.get(f"/api/jobs/{job_id}").json()
                item = payload["items"][0]
                if item["actual_width"] == 1920:
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("pre-detected item metadata was not published while download was running")

            assert payload["status"] == "running"
            assert payload["actual_resolution"] == "1920x1080"
            assert payload["actual_format"] == "mp4 · avc1 + mp4a"
            assert item["actual_width"] == 1920
            assert item["actual_height"] == 1080
            assert item["actual_format"] == "mp4 · avc1 + mp4a"
            assert item["total_bytes"] == 10_485_760
        finally:
            service.release.set()


def test_create_app_ignores_incomplete_frontend_dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.main as main_module

    frontend_dist = tmp_path / "frontend" / "dist"
    frontend_dist.mkdir(parents=True)
    (frontend_dist / "index.html").write_text("<div id=\"root\"></div>", encoding="utf-8")
    monkeypatch.setattr(main_module, "REPO_ROOT", tmp_path)

    with TestClient(main_module.create_app(settings=make_settings(tmp_path), ytdlp_service=FakeYtDlpService())) as client:
        assert client.get("/api/settings").status_code == 200


def test_default_concurrency_is_five(tmp_path: Path) -> None:
    settings = AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
    )

    assert settings.default_concurrency == 5
    assert settings.youtube_max_parallel_downloads == 5


def test_youtube_max_parallel_downloads_env_sets_initial_concurrency(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("YTDL_YOUTUBE_MAX_PARALLEL_DOWNLOADS", "3")

    settings = AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
    )

    assert settings.default_concurrency == 3
    assert settings.youtube_max_parallel_downloads == 3


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
    assert settings_response.json()["default_resolution"] == "1440p"

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
    assert item["fallback_reason"] == "media_stream_blocked"
    assert item["resolution_fallback"] == {
        "requested_resolution": "1080p",
        "fallback_resolution": "720p",
        "reason": "media_stream_blocked",
        "restart_resolution": "720p",
        "message": "当前 1080p 媒体流下载被 YouTube 拒绝或连接重置，可尝试以 720p 重启。",
    }
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
    assert item["fallback_reason"] == "media_stream_blocked"


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
    assert item["fallback_reason"] == "media_stream_blocked"
    assert item["resolution_fallback"]["restart_resolution"] == "720p"
    assert item["resolution_fallback"]["message"] == "当前 1080p 媒体流下载被 YouTube 拒绝或连接重置，可尝试以 720p 重启。"
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
    assert payload["dependencies"]["po_token_provider_available"] is True
    assert payload["dependencies"]["po_token_provider"] == "yt-dlp-getpot-wpc"
    assert payload["dependencies"]["youtube_po_browser_path_configured"] is False
    assert payload["dependencies"]["youtube_po_token_configured"] is False
    assert payload["dependencies"]["youtube_visitor_data_configured"] is False
    assert payload["dependencies"]["youtube_max_parallel_downloads"] == 5
    assert payload["dependencies"]["anti403_http_chunk_size_mb"] == 16
    assert payload["dependencies"]["throttled_rate_kbps"] == 64
    assert payload["dependencies"]["aria2c_enabled"] is False
    assert payload["dependencies"]["aria2c_connections"] == 1
    assert "aria2c_available" in payload["dependencies"]


def test_diagnostics_returns_impersonation_status_from_real_service(monkeypatch, tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr(service, "_available_impersonation_targets", lambda: ["safari", "chrome"])
    client = make_client(tmp_path, service=service)

    response = client.get("/api/diagnostics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["dependencies"]["impersonation_available"] is True
    assert payload["dependencies"]["impersonation_targets"] == ["safari", "chrome"]


def test_init_db_adds_fallback_reason_to_existing_jobitem_table(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    engine = create_app_engine(settings)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE jobitem ("
                "id TEXT PRIMARY KEY, job_id TEXT, source_url TEXT, title TEXT, "
                "status TEXT, progress FLOAT, created_at DATETIME, updated_at DATETIME"
                ")"
            )
        )

    init_db(engine)

    with engine.begin() as connection:
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(jobitem)"))}
    assert "fallback_reason" in columns


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


def test_finished_download_keeps_average_speed(tmp_path: Path, monkeypatch) -> None:
    service = AverageSpeedYtDlpService()
    times = iter([10.0, 11.0, 13.0])
    monkeypatch.setattr("app.job_manager.TransferStats", lambda: TransferStats(clock=lambda: next(times)))

    with TestClient(create_app(settings=make_settings(tmp_path), ytdlp_service=service)) as client:
        response = client.post(
            "/api/jobs",
            json={
                "url": "https://youtu.be/average-speed",
                "options": {"mode": "video_subtitles", "resolution": "720p"},
            },
        )
        assert response.status_code == 201
        payload = wait_for_job_status(client, response.json()["id"], "succeeded")

    expected_speed = 8192 / 3
    assert payload["speed"] == expected_speed
    assert payload["items"][0]["speed"] == expected_speed


def test_split_stream_download_progress_does_not_look_like_restart(tmp_path: Path) -> None:
    service = SplitStreamProgressYtDlpService()

    with TestClient(create_app(settings=make_settings(tmp_path), ytdlp_service=service)) as client:
        response = client.post(
            "/api/jobs",
            json={
                "url": "https://youtu.be/split-stream",
                "options": {"mode": "video_subtitles", "resolution": "1080p"},
            },
        )
        assert response.status_code == 201
        job_id = response.json()["id"]

        assert service.stages.get(timeout=2) == "video_started"
        service.continue_download()
        assert service.stages.get(timeout=2) == "video_finished"
        after_video = client.get(f"/api/jobs/{job_id}").json()["items"][0]
        assert after_video["status"] == "running"
        assert 0 < after_video["progress"] < 100
        assert after_video["downloaded_bytes"] == 100
        assert after_video["total_bytes"] == 100

        service.continue_download()
        assert service.stages.get(timeout=2) == "audio_started"
        after_audio_started = client.get(f"/api/jobs/{job_id}").json()["items"][0]
        assert after_audio_started["status"] == "running"
        assert after_audio_started["progress"] >= after_video["progress"]
        assert after_audio_started["downloaded_bytes"] >= after_video["downloaded_bytes"]
        assert after_audio_started["total_bytes"] >= after_video["total_bytes"]

        service.continue_download()
        assert service.stages.get(timeout=2) == "audio_finished"
        payload = wait_for_job_status(client, job_id, "succeeded")

    item = payload["items"][0]
    assert item["progress"] == 100
    assert item["downloaded_bytes"] == 120
    assert item["total_bytes"] == 120
    assert item["speed"] is not None
    assert item["output_path"].endswith("video.mp4")


def test_single_video_failed_job_surfaces_item_error(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    seed_job(tmp_path, "job-single-failed", status="failed")
    engine = create_app_engine(make_settings(tmp_path))
    with Session(engine) as session:
        job = session.get(Job, "job-single-failed")
        assert job is not None
        job.failed_items = 1
        job.error = "1 item(s) failed."
        session.add(job)
        item = session.get(JobItem, "job-single-failed-item")
        assert item is not None
        item.status = "failed"
        item.error = "YouTube 媒体流连接中断，请重新导入 cookies 后重试。"
        session.add(item)
        session.commit()

    response = client.get("/api/jobs/job-single-failed")

    assert response.status_code == 200
    assert response.json()["error"] == "YouTube 媒体流连接中断，请重新导入 cookies 后重试。"


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
    assert second["fallback_reason"] == "requested_resolution_missing"
    assert second["resolution_fallback"] == {
        "requested_resolution": "1080p",
        "fallback_resolution": "720p",
        "reason": "requested_resolution_missing",
        "restart_resolution": None,
        "message": "视频本来没有 1080p，已自动降级到 720p。",
    }
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
    assert item["fallback_reason"] == "requested_resolution_missing"
    assert item["resolution_fallback"] == {
        "requested_resolution": "1080p",
        "fallback_resolution": "720p",
        "reason": "requested_resolution_missing",
        "restart_resolution": None,
        "message": "视频本来没有 1080p，已自动降级到 720p。",
    }
    assert payload["resolution_fallback"] == item["resolution_fallback"]
    assert item["error"] is None
    assert item["actual_width"] == 1280
    assert item["actual_height"] == 720
    assert item["actual_format"] == "mp4 · avc1 + mp4a"


def test_low_source_can_auto_fallback_below_720_with_clear_reason(tmp_path: Path) -> None:
    service = LowOnlyFallbackYtDlpService()

    with TestClient(create_app(settings=make_settings(tmp_path), ytdlp_service=service)) as client:
        response = client.post(
            "/api/jobs",
            json={
                "url": "https://youtu.be/low-source",
                "options": {"mode": "video_subtitles", "resolution": "1080p"},
            },
        )
        assert response.status_code == 201
        payload = wait_for_job_status(client, response.json()["id"], "succeeded")

    item = payload["items"][0]
    assert [option.resolution for option in service.download_options] == ["360p"]
    assert item["status"] == "succeeded"
    assert item["requested_resolution"] == "1080p"
    assert item["fallback_resolution"] == "360p"
    assert item["fallback_reason"] == "source_below_720_only"
    assert item["resolution_fallback"] == {
        "requested_resolution": "1080p",
        "fallback_resolution": "360p",
        "reason": "source_below_720_only",
        "restart_resolution": None,
        "message": "视频本身没有 720p 或更高清晰度，已自动降级到最高可用的 360p。",
    }
    assert item["actual_width"] == 640
    assert item["actual_height"] == 360


def test_existing_high_resolution_does_not_auto_fallback_below_720(tmp_path: Path) -> None:
    service = UnselectableHighWithoutSafeFallbackService()

    with TestClient(create_app(settings=make_settings(tmp_path), ytdlp_service=service)) as client:
        response = client.post(
            "/api/jobs",
            json={
                "url": "https://youtu.be/no-safe-lower",
                "options": {"mode": "video_subtitles", "resolution": "1080p"},
            },
        )
        assert response.status_code == 201
        payload = wait_for_job_status(client, response.json()["id"], "failed")

    item = payload["items"][0]
    assert service.download_options == []
    assert item["status"] == "failed"
    assert item["fallback_resolution"] is None
    assert item["fallback_reason"] is None
    assert item["error"] == "检测到 1080p 清晰度，但该清晰度当前没有可下载的视频/音频组合，也没有 720p 或更高的可用降级清晰度。"


def test_unselectable_high_resolution_auto_fallback_keeps_original_restart(tmp_path: Path) -> None:
    service = UnselectableHighWithSafeFallbackService()

    with TestClient(create_app(settings=make_settings(tmp_path), ytdlp_service=service)) as client:
        response = client.post(
            "/api/jobs",
            json={
                "url": "https://youtu.be/unselectable-high",
                "options": {"mode": "video_subtitles", "resolution": "1080p"},
            },
        )
        assert response.status_code == 201
        payload = wait_for_job_status(client, response.json()["id"], "succeeded")

    item = payload["items"][0]
    assert [option.resolution for option in service.download_options] == ["720p"]
    assert item["requested_resolution"] == "1080p"
    assert item["fallback_resolution"] == "720p"
    assert item["fallback_reason"] == "requested_resolution_unselectable"
    assert item["resolution_fallback"] == {
        "requested_resolution": "1080p",
        "fallback_resolution": "720p",
        "reason": "requested_resolution_unselectable",
        "restart_resolution": "1080p",
        "message": "检测到 1080p 清晰度，但该清晰度当前没有可下载的视频/音频组合，已自动降级到 720p。",
    }


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


def test_delete_playlist_item_keeps_files_and_refreshes_parent_job(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    playlist_dir = tmp_path / "downloads" / "Course"
    kept_file = playlist_dir / "kept.mp4"
    deleted_file = playlist_dir / "deleted.mp4"
    playlist_dir.mkdir(parents=True)
    kept_file.write_text("kept", encoding="utf-8")
    deleted_file.write_text("deleted", encoding="utf-8")
    engine = create_app_engine(make_settings(tmp_path))
    with Session(engine) as session:
        session.add(
            Job(
                id="job-playlist-delete-item",
                url="https://youtube.com/playlist?list=abc",
                title="Course",
                status="failed",
                options_json="{}",
                total_items=2,
                completed_items=1,
                failed_items=1,
                progress=50.0,
                download_dir=str(playlist_dir),
            )
        )
        session.add(
            JobItem(
                id="item-kept",
                job_id="job-playlist-delete-item",
                source_url="https://youtu.be/kept",
                title="Kept",
                index=1,
                status="succeeded",
                progress=100.0,
                output_path=str(kept_file),
            )
        )
        session.add(
            JobItem(
                id="item-deleted",
                job_id="job-playlist-delete-item",
                source_url="https://youtu.be/deleted",
                title="Deleted",
                index=2,
                status="failed",
                progress=0.0,
                output_path=str(deleted_file),
                error="boom",
            )
        )
        session.commit()

    response = client.post(
        "/api/jobs/job-playlist-delete-item/items/delete",
        json={"item_ids": ["item-deleted"], "delete_files": False},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["deleted_item_ids"] == ["item-deleted"]
    assert payload["job_deleted"] is False
    assert payload["job"]["status"] == "succeeded"
    assert payload["job"]["total_items"] == 1
    assert payload["job"]["completed_items"] == 1
    assert payload["job"]["failed_items"] == 0
    assert [item["id"] for item in payload["job"]["items"]] == ["item-kept"]
    assert kept_file.exists()
    assert deleted_file.exists()


def test_delete_playlist_item_can_delete_output_and_sidecars(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    playlist_dir = tmp_path / "downloads" / "Course"
    kept_file = playlist_dir / "kept.mp4"
    deleted_file = playlist_dir / "deleted.mp4"
    sidecars = [
        deleted_file.with_suffix(".srt"),
        deleted_file.with_suffix(".vtt"),
        deleted_file.with_suffix(".info.json"),
        deleted_file.with_suffix(".description"),
        deleted_file.with_suffix(".jpg"),
    ]
    playlist_dir.mkdir(parents=True)
    kept_file.write_text("kept", encoding="utf-8")
    deleted_file.write_text("deleted", encoding="utf-8")
    for sidecar in sidecars:
        sidecar.write_text("sidecar", encoding="utf-8")
    engine = create_app_engine(make_settings(tmp_path))
    with Session(engine) as session:
        session.add(
            Job(
                id="job-playlist-delete-files",
                url="https://youtube.com/playlist?list=abc",
                title="Course",
                status="running",
                options_json="{}",
                total_items=2,
                download_dir=str(playlist_dir),
            )
        )
        session.add(
            JobItem(
                id="item-kept",
                job_id="job-playlist-delete-files",
                source_url="https://youtu.be/kept",
                title="Kept",
                index=1,
                status="queued",
                progress=0.0,
                output_path=str(kept_file),
            )
        )
        session.add(
            JobItem(
                id="item-deleted",
                job_id="job-playlist-delete-files",
                source_url="https://youtu.be/deleted",
                title="Deleted",
                index=2,
                status="queued",
                progress=0.0,
                output_path=str(deleted_file),
            )
        )
        session.commit()

    response = client.post(
        "/api/jobs/job-playlist-delete-files/items/delete",
        json={"item_ids": ["item-deleted"], "delete_files": True},
    )

    assert response.status_code == 200
    assert kept_file.exists()
    assert not deleted_file.exists()
    assert all(not sidecar.exists() for sidecar in sidecars)
    assert playlist_dir.exists()


def test_delete_all_playlist_items_deletes_parent_and_empty_folder(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    playlist_dir = tmp_path / "downloads" / "Course"
    output_file = playlist_dir / "only.mp4"
    sidecar = output_file.with_suffix(".vtt")
    playlist_dir.mkdir(parents=True)
    output_file.write_text("video", encoding="utf-8")
    sidecar.write_text("subtitle", encoding="utf-8")
    engine = create_app_engine(make_settings(tmp_path))
    with Session(engine) as session:
        session.add(
            Job(
                id="job-playlist-delete-last",
                url="https://youtube.com/playlist?list=abc",
                title="Course",
                status="succeeded",
                options_json="{}",
                total_items=1,
                completed_items=1,
                progress=100.0,
                download_dir=str(playlist_dir),
            )
        )
        session.add(
            JobItem(
                id="item-only",
                job_id="job-playlist-delete-last",
                source_url="https://youtu.be/only",
                title="Only",
                index=1,
                status="succeeded",
                progress=100.0,
                output_path=str(output_file),
            )
        )
        session.commit()

    response = client.post(
        "/api/jobs/job-playlist-delete-last/items/delete",
        json={"item_ids": ["item-only"], "delete_files": True},
    )

    assert response.status_code == 200
    assert response.json() == {"deleted_item_ids": ["item-only"], "job_deleted": True, "job": None}
    assert client.get("/api/jobs/job-playlist-delete-last").status_code == 404
    assert not output_file.exists()
    assert not sidecar.exists()
    assert not playlist_dir.exists()


def test_delete_playlist_item_returns_404_for_missing_item(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    seed_job(tmp_path, "job-delete-missing-item")

    response = client.post(
        "/api/jobs/job-delete-missing-item/items/delete",
        json={"item_ids": ["missing"], "delete_files": False},
    )

    assert response.status_code == 404


def test_delete_running_item_removes_record_without_reinsert(tmp_path: Path) -> None:
    service = BlockingYtDlpService()
    with TestClient(create_app(settings=make_settings(tmp_path), ytdlp_service=service)) as client:
        response = client.post(
            "/api/jobs",
            json={"url": "https://youtu.be/delete-running", "options": {"mode": "video_subtitles", "resolution": "720p"}},
        )
        assert response.status_code == 201
        payload = response.json()
        job_id = payload["id"]
        item_id = payload["items"][0]["id"]
        assert service.started.get(timeout=2) == "https://youtu.be/delete-running"

        delete_response = client.post(
            f"/api/jobs/{job_id}/items/delete",
            json={"item_ids": [item_id], "delete_files": True},
        )
        service.release.set()
        time.sleep(0.1)

        assert delete_response.status_code == 200
        assert delete_response.json()["job_deleted"] is True
        assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_play_single_video_opens_downloaded_file(tmp_path: Path) -> None:
    opened: list[Path] = []
    output_file = tmp_path / "downloads" / "video.mp4"
    output_file.parent.mkdir(parents=True)
    output_file.write_text("video", encoding="utf-8")
    seed_job(tmp_path, "job-play-video", status="succeeded", output_path=output_file)
    client = make_client(tmp_path, system_opener=opened.append)

    response = client.post("/api/jobs/job-play-video/play")

    assert response.status_code == 204
    assert opened == [output_file]


def test_play_single_video_resolves_merged_output_from_stale_stream_path(tmp_path: Path) -> None:
    opened: list[Path] = []
    final_file = tmp_path / "downloads" / "video.mp4"
    stale_stream_file = tmp_path / "downloads" / "video.f137.mp4"
    final_file.parent.mkdir(parents=True)
    final_file.write_text("video", encoding="utf-8")
    seed_job(tmp_path, "job-play-merged-video", status="succeeded", output_path=stale_stream_file)
    client = make_client(tmp_path, system_opener=opened.append)

    response = client.post("/api/jobs/job-play-merged-video/play")

    assert response.status_code == 204
    assert opened == [final_file]


def test_play_single_video_resolves_relative_stale_stream_path_from_job_directory(tmp_path: Path) -> None:
    opened: list[Path] = []
    download_dir = tmp_path / "downloads"
    final_file = download_dir / "relative.mp4"
    final_file.parent.mkdir(parents=True)
    final_file.write_text("video", encoding="utf-8")
    seed_job(tmp_path, "job-play-relative-video", status="succeeded", download_dir=download_dir)
    engine = create_app_engine(make_settings(tmp_path))
    with Session(engine) as session:
        item = session.get(JobItem, "job-play-relative-video-item")
        assert item is not None
        item.output_path = "relative.f140.m4a"
        session.add(item)
        session.commit()
    client = make_client(tmp_path, system_opener=opened.append)

    response = client.post("/api/jobs/job-play-relative-video/play")

    assert response.status_code == 204
    assert opened == [final_file]


def test_job_read_model_discovers_downloaded_video_without_output_path(tmp_path: Path) -> None:
    download_dir = tmp_path / "downloads"
    output_file = download_dir / "Item job-discovered [job-discovered].mp4"
    output_file.parent.mkdir(parents=True)
    output_file.write_text("video", encoding="utf-8")
    seed_job(tmp_path, "job-discovered", status="succeeded")
    client = make_client(tmp_path)

    response = client.get("/api/jobs/job-discovered")

    assert response.status_code == 200
    assert response.json()["items"][0]["output_path"] == str(output_file)


def test_play_single_video_discovers_downloaded_video_without_output_path(tmp_path: Path) -> None:
    opened: list[Path] = []
    download_dir = tmp_path / "downloads"
    output_file = download_dir / "Item job-discovered-play [job-discovered-play].mp4"
    output_file.parent.mkdir(parents=True)
    output_file.write_text("video", encoding="utf-8")
    seed_job(tmp_path, "job-discovered-play", status="succeeded")
    client = make_client(tmp_path, system_opener=opened.append)

    response = client.post("/api/jobs/job-discovered-play/play")

    assert response.status_code == 204
    assert opened == [output_file]


def test_open_single_video_folder_uses_download_dir_when_output_path_is_unknown(tmp_path: Path) -> None:
    opened: list[Path] = []
    download_dir = tmp_path / "downloads"
    download_dir.mkdir(parents=True)
    seed_job(tmp_path, "job-open-folder-without-output", status="running", download_dir=download_dir)
    client = make_client(tmp_path, system_opener=opened.append)

    response = client.post("/api/jobs/job-open-folder-without-output/open-folder")

    assert response.status_code == 204
    assert opened == [download_dir]


def test_delete_job_discovers_output_files_without_output_path(tmp_path: Path) -> None:
    download_dir = tmp_path / "downloads"
    video = download_dir / "Item job-discovered-delete [job-discovered-delete].mp4"
    subtitle = download_dir / "Item job-discovered-delete [job-discovered-delete].en.vtt"
    partial = download_dir / "Item job-discovered-delete [job-discovered-delete].f137.mp4.part"
    download_dir.mkdir(parents=True)
    for path in [video, subtitle, partial]:
        path.write_text("download artifact", encoding="utf-8")
    seed_job(tmp_path, "job-discovered-delete", status="running", download_dir=download_dir)
    client = make_client(tmp_path)

    response = client.delete("/api/jobs/job-discovered-delete?delete_files=true")

    assert response.status_code == 204
    assert not video.exists()
    assert not subtitle.exists()
    assert not partial.exists()


def test_play_single_video_returns_409_when_output_missing(tmp_path: Path) -> None:
    opened: list[Path] = []
    seed_job(tmp_path, "job-play-missing", status="succeeded")
    client = make_client(tmp_path, system_opener=opened.append)

    response = client.post("/api/jobs/job-play-missing/play")

    assert response.status_code == 409
    assert response.json()["detail"] == "视频文件尚不可用。"
    assert opened == []


def test_play_playlist_job_requires_specific_item(tmp_path: Path) -> None:
    opened: list[Path] = []
    playlist_dir = tmp_path / "downloads" / "Course"
    output_file = playlist_dir / "one.mp4"
    playlist_dir.mkdir(parents=True)
    output_file.write_text("video", encoding="utf-8")
    engine = create_app_engine(make_settings(tmp_path))
    init_db(engine)
    with Session(engine) as session:
        session.add(
            Job(
                id="job-playlist-play",
                url="https://youtube.com/playlist?list=abc",
                title="Course",
                status="succeeded",
                options_json="{}",
                total_items=2,
                download_dir=str(playlist_dir),
            )
        )
        for index in [1, 2]:
            session.add(
                JobItem(
                    id=f"item-{index}",
                    job_id="job-playlist-play",
                    source_url=f"https://youtu.be/{index}",
                    title=f"Part {index}",
                    index=index,
                    status="succeeded",
                    output_path=str(output_file),
                )
            )
        session.commit()
    client = make_client(tmp_path, system_opener=opened.append)

    response = client.post("/api/jobs/job-playlist-play/play")

    assert response.status_code == 409
    assert response.json()["detail"] == "合集任务请打开具体视频。"
    assert opened == []


def test_play_playlist_item_opens_downloaded_file(tmp_path: Path) -> None:
    opened: list[Path] = []
    playlist_dir = tmp_path / "downloads" / "Course"
    output_file = playlist_dir / "one.mp4"
    playlist_dir.mkdir(parents=True)
    output_file.write_text("video", encoding="utf-8")
    engine = create_app_engine(make_settings(tmp_path))
    init_db(engine)
    with Session(engine) as session:
        session.add(
            Job(
                id="job-playlist-item-play",
                url="https://youtube.com/playlist?list=abc",
                title="Course",
                status="succeeded",
                options_json="{}",
                total_items=1,
                download_dir=str(playlist_dir),
            )
        )
        session.add(
            JobItem(
                id="item-one",
                job_id="job-playlist-item-play",
                source_url="https://youtu.be/one",
                title="Part one",
                index=1,
                status="succeeded",
                output_path=str(output_file),
            )
        )
        session.commit()
    client = make_client(tmp_path, system_opener=opened.append)

    response = client.post("/api/jobs/job-playlist-item-play/items/item-one/play")

    assert response.status_code == 204
    assert opened == [output_file]


def test_open_single_video_folder_opens_output_parent(tmp_path: Path) -> None:
    opened: list[Path] = []
    output_file = tmp_path / "downloads" / "video.mp4"
    output_file.parent.mkdir(parents=True)
    output_file.write_text("video", encoding="utf-8")
    seed_job(tmp_path, "job-open-folder", status="succeeded", output_path=output_file)
    client = make_client(tmp_path, system_opener=opened.append)

    response = client.post("/api/jobs/job-open-folder/open-folder")

    assert response.status_code == 204
    assert opened == [output_file.parent]


def test_open_playlist_item_folder_opens_output_parent(tmp_path: Path) -> None:
    opened: list[Path] = []
    playlist_dir = tmp_path / "downloads" / "Course"
    output_file = playlist_dir / "one.mp4"
    playlist_dir.mkdir(parents=True)
    output_file.write_text("video", encoding="utf-8")
    engine = create_app_engine(make_settings(tmp_path))
    init_db(engine)
    with Session(engine) as session:
        session.add(
            Job(
                id="job-playlist-item-folder",
                url="https://youtube.com/playlist?list=abc",
                title="Course",
                status="succeeded",
                options_json="{}",
                total_items=1,
                download_dir=str(playlist_dir),
            )
        )
        session.add(
            JobItem(
                id="item-one",
                job_id="job-playlist-item-folder",
                source_url="https://youtu.be/one",
                title="Part one",
                index=1,
                status="succeeded",
                output_path=str(output_file),
            )
        )
        session.commit()
    client = make_client(tmp_path, system_opener=opened.append)

    response = client.post("/api/jobs/job-playlist-item-folder/items/item-one/open-folder")

    assert response.status_code == 204
    assert opened == [playlist_dir]


def test_open_playlist_folder_opens_job_download_dir(tmp_path: Path) -> None:
    opened: list[Path] = []
    playlist_dir = tmp_path / "downloads" / "Course"
    output_file = playlist_dir / "one.mp4"
    playlist_dir.mkdir(parents=True)
    output_file.write_text("video", encoding="utf-8")
    engine = create_app_engine(make_settings(tmp_path))
    init_db(engine)
    with Session(engine) as session:
        session.add(
            Job(
                id="job-playlist-folder",
                url="https://youtube.com/playlist?list=abc",
                title="Course",
                status="succeeded",
                options_json="{}",
                total_items=2,
                download_dir=str(playlist_dir),
            )
        )
        for index in [1, 2]:
            session.add(
                JobItem(
                    id=f"item-folder-{index}",
                    job_id="job-playlist-folder",
                    source_url=f"https://youtu.be/{index}",
                    title=f"Part {index}",
                    index=index,
                    status="succeeded",
                    output_path=str(output_file),
                )
            )
        session.commit()
    client = make_client(tmp_path, system_opener=opened.append)

    response = client.post("/api/jobs/job-playlist-folder/open-folder")

    assert response.status_code == 204
    assert opened == [playlist_dir]


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
