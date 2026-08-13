PROGRESS_PERSIST_MIN_INTERVAL_SECONDS = 0.25
PROGRESS_PERSIST_MIN_DELTA = 0.5


class ProgressPersistGate:
    def __init__(
        self,
        min_interval_seconds: float = PROGRESS_PERSIST_MIN_INTERVAL_SECONDS,
        min_progress_delta: float = PROGRESS_PERSIST_MIN_DELTA,
    ) -> None:
        self.min_interval_seconds = min_interval_seconds
        self.min_progress_delta = min_progress_delta
        self._last_allowed_at: float | None = None
        self._last_progress: float | None = None

    def allow(self, *, status: str | None, progress: float, now: float) -> bool:
        if status in {"finished", "error"} or self._last_allowed_at is None:
            self._remember(progress, now)
            return True
        elapsed = now - self._last_allowed_at
        delta = abs(progress - (self._last_progress or 0.0))
        if elapsed >= self.min_interval_seconds or delta >= self.min_progress_delta:
            self._remember(progress, now)
            return True
        return False

    def _remember(self, progress: float, now: float) -> None:
        self._last_progress = progress
        self._last_allowed_at = now
