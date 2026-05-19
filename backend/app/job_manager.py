import asyncio
import json
import threading
from contextlib import suppress
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
        self._queue: asyncio.Queue[str | None] | None = None
        self._workers: list[asyncio.Task[None]] = []
        self._cancelled: set[str] = set()
        self._paused: set[str] = set()
        self._deleted: set[str] = set()
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
                item.options_json = None
                item.requested_resolution = None
                item.fallback_resolution = None
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
            item.options_json = (
                self._options_with_resolution(DownloadOptions.model_validate_json(job.options_json), resolution).model_dump_json()
                if resolution
                else None
            )
            item.requested_resolution = None
            item.fallback_resolution = None
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
                if delete_files and item.output_path:
                    output_paths.append(Path(item.output_path))
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
            for candidate in self._output_file_candidates(output_path):
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

    def _output_file_candidates(self, output_path: Path) -> list[Path]:
        sidecar_suffixes = [".description", ".info.json", ".jpg", ".jpeg", ".png", ".webp", ".srt", ".vtt"]
        return [output_path, *(output_path.with_suffix(suffix) for suffix in sidecar_suffixes)]

    def _is_under_allowed_root(self, path: Path, allowed_roots: list[Path]) -> bool:
        return any(path == root or root in path.parents for root in allowed_roots)

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
                    resolution = self.service.resolution_from_progress_payload(payload)
                    if resolution is None and hook_item.output_path:
                        resolution = self.service.detect_file_resolution(Path(hook_item.output_path))
                    if resolution is not None:
                        hook_item.actual_width, hook_item.actual_height = resolution
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
            self._download_with_cookie_refresh(
                item.source_url,
                options,
                progress_hook,
                should_cancel=lambda: job.id in self._cancelled or job.id in self._paused or job.id in self._deleted,
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
            self._annotate_resolution_fallback(item, options, exc)
        else:
            session.refresh(item)
            if item.actual_width is None and item.actual_height is None and item.output_path:
                resolution = self.service.detect_file_resolution(Path(item.output_path))
                if resolution is not None:
                    item.actual_width, item.actual_height = resolution
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

    def _item_options(self, item: JobItem, job_options: DownloadOptions) -> DownloadOptions:
        if not item.options_json:
            return job_options
        return DownloadOptions.model_validate(json.loads(item.options_json))

    def _options_with_resolution(self, options: DownloadOptions, resolution: str) -> DownloadOptions:
        return options.model_copy(update={"resolution": resolution, "format_id": None})

    def _annotate_resolution_fallback(self, item: JobItem, options: DownloadOptions, exc: Exception) -> None:
        if options.format_id or not YtDlpService.is_requested_format_unavailable_error(exc):
            return
        fallback = self._fallback_resolution_for_item(item, options)
        if not fallback:
            return
        item.requested_resolution = options.resolution
        item.fallback_resolution = fallback
        item.error = self._resolution_fallback_message(options.resolution, fallback)

    def _fallback_resolution_for_item(self, item: JobItem, options: DownloadOptions) -> str | None:
        try:
            analysis = self.service.extract_metadata(item.source_url, cookies_path=self._cookies_path())
        except Exception:
            return None
        return YtDlpService.suggest_lower_resolution(options.resolution, analysis.formats)

    def _resolution_fallback_message(self, requested_resolution: str, fallback_resolution: str) -> str:
        return f"当前没有 {requested_resolution} 的视频，低于选定分辨率的最高可用分辨率是 {fallback_resolution}。"

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
