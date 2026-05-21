from app.download_progress import DownloadProgressAggregator


def test_split_stream_progress_is_aggregated_and_monotonic() -> None:
    progress = DownloadProgressAggregator()

    first = progress.update(
        {
            "status": "finished",
            "downloaded_bytes": 100,
            "total_bytes": 100,
            "filename": "video.f137.mp4",
            "info_dict": {"format_id": "137"},
        }
    )
    second = progress.update(
        {
            "status": "downloading",
            "downloaded_bytes": 0,
            "total_bytes": 20,
            "tmpfilename": "video.f140.m4a.part",
            "info_dict": {"format_id": "140"},
        }
    )
    final = progress.update(
        {
            "status": "finished",
            "downloaded_bytes": 20,
            "total_bytes": 20,
            "filename": "video.f140.m4a",
            "info_dict": {"format_id": "140"},
        }
    )

    assert 0 < first.progress < 100
    assert second.progress >= first.progress
    assert second.downloaded_bytes >= first.downloaded_bytes
    assert second.total_bytes >= first.total_bytes
    assert final.progress >= second.progress
    assert final.downloaded_bytes == 120
    assert final.total_bytes == 120


def test_single_file_progress_keeps_normal_downloaded_and_total_bytes() -> None:
    progress = DownloadProgressAggregator()

    first = progress.update(
        {
            "status": "downloading",
            "downloaded_bytes": 50,
            "total_bytes": 100,
            "tmpfilename": "video.mp4.part",
        }
    )
    final = progress.update(
        {
            "status": "finished",
            "downloaded_bytes": 100,
            "total_bytes": 100,
            "filename": "video.mp4",
        }
    )

    assert first.downloaded_bytes == 50
    assert first.total_bytes == 100
    assert first.progress == 50
    assert final.downloaded_bytes == 100
    assert final.total_bytes == 100
    assert final.progress < 100
    assert progress.output_path == "video.mp4"


def test_final_payload_without_early_filename_stays_in_default_stream() -> None:
    progress = DownloadProgressAggregator()

    progress.update({"status": "downloading", "downloaded_bytes": 0, "total_bytes": 100})
    progress.update({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
    final = progress.update(
        {
            "status": "finished",
            "downloaded_bytes": 100,
            "total_bytes": 100,
            "filename": "video.mp4",
        }
    )

    assert final.downloaded_bytes == 100
    assert final.total_bytes == 100
    assert final.progress < 100
