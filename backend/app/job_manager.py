import asyncio
import json
import logging
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from .config import AppSettings
from .download_progress import DownloadProgressAggregator
from .events import EventBroker
from .fallback_policy import (
    MEDIA_STREAM_BLOCKED,
    REQUESTED_RESOLUTION_MISSING,
    REQUESTED_RESOLUTION_UNSELECTABLE,
    SOURCE_BELOW_720_ONLY,
)
from .log_safety import sanitize_log_message
from .models import Job, JobEvent, JobItem, JobStatus, utc_now
from .output_paths import discover_output_file_candidates, output_file_candidates, resolve_existing_output_path
from .schemas import DownloadOptions
from .transfer_stats import TransferStats
from .ytdlp_service import DownloadCancelled, MIN_AUTO_FALLBACK_HEIGHT, YtDlpService


logger = logging.getLogger(__name__)


class JobManager:
    def __init__(self, engine: Engine, settings: AppSettings, service: YtDlpService, broker: EventBroker) -> None:
        self.engine = engine
        self.settings = settings
        self.service = service
        self.broker = broker
        self._queue: asyncio.Queue[str | None] | None = None
        self._workers: list[asyncio.Task[None]] = []
        self._cancelled: set[str] = set()
        self._paused: set[str] = set()
        self._deleted: set[str] = set()
        self._deleted_items: set[str] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cookie_import_lock = threading.Lock()

    async def start(self) -> None:
        if self._queue is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        for worker_index in range(max(1, self.settings.default_concurrency)):
            self._workers.append(asyncio.create_task(self._worker(worker_index)))

    async def stop(self) -> None:
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._queue = None

    async def set_concurrency(self, concurrency: int) -> None:
        self.settings.default_concurrency = max(1, concurrency)
        if self._queue is None:
            return
        self._workers = [worker for worker in self._workers if not worker.done()]
        desired = self.settings.default_concurrency
        current = len(self._workers)
        if desired > current:
            for worker_index in range(current, desired):
                self._workers.append(asyncio.create_task(self._worker(worker_index)))
            return
        for _ in range(current - desired):
            await self._queue.put(None)

    async def enqueue(self, job_id: str) -> None:
        await self.start()
        assert self._queue is not None
        await self._queue.put(job_id)
        await self._publish({"type": "job_queued", "job_id": job_id})

    async def cancel(self, job_id: str) -> None:
        self._cancelled.add(job_id)
        with Session(self.engine) as session:
            job = session.get(Job, job_id)
            if job and job.status == JobStatus.queued.value:
                job.status = JobStatus.cancelled.value
                job.updated_at = utc_now()
                session.add(job)
                session.commit()
        await self._publish({"type": "job_cancel_requested", "job_id": job_id})

    async def pause(self, job_id: str) -> None:
        self._paused.add(job_id)
        self._cancelled.discard(job_id)
        with Session(self.engine) as session:
            job = session.get(Job, job_id)
            if not job:
                return
            job.status = JobStatus.paused.value
            job.current_item_title = None
            job.speed = None
            job.eta = None
            job.finished_at = None
            job.updated_at = utc_now()
            session.add(job)
            items = session.exec(select(JobItem).where(JobItem.job_id == job_id)).all()
            for item in items:
                if item.status in {JobStatus.queued.value, JobStatus.running.value}:
                    item.status = JobStatus.paused.value
                    item.error = None
                    item.updated_at = utc_now()
                    session.add(item)
            session.commit()
        await self._publish({"type": "job_paused", "job_id": job_id})

    async def restart(self, job_id: str, resolution: str | None = None) -> None:
        self._paused.discard(job_id)
        self._cancelled.discard(job_id)
        self._deleted.discard(job_id)
        with Session(self.engine) as session:
            job = session.get(Job, job_id)
            if not job:
                return
            if resolution:
                options = self._options_with_resolution(DownloadOptions.model_validate_json(job.options_json), resolution)
                job.options_json = options.model_dump_json()
            job.status = JobStatus.queued.value
            job.progress = 0.0
            job.speed = None
            job.eta = None
            job.completed_items = 0
            job.failed_items = 0
            job.current_item_title = None
            job.error = None
            job.started_at = None
            job.finished_at = None
            job.updated_at = utc_now()
            session.add(job)
            items = session.exec(select(JobItem).where(JobItem.job_id == job_id)).all()
            for item in items:
                item.status = JobStatus.queued.value
                item.progress = 0.0
                item.downloaded_bytes = None
                item.total_bytes = None
                item.speed = None
                item.eta = None
                item.output_path = None
                item.actual_width = None
                item.actual_height = None
                item.actual_format = None
                item.options_json = None
                item.requested_resolution = None
                item.fallback_resolution = None
                item.fallback_reason = None
                item.error = None
                item.started_at = None
                item.finished_at = None
                item.updated_at = utc_now()
                session.add(item)
            session.commit()
        await self.start()
        await self._publish({"type": "job_restarted", "job_id": job_id})
        assert self._queue is not None
        self._queue.put_nowait(job_id)

    async def restart_item(self, job_id: str, item_id: str, resolution: str | None = None) -> bool:
        self._paused.discard(job_id)
        self._cancelled.discard(job_id)
        self._deleted.discard(job_id)
        with Session(self.engine) as session:
            job = session.get(Job, job_id)
            item = session.get(JobItem, item_id)
            if not job or not item or item.job_id != job_id:
                return False
            item.status = JobStatus.queued.value
            item.progress = 0.0
            item.downloaded_bytes = None
            item.total_bytes = None
            item.speed = None
            item.eta = None
            item.output_path = None
            item.actual_width = None
            item.actual_height = None
            item.actual_format = None
            item.options_json = (
                self._options_with_resolution(DownloadOptions.model_validate_json(job.options_json), resolution).model_dump_json()
                if resolution
                else None
            )
            item.requested_resolution = None
            item.fallback_resolution = None
            item.fallback_reason = None
            item.error = None
            item.started_at = None
            item.finished_at = None
            item.updated_at = utc_now()
            job.status = JobStatus.queued.value
            job.speed = None
            job.eta = None
            job.current_item_title = None
            job.error = None
            job.started_at = None
            job.finished_at = None
            job.updated_at = utc_now()
            session.add(item)
            session.add(job)
            session.commit()
            self._refresh_job_counts(session, job)
        await self.start()
        await self._publish({"type": "item_restarted", "job_id": job_id, "item_id": item_id})
        assert self._queue is not None
        self._queue.put_nowait(job_id)
        return True

    async def delete_items(
        self,
        job_id: str,
        item_ids: list[str],
        delete_files: bool = False,
    ) -> tuple[list[str], bool]:
        requested_item_ids = set(item_ids)
        deleted_item_ids: list[str] = []
        output_paths: list[Path] = []
        job_download_dir: Path | None = None
        job_deleted = False
        with Session(self.engine) as session:
            job = session.get(Job, job_id)
            if not job:
                return [], False
            if job.download_dir:
                job_download_dir = Path(job.download_dir)
            items = session.exec(select(JobItem).where(JobItem.job_id == job_id)).all()
            targets = [item for item in items if item.id in requested_item_ids]
            if not targets:
                return [], False
            for item in targets:
                self._deleted_items.add(item.id)
                deleted_item_ids.append(item.id)
                if delete_files:
                    output_paths.extend(self._item_output_paths(item, job_download_dir))
                for event in session.exec(select(JobEvent).where(JobEvent.item_id == item.id)).all():
                    session.delete(event)
                session.delete(item)
            session.commit()

            remaining = session.exec(select(JobItem).where(JobItem.job_id == job_id)).all()
            if not remaining:
                for event in session.exec(select(JobEvent).where(JobEvent.job_id == job_id)).all():
                    session.delete(event)
                session.delete(job)
                session.commit()
                self._deleted.add(job_id)
                job_deleted = True
            else:
                self._recalculate_job_after_item_delete(session, job)

        if delete_files:
            self._delete_output_files(output_paths, job_download_dir)
        event_type = "job_deleted" if job_deleted else "items_deleted"
        await self.broker.publish(
            {
                "type": event_type,
                "job_id": job_id,
                "item_ids": deleted_item_ids,
                "job_deleted": job_deleted,
            }
        )
        return deleted_item_ids, job_deleted

    async def delete(self, job_id: str, delete_files: bool = False) -> None:
        self._deleted.add(job_id)
        self._paused.discard(job_id)
        self._cancelled.add(job_id)
        output_paths: list[Path] = []
        job_download_dir: Path | None = None
        with Session(self.engine) as session:
            job = session.get(Job, job_id)
            if job and job.download_dir:
                job_download_dir = Path(job.download_dir)
            for event in session.exec(select(JobEvent).where(JobEvent.job_id == job_id)).all():
                session.delete(event)
            for item in session.exec(select(JobItem).where(JobItem.job_id == job_id)).all():
                self._deleted_items.add(item.id)
                if delete_files:
                    output_paths.extend(self._item_output_paths(item, job_download_dir))
                session.delete(item)
            if job:
                session.delete(job)
            session.commit()
        if delete_files:
            self._delete_output_files(output_paths, job_download_dir)
        await self.broker.publish({"type": "job_deleted", "job_id": job_id})

    def _delete_output_files(self, output_paths: list[Path], job_download_dir: Path | None) -> None:
        download_root = self.settings.download_dir.expanduser().resolve()
        allowed_roots = [download_root]
        if job_download_dir:
            with suppress(OSError):
                allowed_roots.append(job_download_dir.expanduser().resolve())

        for output_path in output_paths:
            for candidate in output_file_candidates(output_path, job_download_dir):
                with suppress(OSError):
                    resolved = candidate.expanduser().resolve()
                    if not self._is_under_allowed_root(resolved, allowed_roots):
                        continue
                    if resolved.is_file():
                        resolved.unlink()

        if job_download_dir:
            with suppress(OSError):
                resolved_dir = job_download_dir.expanduser().resolve()
                if resolved_dir != download_root and download_root in resolved_dir.parents and resolved_dir.exists():
                    resolved_dir.rmdir()

    def _is_under_allowed_root(self, path: Path, allowed_roots: list[Path]) -> bool:
        return any(path == root or root in path.parents for root in allowed_roots)

    def _item_output_paths(self, item: JobItem, job_download_dir: Path | None) -> list[Path]:
        paths: list[Path] = []
        if item.output_path:
            paths.append(Path(item.output_path))
        paths.extend(discover_output_file_candidates(item.source_url, job_download_dir))
        deduped: list[Path] = []
        seen: set[Path] = set()
        for path in paths:
            if path in seen:
                continue
            seen.add(path)
            deduped.append(path)
        return deduped

    async def _worker(self, worker_index: int) -> None:
        assert self._queue is not None
        while True:
            job_id = await self._queue.get()
            try:
                if job_id is None:
                    return
                await asyncio.to_thread(self._run_job_sync, job_id)
            finally:
                self._queue.task_done()

    def _run_job_sync(self, job_id: str) -> None:
        with Session(self.engine) as session:
            job = session.get(Job, job_id)
            if not job:
                return
            if job_id in self._deleted:
                return
            if job_id in self._paused:
                self._mark_job_paused(session, job)
                return
            if job_id in self._cancelled:
                self._mark_job_cancelled(session, job)
                return
            now = utc_now()
            job.status = JobStatus.running.value
            job.started_at = job.started_at or now
            job.finished_at = None
            job.updated_at = now
            session.add(job)
            session.commit()
            self._publish_threadsafe({"type": "job_started", "job_id": job_id})

            items = session.exec(select(JobItem).where(JobItem.job_id == job_id).order_by(JobItem.index)).all()
            options = DownloadOptions.model_validate(json.loads(job.options_json))
            download_dir = Path(job.download_dir) if job.download_dir else self.settings.download_dir
            for item in items:
                if item.status != JobStatus.queued.value:
                    continue
                if item.id in self._deleted_items:
                    continue
                if job_id in self._deleted:
                    return
                if job_id in self._paused:
                    item.status = JobStatus.paused.value
                    item.updated_at = utc_now()
                    session.add(item)
                    session.commit()
                    break
                if job_id in self._cancelled:
                    item.status = JobStatus.cancelled.value
                    item.updated_at = utc_now()
                    session.add(item)
                    session.commit()
                    break
                self._run_item(session, job, item, self._item_options(item, options), download_dir)

            self._finish_job(session, job)

    def _run_item(
        self,
        session: Session,
        job: Job,
        item: JobItem,
        options: DownloadOptions,
        download_dir: Path,
    ) -> None:
        now = utc_now()
        item.status = JobStatus.running.value
        item.progress = 0.0
        item.started_at = item.started_at or now
        item.finished_at = None
        item.speed = None
        item.eta = None
        item.actual_width = None
        item.actual_height = None
        item.actual_format = None
        item.requested_resolution = None
        item.fallback_resolution = None
        item.fallback_reason = None
        item.updated_at = now
        job.current_item_title = item.title
        job.updated_at = now
        session.add(item)
        session.add(job)
        session.commit()
        self._publish_threadsafe({"type": "item_started", "job_id": job.id, "item_id": item.id, "title": item.title})
        transfer_stats = TransferStats()
        progress_aggregator = DownloadProgressAggregator()

        def progress_hook(payload: dict[str, Any]) -> None:
            if job.id in self._deleted:
                return
            with Session(self.engine) as hook_session:
                hook_item = hook_session.get(JobItem, item.id)
                hook_job = hook_session.get(Job, job.id)
                if not hook_item or not hook_job:
                    return
                status = payload.get("status")
                progress = progress_aggregator.update(payload)
                if progress.downloaded_bytes is not None:
                    transfer_stats.record(progress.downloaded_bytes)
                    hook_item.downloaded_bytes = progress.downloaded_bytes
                if progress.total_bytes is not None:
                    hook_item.total_bytes = progress.total_bytes
                hook_item.progress = progress.progress
                if status == "finished" and self._is_combined_format_payload(payload):
                    resolution = self.service.resolution_from_progress_payload(payload)
                    if resolution is None and hook_item.output_path:
                        resolution = self.service.detect_file_resolution(Path(hook_item.output_path))
                    if resolution is not None:
                        hook_item.actual_width, hook_item.actual_height = resolution
                    hook_item.actual_format = self.service.actual_format_from_progress_payload(payload)
                    if hook_item.actual_format is None and hook_item.output_path:
                        hook_item.actual_format = self._format_from_output_path(Path(hook_item.output_path))
                hook_item.speed = payload.get("speed")
                hook_item.eta = payload.get("eta")
                hook_item.updated_at = utc_now()
                hook_job.progress = self._calculate_job_progress(hook_session, hook_job.id)
                hook_job.speed = hook_item.speed
                hook_job.eta = hook_item.eta
                hook_job.updated_at = utc_now()
                hook_session.add(hook_item)
                hook_session.add(hook_job)
                hook_session.commit()
                self._publish_threadsafe(
                    {
                        "type": "item_progress",
                        "job_id": hook_job.id,
                        "item_id": hook_item.id,
                        "status": status,
                        "progress": hook_item.progress,
                        "speed": hook_item.speed,
                        "eta": hook_item.eta,
                    }
                )

        try:
            options = self._options_for_available_resolution(session, item, options)
            options = self._prepare_download(session, item, options)
            should_cancel = (
                lambda: job.id in self._cancelled
                or job.id in self._paused
                or job.id in self._deleted
                or item.id in self._deleted_items
            )
            self._download_with_cookie_refresh(
                item.source_url,
                options,
                progress_hook,
                should_cancel=should_cancel,
                download_dir=download_dir,
            )
        except DownloadCancelled:
            if job.id in self._paused:
                item.status = JobStatus.paused.value
                item.error = None
            elif job.id in self._deleted:
                return
            else:
                item.status = JobStatus.cancelled.value
                item.error = "Cancelled"
        except Exception as exc:
            item.status = JobStatus.failed.value
            if YtDlpService.is_media_stream_blocked_error(exc):
                item.error = self._media_stream_failure_message()
                self._annotate_media_stream_fallback(item, options)
            else:
                item.error = YtDlpService.readable_error_message(exc)
                self._annotate_resolution_fallback(item, options, exc)
            self._log_item_failure(item, options, exc)
        else:
            if item.id in self._deleted_items:
                return
            session.refresh(item)
            if item.output_path is None and progress_aggregator.output_path:
                progress_output_path = Path(progress_aggregator.output_path)
                if not progress_output_path.is_absolute():
                    progress_output_path = download_dir / progress_output_path
                item.output_path = str(
                    resolve_existing_output_path(progress_output_path)
                    or progress_output_path
                )
            if item.actual_width is None and item.actual_height is None and item.output_path:
                resolution = self.service.detect_file_resolution(Path(item.output_path))
                if resolution is not None:
                    item.actual_width, item.actual_height = resolution
            if item.actual_format is None and item.output_path:
                item.actual_format = self._format_from_output_path(Path(item.output_path))
            item.status = JobStatus.succeeded.value
            item.progress = 100.0
        finally:
            if job.id in self._deleted or item.id in self._deleted_items:
                return
            item.finished_at = utc_now() if item.status != JobStatus.paused.value else None
            if item.status in {JobStatus.succeeded.value, JobStatus.failed.value, JobStatus.cancelled.value}:
                item.speed = transfer_stats.average_speed()
                item.eta = None
            item.updated_at = utc_now()
            session.add(item)
            session.commit()
            self._refresh_job_counts(session, job)
            self._publish_threadsafe(
                {
                    "type": "item_finished",
                    "job_id": job.id,
                    "item_id": item.id,
                    "status": item.status,
                    "error": item.error,
                }
            )

    def _download_with_cookie_refresh(
        self,
        url: str,
        options: DownloadOptions,
        progress_hook,
        should_cancel,
        download_dir: Path,
    ) -> None:
        try:
            self.service.download(
                url,
                options,
                progress_hook,
                should_cancel=should_cancel,
                cookies_path=self._cookies_path(),
                download_dir=download_dir,
            )
        except Exception as exc:
            if not YtDlpService.is_cookie_required_error(exc):
                raise
            try:
                self._import_browser_cookies_after_cookie_error()
            except Exception as import_exc:
                raise RuntimeError(self._cookie_refresh_failed_message(exc, import_exc)) from import_exc
            try:
                self.service.download(
                    url,
                    options,
                    progress_hook,
                    should_cancel=should_cancel,
                    cookies_path=self._cookies_path(),
                    download_dir=download_dir,
                )
            except Exception as retry_exc:
                if YtDlpService.is_cookie_required_error(retry_exc):
                    raise RuntimeError(self._cookie_refresh_did_not_satisfy_youtube_message(retry_exc)) from retry_exc
                raise

    def _import_browser_cookies_after_cookie_error(self) -> None:
        with self._cookie_import_lock:
            self.service.import_browser_cookies("auto", self.settings.cookies_path)

    def _cookie_refresh_failed_message(self, original_error: Exception, import_error: Exception) -> str:
        return (
            "YouTube 要求重新登录或通过 bot 校验，后台已尝试刷新浏览器 cookies，但自动导入失败："
            f"{import_error}。请确认浏览器已登录 YouTube 后，在解析面板点击“从浏览器导入”；"
            "如果提示 Edge cookies 被锁定，请使用“关闭 Edge 并导入”。"
            f" 原始 yt-dlp 错误：{original_error}"
        )

    def _cookie_refresh_did_not_satisfy_youtube_message(self, retry_error: Exception) -> str:
        return (
            "后台已重新导入浏览器 cookies 并重试，但 YouTube 仍要求登录或 bot 校验。"
            "请在浏览器确认账号可正常播放该视频，重新导入 cookies，或手动上传有效的 cookies.txt。"
            f" yt-dlp 错误：{retry_error}"
        )

    def _finish_job(self, session: Session, job: Job) -> None:
        if job.id in self._deleted:
            return
        self._refresh_job_counts(session, job)
        if job.id in self._paused:
            job.status = JobStatus.paused.value
            job.error = None
            job.finished_at = None
        elif job.id in self._cancelled:
            job.status = JobStatus.cancelled.value
            job.finished_at = utc_now()
        elif job.failed_items:
            job.status = JobStatus.failed.value
            job.error = self._job_error_message(session, job)
            job.finished_at = utc_now()
        else:
            job.status = JobStatus.succeeded.value
            job.progress = 100.0
            job.finished_at = utc_now()
        job.current_item_title = None
        job.speed = None if job.status == JobStatus.paused.value else self._terminal_job_speed(session, job.id)
        job.eta = None
        job.updated_at = utc_now()
        session.add(job)
        session.commit()
        self._publish_threadsafe({"type": "job_finished", "job_id": job.id, "status": job.status, "error": job.error})

    def _mark_job_cancelled(self, session: Session, job: Job) -> None:
        job.status = JobStatus.cancelled.value
        job.speed = None
        job.eta = None
        job.finished_at = utc_now()
        job.updated_at = utc_now()
        session.add(job)
        session.commit()
        self._publish_threadsafe({"type": "job_finished", "job_id": job.id, "status": job.status})

    def _mark_job_paused(self, session: Session, job: Job) -> None:
        job.status = JobStatus.paused.value
        job.current_item_title = None
        job.speed = None
        job.eta = None
        job.finished_at = None
        job.updated_at = utc_now()
        session.add(job)
        session.commit()
        self._publish_threadsafe({"type": "job_paused", "job_id": job.id, "status": job.status})

    def _refresh_job_counts(self, session: Session, job: Job) -> None:
        items = session.exec(select(JobItem).where(JobItem.job_id == job.id)).all()
        job.total_items = len(items)
        job.completed_items = sum(1 for item in items if item.status == JobStatus.succeeded.value)
        job.failed_items = sum(1 for item in items if item.status == JobStatus.failed.value)
        if items:
            job.progress = sum(item.progress for item in items) / len(items)
        else:
            job.progress = 0.0
        job.updated_at = utc_now()
        session.add(job)
        session.commit()

    def _recalculate_job_after_item_delete(self, session: Session, job: Job) -> None:
        items = session.exec(select(JobItem).where(JobItem.job_id == job.id)).all()
        self._refresh_job_counts(session, job)
        statuses = {item.status for item in items}
        running_item = next((item for item in items if item.status == JobStatus.running.value), None)
        if running_item:
            job.status = JobStatus.running.value
            job.current_item_title = running_item.title
            job.error = None
            job.finished_at = None
        elif JobStatus.failed.value in statuses:
            job.status = JobStatus.failed.value
            job.error = self._job_error_message(session, job)
            job.current_item_title = None
        elif items and all(item.status == JobStatus.succeeded.value for item in items):
            job.status = JobStatus.succeeded.value
            job.progress = 100.0
            job.error = None
            job.current_item_title = None
            job.finished_at = job.finished_at or utc_now()
        elif JobStatus.paused.value in statuses and statuses <= {JobStatus.paused.value, JobStatus.succeeded.value}:
            job.status = JobStatus.paused.value
            job.error = None
            job.current_item_title = None
        elif JobStatus.queued.value in statuses:
            job.status = JobStatus.queued.value
            job.error = None
            job.current_item_title = None
            job.finished_at = None
        elif JobStatus.cancelled.value in statuses:
            job.status = JobStatus.cancelled.value
            job.current_item_title = None
        job.updated_at = utc_now()
        session.add(job)
        session.commit()

    def _calculate_job_progress(self, session: Session, job_id: str) -> float:
        items = session.exec(select(JobItem).where(JobItem.job_id == job_id)).all()
        if not items:
            return 0.0
        return sum(item.progress for item in items) / len(items)

    def _terminal_job_speed(self, session: Session, job_id: str) -> float | None:
        items = session.exec(select(JobItem).where(JobItem.job_id == job_id)).all()
        speeds = [
            float(item.speed)
            for item in items
            if item.status in {JobStatus.succeeded.value, JobStatus.failed.value, JobStatus.cancelled.value}
            and item.speed is not None
        ]
        if not speeds:
            return None
        return sum(speeds) / len(speeds)

    def _job_error_message(self, session: Session, job: Job) -> str:
        items = session.exec(select(JobItem).where(JobItem.job_id == job.id)).all()
        if len(items) == 1 and items[0].error:
            return items[0].error
        return f"{job.failed_items} item(s) failed."

    def _item_options(self, item: JobItem, job_options: DownloadOptions) -> DownloadOptions:
        if not item.options_json:
            return job_options
        return DownloadOptions.model_validate(json.loads(item.options_json))

    def _options_for_available_resolution(
        self,
        session: Session,
        item: JobItem,
        options: DownloadOptions,
    ) -> DownloadOptions:
        if options.mode == "subtitles_only" or options.format_id:
            return options
        if YtDlpService._resolution_height(options.resolution) is None:
            return options

        analysis = self.service.extract_metadata(item.source_url, cookies_path=self._cookies_path())
        requested_height = YtDlpService._resolution_height(options.resolution)
        available_heights = {
            int(format.height)
            for format in analysis.formats
            if format.height is not None
        }
        if requested_height in available_heights:
            return options

        fallback = YtDlpService.suggest_lower_resolution(
            options.resolution,
            analysis.formats,
            allow_below_min_if_source_below_min=True,
        )
        if not fallback:
            raise RuntimeError(self._no_supported_fallback_message(options.resolution))

        reason = (
            SOURCE_BELOW_720_ONLY
            if not YtDlpService.has_resolution_at_or_above(analysis.formats)
            else REQUESTED_RESOLUTION_MISSING
        )
        self._set_resolution_fallback(item, options.resolution, fallback, reason)
        item.error = None
        item.updated_at = utc_now()
        session.add(item)
        session.commit()
        return self._options_with_resolution(options, fallback)

    def _prepare_download(self, session: Session, item: JobItem, options: DownloadOptions) -> DownloadOptions:
        preparation = self.service.prepare_download(item.source_url, options, cookies_path=self._cookies_path())
        if preparation.is_selectable:
            self._apply_download_preparation(session, item, preparation)
            return options
        if options.format_id or YtDlpService._resolution_height(options.resolution) is None:
            return options

        fallback = self._fallback_resolution_for_item(
            item,
            options,
            allow_below_min_if_source_below_min=False,
        )
        if not fallback:
            raise RuntimeError(self._unselectable_resolution_message(options.resolution))

        fallback_options = self._options_with_resolution(options, fallback)
        fallback_preparation = self.service.prepare_download(
            item.source_url,
            fallback_options,
            cookies_path=self._cookies_path(),
        )
        if not fallback_preparation.is_selectable:
            raise RuntimeError(self._unselectable_resolution_message(options.resolution))

        self._set_resolution_fallback(item, options.resolution, fallback, REQUESTED_RESOLUTION_UNSELECTABLE)
        item.error = None
        item.updated_at = utc_now()
        session.add(item)
        session.commit()
        self._apply_download_preparation(session, item, fallback_preparation)
        return fallback_options

    def _apply_download_preparation(self, session: Session, item: JobItem, preparation: Any) -> None:
        if preparation.width is not None and preparation.height is not None:
            item.actual_width = int(preparation.width)
            item.actual_height = int(preparation.height)
        if preparation.actual_format:
            item.actual_format = str(preparation.actual_format)
        filesize = getattr(preparation, "filesize", None)
        if filesize is not None and not item.total_bytes:
            item.total_bytes = int(filesize)
        item.updated_at = utc_now()
        session.add(item)
        session.commit()
        self._publish_threadsafe(
            {
                "type": "item_prepared",
                "job_id": item.job_id,
                "item_id": item.id,
                "actual_width": item.actual_width,
                "actual_height": item.actual_height,
                "actual_format": item.actual_format,
                "total_bytes": item.total_bytes,
            }
        )

    def _options_with_resolution(self, options: DownloadOptions, resolution: str) -> DownloadOptions:
        return options.model_copy(update={"resolution": resolution, "format_id": None})

    def _annotate_media_stream_fallback(self, item: JobItem, options: DownloadOptions) -> None:
        if options.format_id:
            return
        fallback = self._fallback_resolution_for_item(
            item,
            options,
            allow_below_min_if_source_below_min=True,
        )
        if not fallback:
            return
        self._set_resolution_fallback(item, options.resolution, fallback, MEDIA_STREAM_BLOCKED)

    def _annotate_resolution_fallback(self, item: JobItem, options: DownloadOptions, exc: Exception) -> None:
        if options.format_id or not YtDlpService.is_requested_format_unavailable_error(exc):
            return
        fallback = self._fallback_resolution_for_item(
            item,
            options,
            allow_below_min_if_source_below_min=False,
        )
        if not fallback:
            return
        self._set_resolution_fallback(item, options.resolution, fallback, REQUESTED_RESOLUTION_UNSELECTABLE)
        item.error = self._resolution_fallback_message(options.resolution, fallback)

    def _fallback_resolution_for_item(
        self,
        item: JobItem,
        options: DownloadOptions,
        allow_below_min_if_source_below_min: bool,
    ) -> str | None:
        try:
            analysis = self.service.extract_metadata(item.source_url, cookies_path=self._cookies_path())
        except Exception:
            return None
        return YtDlpService.suggest_lower_resolution(
            options.resolution,
            analysis.formats,
            allow_below_min_if_source_below_min=allow_below_min_if_source_below_min,
        )

    def _set_resolution_fallback(
        self,
        item: JobItem,
        requested_resolution: str,
        fallback_resolution: str,
        reason: str,
    ) -> None:
        item.requested_resolution = requested_resolution
        item.fallback_resolution = fallback_resolution
        item.fallback_reason = reason

    def _resolution_fallback_message(self, requested_resolution: str, fallback_resolution: str) -> str:
        return f"当前没有 {requested_resolution} 的视频，低于选定分辨率的最高可用分辨率是 {fallback_resolution}。"

    def _no_supported_fallback_message(self, requested_resolution: str) -> str:
        return f"当前没有 {requested_resolution} 的视频，也没有 {MIN_AUTO_FALLBACK_HEIGHT}p 或更高的可用降级清晰度。"

    def _unselectable_resolution_message(self, requested_resolution: str) -> str:
        return (
            f"检测到 {requested_resolution} 清晰度，但该清晰度当前没有可下载的视频/音频组合，"
            f"也没有 {MIN_AUTO_FALLBACK_HEIGHT}p 或更高的可用降级清晰度。"
        )

    def _media_stream_failure_message(self) -> str:
        return (
            "YouTube 拒绝了媒体流下载（HTTP 403）或重置了媒体流连接。后台已在当前清晰度下尝试 PO-token provider、"
            "浏览器 impersonation、断点续传和传输重试；请重新导入 cookies 后重试。若浏览器可正常播放但仍失败，"
            "请检查网络/代理是否能稳定访问 YouTube 媒体域名，或配置有效的 YouTube PO token。"
        )

    def _log_item_failure(self, item: JobItem, options: DownloadOptions, exc: Exception) -> None:
        if YtDlpService.is_cookie_required_error(exc):
            category = "cookie_required"
        elif YtDlpService.is_media_stream_blocked_error(exc):
            category = "media_stream_blocked"
        elif YtDlpService.is_requested_format_unavailable_error(exc):
            category = "format_unavailable"
        else:
            category = "download_failed"
        logger.warning(
            "download item failed: job_id=%s item_id=%s title=%r resolution=%s category=%s error_class=%s error=%s",
            item.job_id,
            item.id,
            item.title,
            options.resolution,
            category,
            type(exc).__name__,
            sanitize_log_message(YtDlpService.readable_error_message(exc)),
        )

    def _is_combined_format_payload(self, payload: dict[str, Any]) -> bool:
        info = payload.get("info_dict")
        return isinstance(info, dict) and bool(info.get("requested_formats"))

    def _format_from_output_path(self, output_path: Path) -> str | None:
        suffix = output_path.suffix.lower().lstrip(".")
        return suffix or None

    def _cookies_path(self) -> Path | None:
        path = self.settings.cookies_path
        return path if path.exists() else None

    async def _publish(self, payload: dict[str, Any]) -> None:
        with Session(self.engine) as session:
            session.add(
                JobEvent(
                    job_id=str(payload.get("job_id", "")),
                    item_id=payload.get("item_id"),
                    event_type=str(payload.get("type", "event")),
                    payload_json=json.dumps(payload, default=str),
                )
            )
            session.commit()
        await self.broker.publish(payload)

    def _publish_threadsafe(self, payload: dict[str, Any]) -> None:
        with Session(self.engine) as session:
            session.add(
                JobEvent(
                    job_id=str(payload.get("job_id", "")),
                    item_id=payload.get("item_id"),
                    event_type=str(payload.get("type", "event")),
                    payload_json=json.dumps(payload, default=str),
                )
            )
            session.commit()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(asyncio.create_task, self.broker.publish(payload))


def new_id() -> str:
    return str(uuid4())
