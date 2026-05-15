from pathlib import Path

from app.schemas import DownloadOptions
from app.ytdlp_service import YtDlpService


def test_resolution_option_limits_best_video_height(monkeypatch, tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr(service, "get_ffmpeg_status", lambda: {"ffmpeg": True, "ffprobe": True})
    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="720p"),
        cookies_path=None,
    )

    assert opts["format"] == "bv*[height<=720]+ba/b[height<=720]/best[height<=720]/best"
    assert opts["merge_output_format"] == "mp4"
    assert opts["skip_download"] is False


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


def test_video_options_do_not_request_format_merging_without_ffmpeg(monkeypatch, tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)
    monkeypatch.setattr(service, "get_ffmpeg_status", lambda: {"ffmpeg": False, "ffprobe": False})

    opts = service.build_download_options(
        DownloadOptions(mode="video_subtitles", resolution="720p"),
        cookies_path=None,
    )

    assert "+" not in opts["format"]
    assert opts["format"] == "best[height<=720][ext=mp4]/best[height<=720]/best"
    assert "merge_output_format" not in opts


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
    assert result.is_playlist is True
    assert result.title == "Course"
    assert result.entries[0].title == "Intro"
    assert [fmt.format_id for fmt in result.formats] == ["22", "137"]
    assert result.subtitles[0].language == "en"
    assert result.automatic_subtitles[0].language == "zh-Hans"
