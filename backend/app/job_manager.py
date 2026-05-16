import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from .config import AppSettings
from .events import EventBroker
from .models import Job, JobEvent, JobItem, JobStatus, utc_now
from .schemas import DownloadOptions
from .ytdlp_service import DownloadCancelled, YtDlpService


class JobManager:
    def __init__(self, engine: Engine, settings: AppSettings, service: YtDlpService, broker: EventBroker) -> None:
        self.engine = engine
        self.settings = settings
        self.service = service
        self.broker = broker
        self._queue: asyncio.Queue[str] | None = None
        self._workers: list[asyncio.Task[None]] = []
        self._cancelled: set[str] = set()
        self._paused: set[str] = set()
        self._deleted: set[str] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

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

    async def restart(self, job_id: str) -> None:
        self._paused.discard(job_id)
        self._cancelled.discard(job_id)
        self._deleted.discard(job_id)
        with Session(self.engine) as session:
            job = session.get(Job, job_id)
            if not job:
                return
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

    async def delete(self, job_id: str) -> None:
        self._deleted.add(job_id)
        self._paused.discard(job_id)
        self._cancelled.add(job_id)
        with Session(self.engine) as session:
            for event in session.exec(select(JobEvent).where(JobEvent.job_id == job_id)).all():
                session.delete(event)
            for item in session.exec(select(JobItem).where(JobItem.job_id == job_id)).all():
                session.delete(item)
            job = session.get(Job, job_id)
            if job:
                session.delete(job)
            session.commit()
        await self.broker.publish({"type": "job_deleted", "job_id": job_id})

    async def _worker(self, worker_index: int) -> None:
        assert self._queue is not None
        while True:
            job_id = await self._queue.get()
            try:
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
                self._run_item(session, job, item, options, download_dir)

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
        item.updated_at = now
        job.current_item_title = item.title
        job.updated_at = now
        session.add(item)
        session.add(job)
        session.commit()
        self._publish_threadsafe({"type": "item_started", "job_id": job.id, "item_id": item.id, "title": item.title})

        def progress_hook(payload: dict[str, Any]) -> None:
            if job.id in self._deleted:
                return
            with Session(self.engine) as hook_session:
                hook_item = hook_session.get(JobItem, item.id)
                hook_job = hook_session.get(Job, job.id)
                if not hook_item or not hook_job:
                    return
                status = payload.get("status")
                total = payload.get("total_bytes") or payload.get("total_bytes_estimate")
                downloaded = payload.get("downloaded_bytes")
                if downloaded is not None:
                    hook_item.downloaded_bytes = int(downloaded)
                if total is not None:
                    hook_item.total_bytes = int(total)
                    hook_item.progress = min(100.0, (float(downloaded or 0) / float(total)) * 100.0)
                if status == "finished":
                    hook_item.progress = 100.0
                    hook_item.output_path = payload.get("filename")
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
            self.service.download(
                item.source_url,
                options,
                progress_hook,
                should_cancel=lambda: job.id in self._cancelled or job.id in self._paused or job.id in self._deleted,
                cookies_path=self._cookies_path(),
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
            item.error = str(exc)
        else:
            item.status = JobStatus.succeeded.value
            item.progress = 100.0
        finally:
            if job.id in self._deleted:
                return
            item.finished_at = utc_now() if item.status != JobStatus.paused.value else None
            if item.status in {JobStatus.succeeded.value, JobStatus.failed.value, JobStatus.cancelled.value}:
                item.speed = None
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
            job.error = f"{job.failed_items} item(s) failed."
            job.finished_at = utc_now()
        else:
            job.status = JobStatus.succeeded.value
            job.progress = 100.0
            job.finished_at = utc_now()
        job.current_item_title = None
        job.speed = None
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
        job.completed_items = sum(1 for item in items if item.status == JobStatus.succeeded.value)
        job.failed_items = sum(1 for item in items if item.status == JobStatus.failed.value)
        if items:
            job.progress = sum(item.progress for item in items) / len(items)
        job.updated_at = utc_now()
        session.add(job)
        session.commit()

    def _calculate_job_progress(self, session: Session, job_id: str) -> float:
        items = session.exec(select(JobItem).where(JobItem.job_id == job_id)).all()
        if not items:
            return 0.0
        return sum(item.progress for item in items) / len(items)

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
