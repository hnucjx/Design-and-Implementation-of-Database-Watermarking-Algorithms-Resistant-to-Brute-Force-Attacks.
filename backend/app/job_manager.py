import asyncio
import json
import logging
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from .config import AppSettings
from .download_progress import DownloadProgressAggregator
from .events import EventBroker
from .progress_persist import ProgressPersistGate
from .log_safety import sanitize_log_message
from .models import Job, JobEvent, JobItem, JobStatus, utc_now
from .output_paths import item_artifact_candidates, output_file_candidates, resolve_existing_output_path
from .resolution_decisions import (
    ResolutionDecision,
    ResolutionDecisionKind,
    decide_media_stream_fallback,
    decide_probe_fallback,
    decide_unavailable_format_fallback,
    media_stream_failure_message,
    should_look_for_fallback,
    unselectable_resolution_message,
)
from .schemas import DownloadOptions, FormatOption
from .transfer_stats import TransferStats
from .ytdlp_service import DownloadCancelled, YtDlpService


logger = logging.getLogger(__name__)

# 条目在收尾阶段（状态回写 / SSE 推送 / 诊断日志）崩溃时写给用户看的一句话。
# 刻意不提「内部错误」以外的猜测：此时连是哪一步崩的都要看日志，编一句话只会误导。
CRASHED_ITEM_ERROR = "内部错误：任务在收尾阶段异常退出，详情见日志。"


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
        self._runtime_restart_items: set[str] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cookie_import_lock = threading.Lock()
        self._item_claim_lock = threading.Lock()

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

    async def set_runtime_download_defaults(self, speed_limit_kbps: int | None, retries: int) -> None:
        restart_job_ids: set[str] = set()
        active_statuses = {JobStatus.queued.value, JobStatus.running.value, JobStatus.paused.value}
        restartable_item_statuses = active_statuses | {JobStatus.failed.value, JobStatus.cancelled.value}
        with Session(self.engine) as session:
            jobs = session.exec(select(Job).where(Job.status.in_(active_statuses))).all()
            for job in jobs:
                job.options_json = self._options_with_runtime_defaults(
                    DownloadOptions.model_validate_json(job.options_json),
                    speed_limit_kbps,
                    retries,
                ).model_dump_json()
                job.updated_at = utc_now()
                session.add(job)

                items = session.exec(select(JobItem).where(JobItem.job_id == job.id)).all()
                for item in items:
                    if item.options_json and item.status in restartable_item_statuses:
                        item.options_json = self._options_with_runtime_defaults(
                            DownloadOptions.model_validate_json(item.options_json),
                            speed_limit_kbps,
                            retries,
                        ).model_dump_json()
                        item.updated_at = utc_now()
                        session.add(item)
                    if item.status == JobStatus.running.value:
                        self._runtime_restart_items.add(item.id)
                        restart_job_ids.add(job.id)
            session.commit()

        for job_id in sorted(restart_job_ids):
            await self._publish(
                {
                    "type": "runtime_download_options_changed",
                    "job_id": job_id,
                    "speed_limit_kbps": speed_limit_kbps,
                    "retries": retries,
                }
            )

    async def enqueue(self, job_id: str) -> None:
        await self.start()
        assert self._queue is not None
        for item_id in self._queued_item_ids(job_id):
            await self._queue.put(item_id)
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
        for item_id in self._queued_item_ids(job_id):
            self._queue.put_nowait(item_id)

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
        self._queue.put_nowait(item_id)
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
        """条目可能关联的全部路径。候选链的唯一定义在 `output_paths.item_artifact_candidates`。"""
        return item_artifact_candidates(item.output_path, item.source_url, job_download_dir)

    async def _worker(self, worker_index: int) -> None:
        assert self._queue is not None
        while True:
            item_id = await self._queue.get()
            try:
                if item_id is None:
                    return
                await asyncio.to_thread(self._run_item_work, item_id)
            except Exception:  # noqa: BLE001
                # 单个条目的收尾/记录阶段崩溃，不能把这个 worker 带走：
                # worker 一死，队列就少一个消费口，后面的条目会静默堆在 queued 里
                # （表现为「并发数莫名其妙对不上」，而没有任何一行日志指到这里）。
                # 见 ai/bug-fix/008：这件兜底与「finally 里不再吞异常」是同一件事的两半。
                logger.exception("item worker crashed: worker=%s item_id=%s", worker_index, item_id)
                self._mark_item_failed_after_crash(item_id)
            finally:
                self._queue.task_done()

    def _mark_item_failed_after_crash(self, item_id: str) -> None:
        """把「收尾阶段崩掉的条目」变成一个用户能看见的失败。

        没有这一步的话，兜底只是让 worker 活下来，条目仍然永远停在 `running` ——
        问题从「静默卡住」变成「静默少一个并发」，两者都查不出来。

        整个过程再被包一层：兜底逻辑自己出错也只能记日志，它唯一的职责是不让 worker 死。
        """
        try:
            with Session(self.engine) as session:
                item = session.get(JobItem, item_id)
                if item is None or item.status not in {JobStatus.queued.value, JobStatus.running.value}:
                    return
                job = session.get(Job, item.job_id)
                item.status = JobStatus.failed.value
                item.error = CRASHED_ITEM_ERROR
                item.speed = None
                item.eta = None
                item.finished_at = utc_now()
                item.updated_at = item.finished_at
                session.add(item)
                session.commit()
                if job is not None:
                    self._refresh_job_counts(session, job)
                    self._maybe_finish_job(session, job)
                self._publish_threadsafe(
                    {
                        "type": "item_finished",
                        "job_id": item.job_id,
                        "item_id": item.id,
                        "status": item.status,
                        "error": item.error,
                    }
                )
        except Exception:  # noqa: BLE001
            logger.exception("failed to mark a crashed item as failed: item_id=%s", item_id)

    def _queued_item_ids(self, job_id: str) -> list[str]:
        with Session(self.engine) as session:
            items = session.exec(
                select(JobItem)
                .where(JobItem.job_id == job_id, JobItem.status == JobStatus.queued.value)
                .order_by(JobItem.index)
            ).all()
            return [item.id for item in items]

    def _run_item_work(self, item_id: str) -> None:
        with Session(self.engine) as session:
            item = session.get(JobItem, item_id)
            if not item:
                return
            job = session.get(Job, item.job_id)
            if not job:
                return
            if item.id in self._deleted_items or job.id in self._deleted:
                return
            with self._item_claim_lock:
                session.refresh(item)
                if item.status != JobStatus.queued.value:
                    return
                if job.id in self._paused:
                    item.status = JobStatus.paused.value
                    item.updated_at = utc_now()
                    session.add(item)
                    session.commit()
                    self._maybe_finish_job(session, job)
                    return
                if job.id in self._cancelled:
                    item.status = JobStatus.cancelled.value
                    item.updated_at = utc_now()
                    session.add(item)
                    session.commit()
                    self._maybe_finish_job(session, job)
                    return
                item.status = JobStatus.running.value
                item.updated_at = utc_now()
                session.add(item)
                session.commit()

            self._mark_job_running(session, job)
            options = DownloadOptions.model_validate(json.loads(job.options_json))
            download_dir = Path(job.download_dir) if job.download_dir else self.settings.download_dir
            self._run_item(session, job, item, self._item_options(item, options), download_dir)
            session.refresh(job)
            if job.id in self._deleted:
                return
            self._maybe_finish_job(session, job)

    def _mark_job_running(self, session: Session, job: Job) -> None:
        now = utc_now()
        already_running = job.status == JobStatus.running.value
        job.status = JobStatus.running.value
        job.started_at = job.started_at or now
        job.finished_at = None
        job.updated_at = now
        session.add(job)
        session.commit()
        if not already_running:
            self._publish_threadsafe({"type": "job_started", "job_id": job.id})

    def _maybe_finish_job(self, session: Session, job: Job) -> None:
        if job.id in self._deleted:
            return
        items = session.exec(select(JobItem).where(JobItem.job_id == job.id)).all()
        if not items:
            return
        running_items = [item for item in items if item.status == JobStatus.running.value]
        queued_items = [item for item in items if item.status == JobStatus.queued.value]
        if running_items:
            job.status = JobStatus.running.value
            job.current_item_title = running_items[0].title
            job.finished_at = None
            job.updated_at = utc_now()
            session.add(job)
            session.commit()
            return
        if queued_items and job.id not in self._paused and job.id not in self._cancelled:
            job.status = JobStatus.running.value
            job.finished_at = None
            job.updated_at = utc_now()
            session.add(job)
            session.commit()
            return
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
        persist_gate = ProgressPersistGate()
        runtime_restart_requested = False

        def progress_hook(payload: dict[str, Any]) -> None:
            if job.id in self._deleted:
                return
            status = payload.get("status")
            progress = progress_aggregator.update(payload)
            if progress.downloaded_bytes is not None:
                transfer_stats.record(progress.downloaded_bytes)
            if not persist_gate.allow(status=status, progress=progress.progress, now=time.monotonic()):
                return
            with Session(self.engine) as hook_session:
                hook_item = hook_session.get(JobItem, item.id)
                hook_job = hook_session.get(Job, job.id)
                if not hook_item or not hook_job:
                    return
                if progress.downloaded_bytes is not None:
                    hook_item.downloaded_bytes = progress.downloaded_bytes
                if progress.total_bytes is not None:
                    hook_item.total_bytes = max(hook_item.total_bytes or 0, progress.total_bytes)
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
            options = self._prepare_download(session, item, options)
            progress_aggregator.set_expected_total_bytes(item.total_bytes)
            should_cancel = (
                lambda: job.id in self._cancelled
                or job.id in self._paused
                or job.id in self._deleted
                or item.id in self._deleted_items
                or item.id in self._runtime_restart_items
            )
            self._download_with_cookie_refresh(
                item.source_url,
                options,
                progress_hook,
                should_cancel=should_cancel,
                download_dir=download_dir,
            )
        except DownloadCancelled:
            if item.id in self._runtime_restart_items:
                self._runtime_restart_items.discard(item.id)
                runtime_restart_requested = True
                item.status = JobStatus.queued.value
                item.progress = 0.0
                item.speed = None
                item.eta = None
                item.error = None
                item.started_at = None
                item.finished_at = None
            elif job.id in self._paused:
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
            # 这里**只允许**在分支里收尾，不允许用 `return` 提前退出：
            # `finally` 里的 `return` 会静默吞掉正在传播的异常（CPython 只给一句
            # SyntaxWarning: 'return' in a 'finally' block，而这条警告只在源码被重新
            # 编译、`__pycache__` 失效时才打印），结果是条目永远停在 running、
            # 日志里连异常都没有。见 ai/bug-fix/008。
            if job.id in self._deleted or item.id in self._deleted_items:
                # 删除竞态：任务/条目在下载过程中被删掉，状态行已不存在，不再回写。
                pass
            elif runtime_restart_requested:
                item.updated_at = utc_now()
                job.updated_at = item.updated_at
                session.add(item)
                session.add(job)
                session.commit()
                self._refresh_job_counts(session, job)
                self._publish_threadsafe(
                    {
                        "type": "item_requeued",
                        "job_id": job.id,
                        "item_id": item.id,
                        "reason": "runtime_download_options_changed",
                    }
                )
                self._enqueue_threadsafe(item.id)
            else:
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

    def _probe_preparation(self, item: JobItem, options: DownloadOptions) -> Any | None:
        """探测目标清晰度能否选出可下载组合。返回 `None` 表示**不可选**。

        「不可选」有两种表现形式，必须都当成否定结论：
        1. `prepare_download` 返回 `is_selectable=False`；
        2. `prepare_download` 抛「Requested format is not available」—— yt-dlp 在
           `extract_info` 内部就做格式选择，匹配不到直接抛 `DownloadError`。

        `YtDlpService.prepare_download` 已把 (2) 归一成 (1)；这里再兜一层，让任何一个
        service 实现都不能把降级分支变成死代码。**其余异常继续抛出**：那些是真实故障
        （网络、JS challenge、cookies 失效），被降级逻辑吞掉只会让病因更难查。见 ai/bug-fix/010。
        """
        try:
            preparation = self.service.prepare_download(
                item.source_url,
                options,
                cookies_path=self._cookies_path(),
            )
        except Exception as exc:
            if not YtDlpService.is_requested_format_unavailable_error(exc):
                raise
            return None
        if not preparation.is_selectable:
            return None
        return preparation

    def _prepare_download(self, session: Session, item: JobItem, options: DownloadOptions) -> DownloadOptions:
        """探测目标清晰度；不可选时按 `resolution_decisions` 的判定降级。

        本方法只保留**IO 与时序**：探测、取元数据、写状态、提交。判定全部在
        [`resolution_decisions`](resolution_decisions.py) 里，那里可被单测。
        """
        preparation = self._probe_preparation(item, options)
        if preparation is not None:
            self._apply_download_preparation(session, item, preparation)
            return options

        # 早退必须在取元数据**之前**：这两种情况不需要元数据，多取一次就是白付一次网络开销。
        if not should_look_for_fallback(options):
            return options

        analysis_formats = self._extract_formats_or_none(item)
        decision = decide_probe_fallback(options.resolution, analysis_formats)
        if decision.kind is ResolutionDecisionKind.fail:
            raise RuntimeError(decision.message)
        if decision.kind is ResolutionDecisionKind.skip:
            return options

        fallback_options = self._options_with_resolution(options, decision.fallback_resolution)
        fallback_preparation = self._probe_preparation(item, fallback_options)
        if fallback_preparation is None:
            raise RuntimeError(unselectable_resolution_message(options.resolution))

        self._set_resolution_fallback(item, decision, options.resolution)
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

    def _options_with_runtime_defaults(
        self,
        options: DownloadOptions,
        speed_limit_kbps: int | None,
        retries: int,
    ) -> DownloadOptions:
        return options.model_copy(update={"speed_limit_kbps": speed_limit_kbps, "retries": retries})

    def _annotate_media_stream_fallback(self, item: JobItem, options: DownloadOptions) -> None:
        """媒体流 403 / 连接重置后，标注一个可重启的清晰度。判定见 `resolution_decisions`。

        注意本方法**不碰 `item.error`**：媒体流失败的文案由 `_media_stream_failure_message` 给，
        它要带上 cookies 状态，是另一件事。
        """
        if options.format_id:
            return
        decision = decide_media_stream_fallback(options.resolution, self._extract_formats_or_none(item))
        if decision.kind is not ResolutionDecisionKind.fallback:
            return
        self._set_resolution_fallback(item, decision, options.resolution)

    def _annotate_resolution_fallback(self, item: JobItem, options: DownloadOptions, exc: Exception) -> None:
        """「Requested format is not available」失败后标注降级，并改写 `item.error`。

        为什么要改写 `item.error`：走到这里时任务已经 `failed`，用户看到的原因必须能解释
        "为什么降过级仍然失败"。判定与文案都来自 `resolution_decisions`。
        """
        if options.format_id or not YtDlpService.is_requested_format_unavailable_error(exc):
            return
        decision = decide_unavailable_format_fallback(options.resolution, self._extract_formats_or_none(item))
        if decision.kind is not ResolutionDecisionKind.fallback:
            return
        self._set_resolution_fallback(item, decision, options.resolution)
        # 该路径上的 `message` 保证非空（由 `decide_unavailable_format_fallback` 负责）。
        item.error = decision.message

    def _extract_formats_or_none(self, item: JobItem) -> list[FormatOption] | None:
        """取一次元数据，返回可用格式；**取不到返回 `None` 而不是空列表**。

        `None` 与 `[]` 表示的是两件事（"这次没取到" vs "取到了但没有格式"），类型上分开是为了不让
        调用方把前者顺手写成后者。**但 `resolution_decisions` 里三条判定路径对两者的结论当前相同**，
        所以这不是一处行为差异，而是接口约定 + 一道防止语义漂移的栅栏。
        """
        try:
            analysis = self.service.extract_metadata(item.source_url, cookies_path=self._cookies_path())
        except Exception:
            return None
        return list(analysis.formats)

    def _set_resolution_fallback(
        self,
        item: JobItem,
        decision: ResolutionDecision,
        requested_resolution: str,
    ) -> None:
        """把降级决策写回条目。调用前必须已确认 `decision.kind is fallback`。"""
        item.requested_resolution = requested_resolution
        item.fallback_resolution = decision.fallback_resolution
        item.fallback_reason = decision.reason

    def _media_stream_failure_message(self) -> str:
        cookie_state = "已配置" if self.settings.cookies_path.exists() else "未配置"
        return media_stream_failure_message(cookie_state)

    def _log_item_failure(self, item: JobItem, options: DownloadOptions, exc: Exception) -> None:
        if YtDlpService.is_js_challenge_error(exc):
            category = "js_challenge_failed"
        elif YtDlpService.is_cookie_required_error(exc):
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
        # 同一处补一条可执行诊断：原始报错（如 “The page needs to be reloaded.”）指不到病因，
        # 而这条日志给出了原因与下一步，用户不必再回来问。
        # 整段都被保护：**日志不能把状态机搞挂**。测试里的 fake service 没有这个方法，
        # 真服务将来若改了签名也一样 —— 任何情况下都只能少打一条日志。
        try:
            advise = getattr(self.service, "advise_failure", None)
            advice = advise(exc, self._cookies_path()) if callable(advise) else None
        except Exception:  # noqa: BLE001
            advice = None
        if advice:
            logger.warning(
                "download item failed advice: job_id=%s item_id=%s\n%s",
                item.job_id,
                item.id,
                advice.to_log_block(),
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

    def _enqueue_threadsafe(self, item_id: str) -> None:
        if self._loop and self._loop.is_running() and self._queue is not None:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, item_id)


def new_id() -> str:
    return str(uuid4())
