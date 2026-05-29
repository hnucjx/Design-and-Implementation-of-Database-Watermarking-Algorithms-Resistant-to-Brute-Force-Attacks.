import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Callable

from fastapi import Body, Depends, FastAPI, File, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select

from .config import AppSettings, REPO_ROOT, get_settings
from .db import create_app_engine, init_db, session_dependency
from .events import EventBroker
from .job_read_model import read_job
from .job_manager import JobManager, new_id
from .models import Job, JobItem, Setting
from .output_paths import discover_existing_output_path, discover_output_file_candidates, resolve_existing_output_path
from .paths import safe_path_name
from .schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    BrowserCookieImportRequest,
    CookieStatus,
    CreateJobRequest,
    DeleteJobItemsRequest,
    DeleteJobItemsResponse,
    DiagnosticsRead,
    JobBatchActionRequest,
    JobBatchActionResponse,
    JobRead,
    RestartJobRequest,
    SettingsRead,
    SettingsUpdate,
    VideoEntry,
)
from .system_open import LocalOpenError, open_path_with_default_app, open_video_with_best_player
from .ytdlp_service import BrowserCookieImportError, YtDlpService


def create_app(
    settings: AppSettings | None = None,
    ytdlp_service: YtDlpService | None = None,
    directory_picker: Callable[[Path], Path | None] | None = None,
    system_opener: Callable[[Path], None] | None = None,
    video_opener: Callable[[Path, str | None], None] | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    app_settings.ensure_directories()
    engine = create_app_engine(app_settings)
    init_db(engine)
    broker = EventBroker()
    service = ytdlp_service or YtDlpService(
        app_settings.download_dir,
        youtube_po_token=app_settings.youtube_po_token,
        youtube_visitor_data=app_settings.youtube_visitor_data,
        youtube_po_browser_path=app_settings.youtube_po_browser_path,
        anti403_http_chunk_size_mb=app_settings.anti403_http_chunk_size_mb,
        throttled_rate_kbps=app_settings.throttled_rate_kbps,
        aria2c_enabled=app_settings.aria2c_enabled,
        aria2c_path=app_settings.aria2c_path,
        aria2c_connections=app_settings.aria2c_connections,
    )
    with Session(engine) as session:
        _apply_stored_settings(session, app_settings, service)
    manager = JobManager(engine, app_settings, service, broker)
    get_session = session_dependency(engine)
    pick_directory = directory_picker or _select_directory_with_tkinter
    open_local_path = system_opener or open_path_with_default_app
    open_video_path = video_opener or (
        (lambda path, _actual_format=None: system_opener(path)) if system_opener else open_video_with_best_player
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await manager.start()
        try:
            yield
        finally:
            await manager.stop()

    app = FastAPI(title="YouTube Downloader", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    SessionDep = Annotated[Session, Depends(get_session)]

    def _extract_metadata_with_cookies(url: str, cookies_enabled: bool) -> AnalyzeResponse:
        cookies_path = app_settings.cookies_path if cookies_enabled and app_settings.cookies_path.exists() else None
        try:
            return service.extract_metadata(url, cookies_path=cookies_path)
        except Exception as exc:
            if not cookies_enabled or not _is_cookie_required_error(exc):
                raise
            try:
                service.import_browser_cookies("auto", app_settings.cookies_path)
            except BrowserCookieImportError:
                raise
            except Exception as import_exc:
                raise RuntimeError(f"{exc} Browser cookies import failed: {import_exc}") from import_exc
            return service.extract_metadata(url, cookies_path=app_settings.cookies_path)

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/diagnostics", response_model=DiagnosticsRead)
    def diagnostics() -> DiagnosticsRead:
        dependencies = (
            service.get_dependency_status()
            if hasattr(service, "get_dependency_status")
            else {**service.get_ffmpeg_status(), "yt_dlp_version": None, "js_runtime": False}
        )
        dependencies = {
            **dependencies,
            "youtube_max_parallel_downloads": app_settings.youtube_max_parallel_downloads,
            "anti403_http_chunk_size_mb": app_settings.anti403_http_chunk_size_mb,
            "throttled_rate_kbps": app_settings.throttled_rate_kbps,
        }
        return DiagnosticsRead(cookies_enabled=app_settings.cookies_path.exists(), dependencies=dependencies)

    @app.post("/api/analyze", response_model=AnalyzeResponse)
    def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
        try:
            return _extract_metadata_with_cookies(request.url, request.cookies_enabled)
        except BrowserCookieImportError as exc:
            raise HTTPException(status_code=_cookie_import_status_code(exc), detail=exc.to_detail()) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/jobs", response_model=JobRead, status_code=201)
    async def create_job(request: CreateJobRequest, session: SessionDep) -> JobRead:
        try:
            analysis = _extract_metadata_with_cookies(request.url, True)
        except BrowserCookieImportError as exc:
            raise HTTPException(status_code=_cookie_import_status_code(exc), detail=exc.to_detail()) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        entries = _selected_entries(request.url, analysis, request.options.playlist_items)
        if not entries:
            raise HTTPException(status_code=400, detail="No downloadable playlist entries were selected.")

        job_id = new_id()
        download_dir = _job_download_dir(app_settings.download_dir, analysis, job_id)
        job = Job(
            id=job_id,
            url=request.url,
            title=analysis.title,
            options_json=request.options.model_dump_json(),
            total_items=len(entries),
            download_dir=str(download_dir),
        )
        session.add(job)
        session.commit()
        for entry in entries:
            session.add(
                JobItem(
                    id=new_id(),
                    job_id=job.id,
                    source_url=entry.url,
                    title=entry.title,
                    index=entry.index,
                )
            )
        session.commit()
        await manager.enqueue(job.id)
        return _read_job(session, job.id)

    @app.get("/api/jobs", response_model=list[JobRead])
    def list_jobs(session: SessionDep) -> list[JobRead]:
        jobs = session.exec(select(Job).order_by(Job.created_at.desc())).all()
        return [_read_job(session, job.id) for job in jobs]

    @app.post("/api/jobs/batch", response_model=JobBatchActionResponse)
    async def batch_job_action(request: JobBatchActionRequest, session: SessionDep) -> JobBatchActionResponse:
        affected: list[str] = []
        for job_id in request.job_ids:
            if not session.get(Job, job_id):
                continue
            if request.action == "pause":
                await manager.pause(job_id)
            elif request.action == "restart":
                await manager.restart(job_id)
            else:
                await manager.delete(job_id, delete_files=request.delete_files)
            affected.append(job_id)

        if not affected:
            raise HTTPException(status_code=404, detail="No matching jobs found.")

        if request.action == "delete":
            return JobBatchActionResponse(affected_job_ids=affected, jobs=[])

        session.expire_all()
        return JobBatchActionResponse(affected_job_ids=affected, jobs=[_read_job(session, job_id) for job_id in affected])

    @app.get("/api/jobs/{job_id}", response_model=JobRead)
    def get_job(job_id: str, session: SessionDep) -> JobRead:
        return _read_job(session, job_id)

    @app.post("/api/jobs/{job_id}/cancel", response_model=JobRead)
    async def cancel_job(job_id: str, session: SessionDep) -> JobRead:
        if not session.get(Job, job_id):
            raise HTTPException(status_code=404, detail="Job not found.")
        await manager.cancel(job_id)
        return _read_job(session, job_id)

    @app.post("/api/jobs/{job_id}/pause", response_model=JobRead)
    async def pause_job(job_id: str, session: SessionDep) -> JobRead:
        if not session.get(Job, job_id):
            raise HTTPException(status_code=404, detail="Job not found.")
        await manager.pause(job_id)
        session.expire_all()
        return _read_job(session, job_id)

    @app.post("/api/jobs/{job_id}/restart", response_model=JobRead)
    async def restart_job(
        job_id: str,
        session: SessionDep,
        request: RestartJobRequest | None = Body(default=None),
    ) -> JobRead:
        if not session.get(Job, job_id):
            raise HTTPException(status_code=404, detail="Job not found.")
        await manager.restart(job_id, resolution=request.resolution if request else None)
        session.expire_all()
        return _read_job(session, job_id)

    @app.post("/api/jobs/{job_id}/items/{item_id}/restart", response_model=JobRead)
    async def restart_job_item(
        job_id: str,
        item_id: str,
        session: SessionDep,
        request: RestartJobRequest | None = Body(default=None),
    ) -> JobRead:
        if not await manager.restart_item(job_id, item_id, resolution=request.resolution if request else None):
            raise HTTPException(status_code=404, detail="Job item not found.")
        session.expire_all()
        return _read_job(session, job_id)

    @app.post("/api/jobs/{job_id}/play", status_code=204)
    async def play_job_video(job_id: str, session: SessionDep) -> Response:
        job = _require_job(session, job_id)
        item = _single_job_item(session, job)
        await _open_video_path(open_video_path, _output_file(item, job.download_dir), item.actual_format)
        return Response(status_code=204)

    @app.post("/api/jobs/{job_id}/open-folder", status_code=204)
    async def open_job_video_folder(job_id: str, session: SessionDep) -> Response:
        job = _require_job(session, job_id)
        await _open_local_path(open_local_path, _job_folder(session, job))
        return Response(status_code=204)

    @app.post("/api/jobs/{job_id}/items/{item_id}/play", status_code=204)
    async def play_job_item_video(job_id: str, item_id: str, session: SessionDep) -> Response:
        job = _require_job(session, job_id)
        item = _require_job_item(session, job_id, item_id)
        await _open_video_path(open_video_path, _output_file(item, job.download_dir), item.actual_format)
        return Response(status_code=204)

    @app.post("/api/jobs/{job_id}/items/{item_id}/open-folder", status_code=204)
    async def open_job_item_video_folder(job_id: str, item_id: str, session: SessionDep) -> Response:
        job = _require_job(session, job_id)
        item = _require_job_item(session, job_id, item_id)
        await _open_local_path(open_local_path, _output_folder(item, job.download_dir))
        return Response(status_code=204)

    @app.post("/api/jobs/{job_id}/items/delete", response_model=DeleteJobItemsResponse)
    async def delete_job_items(job_id: str, request: DeleteJobItemsRequest, session: SessionDep) -> DeleteJobItemsResponse:
        if not session.get(Job, job_id):
            raise HTTPException(status_code=404, detail="Job not found.")
        deleted_item_ids, job_deleted = await manager.delete_items(
            job_id,
            request.item_ids,
            delete_files=request.delete_files,
        )
        if not deleted_item_ids:
            raise HTTPException(status_code=404, detail="Job item not found.")
        if job_deleted:
            return DeleteJobItemsResponse(deleted_item_ids=deleted_item_ids, job_deleted=True, job=None)
        session.expire_all()
        return DeleteJobItemsResponse(
            deleted_item_ids=deleted_item_ids,
            job_deleted=False,
            job=_read_job(session, job_id),
        )

    @app.delete("/api/jobs/{job_id}", status_code=204)
    async def delete_job(job_id: str, session: SessionDep, delete_files: bool = False) -> Response:
        if not session.get(Job, job_id):
            raise HTTPException(status_code=404, detail="Job not found.")
        await manager.delete(job_id, delete_files=delete_files)
        return Response(status_code=204)

    @app.get("/api/events")
    async def events() -> StreamingResponse:
        async def event_stream():
            async for message in broker.subscribe():
                yield f"data: {message}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/api/settings", response_model=SettingsRead)
    def get_app_settings(session: SessionDep) -> SettingsRead:
        return _settings_response(session, app_settings, service)

    @app.put("/api/settings", response_model=SettingsRead)
    async def update_app_settings(update: SettingsUpdate, session: SessionDep) -> SettingsRead:
        if update.download_dir is not None:
            path = Path(update.download_dir).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            app_settings.download_dir = path
            service.download_dir = path
            _set_setting(session, "download_dir", str(path))
        if update.default_concurrency is not None:
            app_settings.default_concurrency = update.default_concurrency
            _set_setting(session, "default_concurrency", str(update.default_concurrency))
            await manager.set_concurrency(update.default_concurrency)
        if update.default_subtitle_languages is not None:
            app_settings.default_subtitle_languages = update.default_subtitle_languages
            _set_setting(session, "default_subtitle_languages", ",".join(update.default_subtitle_languages))
        if update.default_resolution is not None:
            app_settings.default_resolution = update.default_resolution
            _set_setting(session, "default_resolution", update.default_resolution)
        return _settings_response(session, app_settings, service)

    @app.post("/api/settings/download-dir/select", response_model=SettingsRead)
    async def select_download_dir(session: SessionDep) -> SettingsRead:
        _settings_response(session, app_settings, service)
        try:
            selected = await asyncio.to_thread(pick_directory, app_settings.download_dir)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if selected is None:
            return _settings_response(session, app_settings, service)

        path = Path(selected).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        app_settings.download_dir = path
        service.download_dir = path
        _set_setting(session, "download_dir", str(path))
        return _settings_response(session, app_settings, service)

    @app.post("/api/cookies", response_model=CookieStatus)
    async def upload_cookies(file: UploadFile = File(...)) -> CookieStatus:
        content = await file.read()
        app_settings.data_dir.mkdir(parents=True, exist_ok=True)
        app_settings.cookies_path.write_bytes(content)
        return CookieStatus(enabled=True, filename=file.filename or app_settings.cookies_filename, source="file")

    @app.post("/api/cookies/from-browser", response_model=CookieStatus)
    async def import_cookies_from_browser(request: BrowserCookieImportRequest) -> CookieStatus:
        try:
            result = await asyncio.to_thread(
                service.import_browser_cookies,
                request.browser,
                app_settings.cookies_path,
                request.close_browser_if_locked,
            )
        except BrowserCookieImportError as exc:
            raise HTTPException(status_code=_cookie_import_status_code(exc), detail=exc.to_detail()) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _cookie_status_from_import(result)

    @app.delete("/api/cookies", response_model=CookieStatus)
    def delete_cookies() -> CookieStatus:
        if app_settings.cookies_path.exists():
            app_settings.cookies_path.unlink()
        return CookieStatus(enabled=False, filename=None, source="none")

    frontend_dist = REPO_ROOT / "frontend" / "dist"
    frontend_assets = frontend_dist / "assets"
    frontend_index_path = frontend_dist / "index.html"
    if frontend_index_path.is_file() and frontend_assets.is_dir():
        app.mount("/assets", StaticFiles(directory=frontend_assets), name="assets")

        @app.get("/")
        def frontend_index() -> FileResponse:
            return FileResponse(frontend_index_path)

    return app


def _cookie_status_from_import(result: Any) -> CookieStatus:
    if isinstance(result, dict):
        return CookieStatus(**result)
    return CookieStatus(
        enabled=True,
        filename=getattr(result, "filename", None),
        source="browser",
        browser=getattr(result, "browser", None),
        imported_count=getattr(result, "imported_count", None),
    )


def _is_cookie_required_error(exc: Exception) -> bool:
    return YtDlpService.is_cookie_required_error(exc)


def _cookie_import_status_code(exc: BrowserCookieImportError) -> int:
    return 409 if exc.code == "browser_locked" else 400


def _selected_entries(url: str, analysis: AnalyzeResponse, playlist_items: list[int] | None) -> list[VideoEntry]:
    if analysis.is_playlist:
        selected = set(playlist_items or [])
        entries = analysis.entries
        if selected:
            entries = [entry for entry in entries if entry.index in selected]
        return entries
    return [
        VideoEntry(
            index=1,
            id=None,
            title=analysis.title,
            url=analysis.url or url,
            duration=analysis.duration,
            thumbnail=analysis.thumbnail,
        )
    ]


def _read_job(session: Session, job_id: str) -> JobRead:
    payload = read_job(session, job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return payload


def _require_job(session: Session, job_id: str) -> Job:
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


def _require_job_item(session: Session, job_id: str, item_id: str) -> JobItem:
    item = session.get(JobItem, item_id)
    if not item or item.job_id != job_id:
        raise HTTPException(status_code=404, detail="Job item not found.")
    return item


def _single_job_item(session: Session, job: Job) -> JobItem:
    items = session.exec(select(JobItem).where(JobItem.job_id == job.id).order_by(JobItem.index)).all()
    if len(items) != 1:
        raise HTTPException(status_code=409, detail="合集任务请打开具体视频。")
    return items[0]


def _output_file(item: JobItem, base_dir: str | Path | None = None) -> Path:
    base_path = Path(base_dir) if base_dir else None
    path = resolve_existing_output_path(Path(item.output_path), base_path) if item.output_path else None
    if path is None:
        path = discover_existing_output_path(item.source_url, base_path)
    if path is None:
        raise HTTPException(status_code=409, detail="视频文件尚不可用。")
    if not path.is_file():
        raise HTTPException(status_code=409, detail="视频文件不存在。")
    return path


def _item_folder(item: JobItem, base_dir: str | Path | None = None) -> Path:
    base_path = Path(base_dir) if base_dir else None
    path = resolve_existing_output_path(Path(item.output_path), base_path) if item.output_path else None
    if not path:
        discovered = discover_output_file_candidates(item.source_url, base_path)
        path = discovered[0] if discovered else None
    if path:
        folder = path.parent
        if folder.is_dir():
            return folder
    if base_path and base_path.is_dir():
        return base_path
    raise HTTPException(status_code=409, detail="视频文件夹不存在。")


def _output_folder(item: JobItem, base_dir: str | Path | None = None) -> Path:
    return _item_folder(item, base_dir)


def _job_folder(session: Session, job: Job) -> Path:
    items = session.exec(select(JobItem).where(JobItem.job_id == job.id).order_by(JobItem.index)).all()
    if len(items) == 1:
        return _output_folder(items[0], job.download_dir)
    if not job.download_dir:
        raise HTTPException(status_code=409, detail="合集文件夹尚不可用。")
    folder = Path(job.download_dir).expanduser()
    if not folder.is_dir():
        raise HTTPException(status_code=409, detail="合集文件夹不存在。")
    return folder

async def _open_local_path(path_opener: Callable[[Path], None], path: Path) -> None:
    try:
        await asyncio.to_thread(path_opener, path)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"无法打开本地路径：{exc}") from exc


async def _open_video_path(
    video_opener: Callable[[Path, str | None], None],
    path: Path,
    actual_format: str | None,
) -> None:
    try:
        await asyncio.to_thread(video_opener, path, actual_format)
    except HTTPException:
        raise
    except LocalOpenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"无法打开本地视频：{exc}") from exc


def _job_download_dir(root_dir: Path, analysis: AnalyzeResponse, job_id: str) -> Path:
    if not analysis.is_playlist:
        return root_dir
    folder = safe_path_name(analysis.title, fallback=f"playlist-{job_id[:8]}")
    return root_dir / folder


def _set_setting(session: Session, key: str, value: str) -> None:
    setting = session.get(Setting, key) or Setting(key=key, value=value)
    setting.value = value
    session.add(setting)
    session.commit()


def _select_directory_with_tkinter(initial_dir: Path) -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise RuntimeError("Folder dialog is unavailable in this environment.") from exc

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(initialdir=str(initial_dir), title="选择下载目录")
    finally:
        root.destroy()
    return Path(selected) if selected else None


def _apply_stored_settings(session: Session, settings: AppSettings, service: YtDlpService) -> None:
    stored = {setting.key: setting.value for setting in session.exec(select(Setting)).all()}
    if stored.get("download_dir"):
        settings.download_dir = Path(stored["download_dir"])
        service.download_dir = settings.download_dir
    if stored.get("default_concurrency"):
        settings.default_concurrency = int(stored["default_concurrency"])
    if stored.get("default_resolution"):
        settings.default_resolution = stored["default_resolution"]
    if stored.get("default_subtitle_languages") is not None:
        settings.default_subtitle_languages = [
            lang for lang in stored["default_subtitle_languages"].split(",") if lang
        ]


def _settings_response(session: Session, settings: AppSettings, service: YtDlpService) -> SettingsRead:
    _apply_stored_settings(session, settings, service)
    return SettingsRead(
        download_dir=str(settings.download_dir),
        default_concurrency=settings.default_concurrency,
        default_subtitle_languages=settings.default_subtitle_languages,
        default_resolution=settings.default_resolution,
        cookies_enabled=settings.cookies_path.exists(),
        ffmpeg=service.get_ffmpeg_status(),
    )


app = create_app()
