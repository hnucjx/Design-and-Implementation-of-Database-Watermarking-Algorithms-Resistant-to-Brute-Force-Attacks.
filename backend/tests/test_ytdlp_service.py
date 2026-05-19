from pathlib import Path
import sys
from types import SimpleNamespace
from http.cookiejar import Cookie

import pytest
from yt_dlp.cookies import YoutubeDLCookieJar

from app.schemas import DownloadOptions, FormatOption
from app.ytdlp_service import BrowserCookieImportError, YtDlpService


def test_resolution_option_limits_best_video_height(monkeypatch, tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr(service, "_ffmpeg_executable", lambda: str(tmp_path / "ffmpeg.exe"))
    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="1080p"),
        cookies_path=None,
    )

    assert opts["format"] == "bv*[height=1080][ext=mp4]+ba[ext=m4a]/bv*[height=1080]+ba/b[height=1080]"
    assert "/best" not in opts["format"]
    assert "height<=1080" not in opts["format"]
    assert opts["merge_output_format"] == "mp4"
    assert opts["ffmpeg_location"] == str(tmp_path / "ffmpeg.exe")
    assert opts["skip_download"] is False


def test_bundled_ffmpeg_is_used_when_system_ffmpeg_is_missing(monkeypatch, tmp_path: Path) -> None:
    bundled_ffmpeg = tmp_path / "bundled-ffmpeg.exe"
    monkeypatch.setattr("app.ytdlp_service.shutil.which", lambda name: None)
    monkeypatch.setitem(
        sys.modules,
        "imageio_ffmpeg",
        SimpleNamespace(get_ffmpeg_exe=lambda: str(bundled_ffmpeg)),
    )
    service = YtDlpService(download_dir=tmp_path)

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="1080p"),
        cookies_path=None,
    )

    assert opts["ffmpeg_location"] == str(bundled_ffmpeg)
    assert service.get_ffmpeg_status() == {"ffmpeg": True, "ffprobe": False}


def test_resolution_can_be_extracted_from_progress_payload_requested_formats(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    resolution = service.resolution_from_progress_payload(
        {
            "status": "finished",
            "info_dict": {
                "requested_formats": [
                    {"format_id": "137", "vcodec": "avc1", "acodec": "none", "width": 1920, "height": 1080},
                    {"format_id": "140", "vcodec": "none", "acodec": "mp4a", "width": None, "height": None},
                ]
            },
        }
    )

    assert resolution == (1920, 1080)


def test_suggests_highest_available_resolution_below_requested(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    fallback = service.suggest_lower_resolution(
        "1080p",
        [
            FormatOption(format_id="22", label="720p mp4", height=720, ext="mp4"),
            FormatOption(format_id="18", label="360p mp4", height=360, ext="mp4"),
            FormatOption(format_id="137", label="1080p mp4", height=1080, ext="mp4"),
        ],
    )

    assert fallback == "720p"
    assert service.suggest_lower_resolution("720p", []) is None
    assert service.suggest_lower_resolution("best", [FormatOption(format_id="18", label="360p", height=360)]) is None


def test_download_options_accept_task_specific_download_dir(monkeypatch, tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path / "root")
    target_dir = tmp_path / "root" / "Course"
    monkeypatch.setattr(service, "_ffmpeg_executable", lambda: str(tmp_path / "ffmpeg.exe"))

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="720p"),
        cookies_path=None,
        download_dir=target_dir,
    )

    assert opts["outtmpl"] == str(target_dir / "%(title).200B [%(id)s].%(ext)s")


def test_subtitle_only_options_skip_video_and_include_languages(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)
    opts = service.build_download_options(
        DownloadOptions(
            mode="subtitles_only",
            subtitle_languages=["en", "zh-Hans"],
            subtitle_source="both",
            subtitle_format="srt",
        ),
        cookies_path=tmp_path / "cookies.txt",
    )

    assert opts["skip_download"] is True
    assert opts["writesubtitles"] is True
    assert opts["writeautomaticsub"] is True
    assert opts["subtitleslangs"] == ["en", "zh-Hans"]
    assert opts["subtitlesformat"] == "srt"
    assert opts["cookiefile"] == str(tmp_path / "cookies.txt")


def test_explicit_resolution_fails_without_any_ffmpeg(monkeypatch, tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr("app.ytdlp_service.shutil.which", lambda name: None)
    monkeypatch.setitem(sys.modules, "imageio_ffmpeg", None)

    with pytest.raises(RuntimeError, match="ffmpeg is required"):
        service.build_download_options(
            DownloadOptions(mode="video_subtitles", resolution="1080p"),
            cookies_path=None,
        )


def test_download_options_enable_supported_js_runtime(monkeypatch, tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr(service, "get_ffmpeg_status", lambda: {"ffmpeg": False, "ffprobe": False})
    monkeypatch.setattr(service, "_detect_js_runtime", lambda: ("node", "C:/Program Files/nodejs/node.exe", "v20.11.1"))

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="best"),
        cookies_path=None,
    )

    assert opts["js_runtimes"] == {"node": {"path": "C:/Program Files/nodejs/node.exe"}}


