"""Detect downloads that stop making progress so they fail visibly.

yt-dlp can sit in a silent loop without ever raising (for example the
`ThrottledDownload` / `ReExtractInfo` re-extract cycle, see PLAN.md §3.1). The
guard watches the progress hook and raises `DownloadStalled` when no new bytes
have arrived for a configurable window.

Judgement rule: track the **highest byte count ever seen** (`best_bytes`) and
the time it was reached. The guard trips only when that maximum has not been
beaten for `timeout_seconds`. This distinguishes two cases that a naive
"did this round grow?" check cannot:

* throttle / re-extract loop: bytes oscillate 0 -> 56K -> 0 -> 56K, the current
  round keeps growing but the historical maximum is never beaten -> trips;
* a slow but healthy resume: bytes eventually exceed the old maximum -> no trip.

A fresh `StallGuard` is created per download attempt. `status == "finished"`
resets the baseline, because yt-dlp emits it between the video and audio
streams of a merged format and the byte counter restarts from zero there.
"""

from __future__ import annotations

import time
from typing import Any


class DownloadStalled(RuntimeError):
    """Raised when a download stops producing new bytes for too long."""


class StallGuard:
    def __init__(self, timeout_seconds: float = 90.0) -> None:
        self.timeout_seconds = float(timeout_seconds)
        self._best_bytes = 0
        self._best_at: float | None = None

    @property
    def best_bytes(self) -> int:
        return self._best_bytes

    def reset(self, now: float | None = None) -> None:
        self._best_bytes = 0
        self._best_at = None if now is None else now

    def observe(self, payload: dict[str, Any], now: float | None = None) -> None:
        if self.timeout_seconds <= 0 or not isinstance(payload, dict):
            return

        status = payload.get("status")
        timestamp = time.monotonic() if now is None else now

        if status == "finished":
            self._best_bytes = 0
            self._best_at = timestamp
            return
        if status != "downloading":
            return

        downloaded = payload.get("downloaded_bytes")
        if not isinstance(downloaded, (int, float)) or downloaded <= 0:
            return

        if downloaded > self._best_bytes:
            self._best_bytes = int(downloaded)
            self._best_at = timestamp
            return

        if self._best_at is not None and timestamp - self._best_at >= self.timeout_seconds:
            raise DownloadStalled(f"下载停滞：{int(self.timeout_seconds)} 秒内没有新增字节")
