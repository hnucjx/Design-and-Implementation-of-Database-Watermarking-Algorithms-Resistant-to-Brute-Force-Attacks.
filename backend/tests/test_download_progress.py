from app.download_progress import DownloadProgressAggregator, MAX_RUNNING_PROGRESS


def test_sidecar_finish_does_not_lock_progress_when_expected_total_is_known() -> None:
    progress = DownloadProgressAggregator(expected_total_bytes=1_000_000)

    sidecar = progress.update(
        {
            "status": "finished",
            "downloaded_bytes": 2_000,
            "total_bytes": 2_000,
            "filename": "video.en.vtt",
        }
    )
    media = progress.update(
        {
            "status": "downloading",
            "downloaded_bytes": 100_000,
            "total_bytes": 998_000,
            "tmpfilename": "video.mp4.part",
            "speed": 50_000,
            "eta": 18,
        }
    )

    assert sidecar.progress < 5
    assert media.progress == 10.2
    assert media.progress < MAX_RUNNING_PROGRESS
    assert media.downloaded_bytes == 102_000
    assert media.total_bytes == 1_000_000


def test_missing_total_does_not_treat_downloaded_bytes_as_complete() -> None:
    progress = DownloadProgressAggregator()

    snapshot = progress.update(
        {
            "status": "downloading",
            "downloaded_bytes": 50_000,
            "speed": 12_000,
            "eta": 40,
        }
    )

    assert snapshot.progress == 0
    assert snapshot.downloaded_bytes == 50_000
    assert snapshot.total_bytes is None


def test_http_chunk_total_smaller_than_downloaded_uses_expected_total() -> None:
    progress = DownloadProgressAggregator(expected_total_bytes=100_000_000)

    first_chunk = progress.update(
        {
            "status": "downloading",
            "downloaded_bytes": 16_000_000,
            "total_bytes": 16_000_000,
            "tmpfilename": "video.mp4.part",
            "info_dict": {"format_id": "137", "filesize": 80_000_000},
        }
    )
    later_chunk = progress.update(
        {
            "status": "downloading",
            "downloaded_bytes": 20_000_000,
            "total_bytes": 16_000_000,
            "tmpfilename": "video.mp4.part",
            "info_dict": {"format_id": "137", "filesize": 80_000_000},
        }
    )

    assert first_chunk.progress == 16.0
    assert later_chunk.progress == 20.0
    assert later_chunk.total_bytes == 100_000_000
    assert later_chunk.progress < MAX_RUNNING_PROGRESS


def test_split_stream_first_finish_uses_expected_total_instead_of_almost_complete() -> None:
    progress = DownloadProgressAggregator(expected_total_bytes=120)

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

    assert first.progress == 100 / 120 * 100
    assert second.progress == first.progress
    assert first.progress < 90


def test_new_stream_total_allows_progress_to_drop_from_false_completion() -> None:
    progress = DownloadProgressAggregator()

    sidecar = progress.update(
        {
            "status": "finished",
            "downloaded_bytes": 2_000,
            "total_bytes": 2_000,
            "filename": "video.en.vtt",
        }
    )
    media = progress.update(
        {
            "status": "downloading",
            "downloaded_bytes": 50_000,
            "total_bytes": 1_000_000,
            "tmpfilename": "video.mp4.part",
            "info_dict": {"format_id": "137"},
        }
    )

    assert sidecar.progress == MAX_RUNNING_PROGRESS
    assert media.progress == 52_000 / 1_002_000 * 100
    assert media.downloaded_bytes == 52_000
    assert media.total_bytes == 1_002_000


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

    assert first.progress == MAX_RUNNING_PROGRESS
    assert second.progress == 100 / 120 * 100
    assert second.downloaded_bytes >= first.downloaded_bytes
    assert second.total_bytes >= first.total_bytes
    assert final.progress == MAX_RUNNING_PROGRESS
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