def test_default_speed_limit_is_2048_kbps(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="best"),
        cookies_path=None,
    )

    assert opts["ratelimit"] == 2048 * 1024


def test_empty_speed_limit_means_unlimited(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="best", speed_limit_kbps=None),
        cookies_path=None,
    )

    assert "ratelimit" not in opts


def test_import_browser_cookies_saves_only_youtube_related_cookies(monkeypatch, tmp_path: Path) -> None:
    jar = YoutubeDLCookieJar()
    jar.set_cookie(_cookie(".youtube.com", "VISITOR_INFO1_LIVE", "YOUTUBE_SECRET"))
    jar.set_cookie(_cookie(".google.com", "SID", "GOOGLE_SECRET"))
    jar.set_cookie(_cookie(".example.com", "SESSION", "UNRELATED_SECRET"))
    attempted: list[str] = []

    def fake_extract(browser_name, profile=None, logger=None, *, keyring=None, container=None):
        attempted.append(browser_name)
        if browser_name == "edge":
            raise RuntimeError("edge is not available")
        return jar

    monkeypatch.setattr("app.ytdlp_service.extract_cookies_from_browser", fake_extract, raising=False)

    result = YtDlpService(download_dir=tmp_path).import_browser_cookies("auto", tmp_path / "cookies.txt")

    content = (tmp_path / "cookies.txt").read_text(encoding="utf-8")
    assert result.browser == "chrome"
    assert result.imported_count == 2
    assert attempted[:2] == ["edge", "chrome"]
    assert "YOUTUBE_SECRET" in content
    assert "GOOGLE_SECRET" in content
    assert "UNRELATED_SECRET" not in content


def test_import_browser_cookies_reports_locked_edge_database(monkeypatch, tmp_path: Path) -> None:
    def fake_extract(browser_name, profile=None, logger=None, *, keyring=None, container=None):
        raise RuntimeError("Could not copy Chrome cookie database. See https://github.com/yt-dlp/yt-dlp/issues/7271")

    monkeypatch.setattr("app.ytdlp_service.extract_cookies_from_browser", fake_extract, raising=False)

    with pytest.raises(BrowserCookieImportError) as exc_info:
        YtDlpService(download_dir=tmp_path).import_browser_cookies("edge", tmp_path / "cookies.txt")

    error = exc_info.value
    assert error.code == "browser_locked"
    assert error.browser == "edge"
    assert "Edge" in error.message
    assert not (tmp_path / "cookies.txt").exists()


def test_import_browser_cookies_closes_edge_and_retries_when_allowed(monkeypatch, tmp_path: Path) -> None:
    jar = YoutubeDLCookieJar()
    jar.set_cookie(_cookie(".youtube.com", "VISITOR_INFO1_LIVE", "YOUTUBE_SECRET"))
    attempts: list[str] = []
    closed: list[str] = []

    def fake_extract(browser_name, profile=None, logger=None, *, keyring=None, container=None):
        attempts.append(browser_name)
        if len(attempts) == 1:
            raise RuntimeError("Could not copy Chrome cookie database. See https://github.com/yt-dlp/yt-dlp/issues/7271")
        return jar

    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr("app.ytdlp_service.extract_cookies_from_browser", fake_extract, raising=False)
    monkeypatch.setattr(service, "_close_browser_for_cookie_import", lambda browser: closed.append(browser), raising=False)

    result = service.import_browser_cookies("edge", tmp_path / "cookies.txt", close_browser_if_locked=True)

    assert result.browser == "edge"
    assert result.imported_count == 1
    assert attempts == ["edge", "edge"]
    assert closed == ["edge"]
    assert "YOUTUBE_SECRET" in (tmp_path / "cookies.txt").read_text(encoding="utf-8")


def test_import_browser_cookies_uses_edge_cdp_fallback_after_dpapi_failure(monkeypatch, tmp_path: Path) -> None:
    jar = YoutubeDLCookieJar()
    jar.set_cookie(_cookie(".youtube.com", "VISITOR_INFO1_LIVE", "YOUTUBE_SECRET"))
    cdp_calls: list[bool] = []

    def fake_extract(browser_name, profile=None, logger=None, *, keyring=None, container=None):
        raise RuntimeError("Failed to decrypt with DPAPI. See https://github.com/yt-dlp/yt-dlp/issues/10927")

    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr("app.ytdlp_service.extract_cookies_from_browser", fake_extract, raising=False)
    monkeypatch.setattr(service, "_extract_edge_cookies_via_cdp", lambda: cdp_calls.append(True) or jar, raising=False)

    result = service.import_browser_cookies("edge", tmp_path / "cookies.txt")

    assert result.browser == "edge"
    assert result.imported_count == 1
    assert cdp_calls == [True]
    assert "YOUTUBE_SECRET" in (tmp_path / "cookies.txt").read_text(encoding="utf-8")


