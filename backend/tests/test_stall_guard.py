from pathlib import Path

import pytest

from app.schemas import DownloadOptions
from app.stall_guard import DownloadStalled, StallGuard
from app.ytdlp_service import YtDlpService


def downloading(downloaded_bytes: int | None) -> dict:
    return {"status": "downloading", "downloaded_bytes": downloaded_bytes, "total_bytes": 1_000_000}


def test_steady_progress_does_not_trip() -> None:
    guard = StallGuard(90.0)

    for index in range(1, 10):
        guard.observe(downloading(index * 10_000), now=float(index * 5))

    assert guard.best_bytes == 90_000


def test_no_new_bytes_for_the_timeout_trips() -> None:
    guard = StallGuard(90.0)
    guard.observe(downloading(50_000), now=0.0)
    guard.observe(downloading(50_000), now=89.0)

    with pytest.raises(DownloadStalled) as raised:
        guard.observe(downloading(50_000), now=90.0)

    assert "下载停滞" in str(raised.value)


def test_throttle_oscillation_trips_because_the_maximum_is_never_beaten() -> None:
    """节流循环里本轮一直在增长，但历史最大值从不刷新。"""
    guard = StallGuard(90.0)
    guard.observe(downloading(56_331), now=0.0)

    for round_index in range(4):  # 每 5 秒一轮：字节回落再涨回旧峰值，峰值从不刷新
        base = 5.0 * (round_index + 1)
        guard.observe(downloading(1_024), now=base)
        guard.observe(downloading(40_000), now=base + 2.0)
        guard.observe(downloading(56_331), now=base + 4.0)

    with pytest.raises(DownloadStalled):
        guard.observe(downloading(56_331), now=95.0)


def test_finished_resets_the_baseline_for_the_next_stream() -> None:
    """合并格式下视频流结束、音频流从 0 重新开始，不应被误判为停滞。"""
    guard = StallGuard(90.0)
    guard.observe(downloading(500_000), now=0.0)
    guard.observe({"status": "finished"}, now=10.0)

    guard.observe(downloading(1_000), now=20.0)
    guard.observe(downloading(20_000), now=100.0)
    guard.observe(downloading(30_000), now=300.0)

    assert guard.best_bytes == 30_000


def test_zero_timeout_disables_the_guard() -> None:
    guard = StallGuard(0.0)

    for index in range(1, 50):
        guard.observe(downloading(1_024), now=float(index * 100))


def test_non_downloading_statuses_are_ignored() -> None:
    guard = StallGuard(90.0)

    for index in range(1, 20):
        guard.observe({"status": "finished"}, now=float(index * 100))
        guard.observe({"status": "error"}, now=float(index * 100))
        guard.observe({"status": "downloading", "downloaded_bytes": None}, now=float(index * 100))


def test_stall_message_is_not_classified_as_media_stream_blocked(tmp_path: Path) -> None:
    service = YtDlpService(download_dir=tmp_path)

    assert not service.is_media_stream_blocked_error(DownloadStalled("下载停滞：90 秒内没有新增字节"))


def test_download_does_not_retry_other_profiles_on_stall(tmp_path: Path, monkeypatch) -> None:
    service = YtDlpService(download_dir=tmp_path)
    attempts: list[str] = []

    def fake_once(url, options, progress_hook, should_cancel, cookies_path, download_dir, profile):
        attempts.append(profile)
        raise DownloadStalled("下载停滞：90 秒内没有新增字节")

    monkeypatch.setattr(service, "_download_once", fake_once)

    with pytest.raises(DownloadStalled):
        service.download(
            "https://www.youtube.com/watch?v=example",
            DownloadOptions(mode="video_subtitles", resolution="best"),
            lambda payload: None,
            lambda: False,
        )

    assert attempts == ["default"]


def test_download_still_retries_other_profiles_on_403(tmp_path: Path, monkeypatch) -> None:
    service = YtDlpService(download_dir=tmp_path)
    attempts: list[str] = []

    def fake_once(url, options, progress_hook, should_cancel, cookies_path, download_dir, profile):
        attempts.append(profile)
        raise RuntimeError("ERROR: unable to download video data: HTTP Error 403: Forbidden")

    monkeypatch.setattr(service, "_download_once", fake_once)

    with pytest.raises(RuntimeError):
        service.download(
            "https://www.youtube.com/watch?v=example",
            DownloadOptions(mode="video_subtitles", resolution="best"),
            lambda payload: None,
            lambda: False,
        )

    assert len(attempts) > 1
