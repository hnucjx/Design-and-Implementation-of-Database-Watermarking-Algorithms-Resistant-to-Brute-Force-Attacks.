from dataclasses import dataclass
from typing import Any


MAX_RUNNING_PROGRESS = 99.9


@dataclass
class ProgressSnapshot:
    downloaded_bytes: int | None
    total_bytes: int | None
    progress: float


@dataclass
class _StreamProgress:
    downloaded_bytes: int = 0
    total_bytes: int | None = None


class DownloadProgressAggregator:
    def __init__(self, max_running_progress: float = MAX_RUNNING_PROGRESS) -> None:
        self._max_running_progress = max_running_progress
        self._streams: dict[str, _StreamProgress] = {}
        self._last_progress = 0.0
        self.output_path: str | None = None

    def update(self, payload: dict[str, Any]) -> ProgressSnapshot:
        key = self._stream_key(payload)
        stream = self._streams.setdefault(key, _StreamProgress())
        downloaded = self._positive_int(payload.get("downloaded_bytes"))
        total = self._positive_int(payload.get("total_bytes") or payload.get("total_bytes_estimate"))

        if downloaded is not None:
            stream.downloaded_bytes = max(stream.downloaded_bytes, downloaded)
        if total is not None:
            stream.total_bytes = max(stream.total_bytes or 0, total)
        elif payload.get("status") == "finished" and downloaded is not None:
            stream.total_bytes = max(stream.total_bytes or 0, downloaded)

        if payload.get("status") == "finished" and payload.get("filename"):
            self.output_path = str(payload["filename"])

        downloaded_sum = sum(stream.downloaded_bytes for stream in self._streams.values())
        total_sum = self._total_bytes()
        progress = self._progress(downloaded_sum, total_sum)
        self._last_progress = max(self._last_progress, progress)

        return ProgressSnapshot(
            downloaded_bytes=downloaded_sum if downloaded is not None else None,
            total_bytes=total_sum,
            progress=self._last_progress,
        )

    def _total_bytes(self) -> int | None:
        if not self._streams:
            return None
        total = sum(max(stream.total_bytes or 0, stream.downloaded_bytes) for stream in self._streams.values())
        return total or None

    def _progress(self, downloaded_bytes: int, total_bytes: int | None) -> float:
        if not total_bytes:
            return self._last_progress
        progress = min(self._max_running_progress, (downloaded_bytes / total_bytes) * 100.0)
        return max(0.0, progress)

    def _stream_key(self, payload: dict[str, Any]) -> str:
        ctx_id = payload.get("ctx_id")
        if ctx_id is not None:
            return f"ctx:{ctx_id}"

        info = payload.get("info_dict")
        if isinstance(info, dict):
            format_id = info.get("format_id")
            if format_id:
                return f"format:{format_id}"

        if "default" in self._streams and not payload.get("tmpfilename"):
            return "default"

        path = payload.get("tmpfilename") or payload.get("filename")
        if path:
            return f"path:{self._normalize_path_key(str(path))}"
        return "default"

    def _normalize_path_key(self, path: str) -> str:
        return path.removesuffix(".part")

    def _positive_int(self, value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None
