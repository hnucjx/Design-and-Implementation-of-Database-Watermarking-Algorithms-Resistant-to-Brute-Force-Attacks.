"""Measure whether JobManager item-level concurrency really runs in parallel.

Usage:  python scripts/bench_concurrency.py [temp_dir]

Creates one playlist job with 8 items whose `download()` sleeps 1s, then reports
wall-clock time and the observed peak number of parallel downloads for
concurrency 1 / 2 / 4 / 8. Perfect scaling means wall ~= 8 / concurrency and
peak == concurrency.
"""
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import AppSettings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.schemas import AnalyzeResponse, FormatOption, VideoEntry  # noqa: E402

ITEM_COUNT = 8


class SlowService:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.durations = []

    def get_ffmpeg_status(self):
        return {"ffmpeg": True, "ffprobe": True}

    def extract_metadata(self, url, cookies_path=None):
        entries = [
            VideoEntry(index=i, id=f"v{i}", title=f"Video {i}", url=f"https://youtu.be/v{i}")
            for i in range(1, ITEM_COUNT + 1)
        ]
        return AnalyzeResponse(
            url=url,
            title="Probe playlist",
            is_playlist=True,
            entries=entries,
            formats=[FormatOption(format_id="22", label="720p mp4", height=720, ext="mp4")],
            ffmpeg={"ffmpeg": True, "ffprobe": True},
        )

    def prepare_download(self, url, options, cookies_path=None):
        return SimpleNamespace(is_selectable=True, width=1280, height=720, actual_format="mp4", filesize=1000)

    def download(self, url, options, progress_hook, should_cancel, cookies_path=None, download_dir=None):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        started = time.perf_counter()
        try:
            time.sleep(1.0)
            progress_hook({"status": "finished", "info_dict": {"ext": "mp4"}, "filename": "x.mp4"})
        finally:
            with self.lock:
                self.durations.append(time.perf_counter() - started)
                self.active -= 1

    def detect_file_resolution(self, path):
        return None


def run(concurrency: int, tmp: Path):
    settings = AppSettings(
        data_dir=tmp / "data",
        download_dir=tmp / "downloads",
        database_path=tmp / "data" / "probe.sqlite3",
        default_concurrency=concurrency,
    )
    service = SlowService()
    with TestClient(create_app(settings=settings, ytdlp_service=service)) as client:
        start = time.perf_counter()
        response = client.post(
            "/api/jobs",
            json={"url": "https://youtube.com/playlist?list=probe", "options": {"resolution": "720p"}},
        )
        assert response.status_code == 201, response.text
        job_id = response.json()["id"]
        deadline = time.perf_counter() + 60
        while time.perf_counter() < deadline:
            detail = client.get(f"/api/jobs/{job_id}").json()
            if detail["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.2)
        wall = time.perf_counter() - start
    return wall, service.peak, detail["status"]


if __name__ == "__main__":
    base = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    for concurrency in (1, 2, 4, 8):
        tmp = base / f"probe-{concurrency}"
        wall, peak, status = run(concurrency, tmp)
        print(f"concurrency={concurrency}: wall={wall:.2f}s peak_parallel_downloads={peak} status={status}")