def test_edge_cdp_fallback_terminates_process_tree(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    class FakeProcess:
        pid = 1234

        def terminate(self):
            raise AssertionError("Windows cleanup should use taskkill for the process tree.")

    def fake_run(args, **kwargs):
        calls.append(args)

    monkeypatch.setattr("app.ytdlp_service.os.name", "nt")
    monkeypatch.setattr("app.ytdlp_service.subprocess.run", fake_run)

    YtDlpService(download_dir=tmp_path)._terminate_edge_process(FakeProcess())

    assert calls == [["taskkill", "/PID", "1234", "/F", "/T"]]


def test_auto_browser_cookie_import_prioritizes_locked_edge_error(monkeypatch, tmp_path: Path) -> None:
    empty_jar = YoutubeDLCookieJar()
    attempts: list[str] = []

    def fake_extract(browser_name, profile=None, logger=None, *, keyring=None, container=None):
        attempts.append(browser_name)
        if browser_name == "edge":
            raise RuntimeError("Could not copy Chrome cookie database. See https://github.com/yt-dlp/yt-dlp/issues/7271")
        return empty_jar

    monkeypatch.setattr("app.ytdlp_service.AUTO_BROWSER_COOKIE_CANDIDATES", ["edge", "chrome"])
    monkeypatch.setattr("app.ytdlp_service.extract_cookies_from_browser", fake_extract, raising=False)

    with pytest.raises(BrowserCookieImportError) as exc_info:
        YtDlpService(download_dir=tmp_path).import_browser_cookies("auto", tmp_path / "cookies.txt")

    assert exc_info.value.code == "browser_locked"
    assert exc_info.value.browser == "edge"
    assert attempts == ["edge", "chrome"]


def test_extract_metadata_maps_playlist_entries_formats_and_subtitles(monkeypatch, tmp_path: Path) -> None:
    captured_opts = {}

    class FakeYoutubeDL:
        def __init__(self, opts):
            captured_opts.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            assert url == "https://youtube.com/playlist?list=abc"
            assert download is False
            return {
                "_type": "playlist",
                "id": "abc",
                "title": "Course",
                "webpage_url": url,
                "entries": [
                    {
                        "id": "v1",
                        "title": "Intro",
                        "webpage_url": "https://youtube.com/watch?v=v1",
                        "duration": 120,
                        "thumbnail": "https://img/1.jpg",
                    }
                ],
                "formats": [
                    {"format_id": "22", "height": 720, "ext": "mp4", "filesize": 10_000},
                    {"format_id": "137", "height": 1080, "ext": "mp4", "filesize_approx": 20_000},
                ],
                "subtitles": {"en": [{"ext": "vtt"}]},
                "automatic_captions": {"zh-Hans": [{"ext": "vtt"}]},
            }

    monkeypatch.setattr("app.ytdlp_service.yt_dlp.YoutubeDL", FakeYoutubeDL)

    result = YtDlpService(download_dir=tmp_path).extract_metadata(
        "https://youtube.com/playlist?list=abc",
        cookies_path=tmp_path / "cookies.txt",
    )

    assert captured_opts["cookiefile"] == str(tmp_path / "cookies.txt")
    assert captured_opts["ignoreconfig"] is True
    assert captured_opts["extract_flat"] == "in_playlist"
    assert captured_opts["sleep_interval_requests"] == 1.0
    assert result.is_playlist is True
    assert result.title == "Course"
    assert result.entries[0].title == "Intro"
    assert [fmt.format_id for fmt in result.formats] == ["22", "137"]
    assert [fmt.filesize for fmt in result.formats] == [10_000, 20_000]
    assert result.subtitles[0].language == "en"
    assert result.automatic_subtitles[0].language == "zh-Hans"


def test_download_options_ignore_user_ytdlp_config(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="best"),
        cookies_path=None,
    )

    assert opts["ignoreconfig"] is True


def test_download_options_apply_conservative_youtube_request_pacing(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="best"),
        cookies_path=None,
    )

    assert opts["sleep_interval_requests"] == 1.0
    assert opts["sleep_interval"] == 2.0
    assert opts["max_sleep_interval"] == 5.0


def test_cookie_required_error_detection_handles_youtube_bot_challenge(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    assert service.is_cookie_required_error(
        RuntimeError(
            "ERROR: [youtube] G9MxNwUoSt0: Sign in to confirm you’re not a bot. "
            "Use --cookies-from-browser or --cookies for the authentication."
        )
    )
    assert not service.is_cookie_required_error(RuntimeError("Requested format is not available."))


def _cookie(domain: str, name: str, value: str) -> Cookie:
    return Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path="/",
        path_specified=True,
        secure=True,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    )
