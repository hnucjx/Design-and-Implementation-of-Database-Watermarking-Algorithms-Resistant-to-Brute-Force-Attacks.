from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Session, select

from .fallback_policy import build_resolution_fallback
from .models import Job, JobItem, utc_now
from .output_paths import discover_existing_output_path, resolve_existing_output_path
from .schemas import JobItemRead, JobRead, ResolutionFallback


def read_job(session: Session, job_id: str) -> JobRead | None:
    job = session.get(Job, job_id)
    if not job:
        return None
    items = session.exec(select(JobItem).where(JobItem.job_id == job_id).order_by(JobItem.index)).all()
    return JobRead(
        id=job.id,
        url=job.url,
        title=job.title,
        status=job.status,
        progress=job.progress,
        speed=job.speed,
        eta=job.eta,
        total_items=job.total_items,
        completed_items=job.completed_items,
        failed_items=job.failed_items,
        current_item_title=job.current_item_title,
        error=_job_error(job, items),
        download_dir=job.download_dir,
        actual_resolution=_actual_resolution(items),
        actual_format=_actual_format(items),
        resolution_fallback=_job_resolution_fallback(items),
        created_at=job.created_at,
        updated_at=job.updated_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        elapsed_seconds=_elapsed_seconds(job.started_at, job.finished_at),
        items=[_read_job_item(item, job.download_dir) for item in items],
    )


def _read_job_item(item: JobItem, job_download_dir: str | None) -> JobItemRead:
    return JobItemRead(
        id=item.id,
        job_id=item.job_id,
        source_url=item.source_url,
        title=item.title,
        index=item.index,
        status=item.status,
        progress=item.progress,
        downloaded_bytes=item.downloaded_bytes,
        total_bytes=item.total_bytes,
        speed=item.speed,
        eta=item.eta,
        output_path=_project_output_path(item, job_download_dir),
        actual_width=item.actual_width,
        actual_height=item.actual_height,
        actual_format=item.actual_format,
        requested_resolution=item.requested_resolution,
        fallback_resolution=item.fallback_resolution,
        fallback_reason=item.fallback_reason,
        resolution_fallback=build_resolution_fallback(
            item.requested_resolution,
            item.fallback_resolution,
            item.status,
            item.fallback_reason,
        ),
        error=item.error,
        created_at=item.created_at,
        updated_at=item.updated_at,
        started_at=item.started_at,
        finished_at=item.finished_at,
        elapsed_seconds=_elapsed_seconds(item.started_at, item.finished_at),
    )


def _project_output_path(item: JobItem, job_download_dir: str | None) -> str | None:
    base_dir = Path(job_download_dir) if job_download_dir else None
    if item.output_path:
        resolved = resolve_existing_output_path(Path(item.output_path), base_dir)
        return str(resolved or item.output_path)
    discovered = discover_existing_output_path(item.source_url, base_dir)
    return str(discovered) if discovered else None


def _actual_resolution(items: list[JobItem]) -> str | None:
    resolutions = {
        (item.actual_width, item.actual_height)
        for item in items
        if item.actual_width is not None and item.actual_height is not None
    }
    if not resolutions:
        return None
    if len(resolutions) > 1:
        return "混合分辨率"
    width, height = next(iter(resolutions))
    return f"{width}x{height}"


def _actual_format(items: list[JobItem]) -> str | None:
    formats = {item.actual_format for item in items if item.actual_format}
    if not formats:
        return None
    if len(formats) > 1:
        return "混合格式"
    return next(iter(formats))


def _job_resolution_fallback(items: list[JobItem]) -> ResolutionFallback | None:
    if len(items) != 1:
        return None
    item = items[0]
    return build_resolution_fallback(
        item.requested_resolution,
        item.fallback_resolution,
        item.status,
        item.fallback_reason,
    )


def _job_error(job: Job, items: list[JobItem]) -> str | None:
    if len(items) == 1 and items[0].status == "failed" and items[0].error:
        return items[0].error
    return job.error


def _elapsed_seconds(started_at: datetime | None, finished_at: datetime | None) -> int:
    if not started_at:
        return 0
    start = _as_aware_utc(started_at)
    finish = _as_aware_utc(finished_at) if finished_at else utc_now()
    return max(0, int((finish - start).total_seconds()))


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
