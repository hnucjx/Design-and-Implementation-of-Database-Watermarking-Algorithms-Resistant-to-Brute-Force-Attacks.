from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select

from .config import AppSettings, REPO_ROOT, get_settings
from .db import create_app_engine, init_db, session_dependency
from .events import EventBroker
from .job_manager import JobManager, new_id
from .models import Job, JobItem, Setting, utc_now
from .schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    CookieStatus,
    CreateJobRequest,
    JobItemRead,
    JobRead,
    SettingsRead,
    SettingsUpdate,
    VideoEntry,
)
from .ytdlp_service import YtDlpService


def create_app(settings: AppSettings | None = None, ytdlp_service: YtDlpService | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    app_settings.ensure_directories()
    engine = create_app_engine(app_settings)
    init_db(engine)
    broker = EventBroker()
    service = ytdlp_service or YtDlpService(app_settings.download_dir)
    manager = JobManager(engine, app_settings, service, broker)
    get_session = session_dependency(engine)

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

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/analyze", response_model=AnalyzeResponse)
    def analyze(request: AnalyzeRequest) -> AnalyzeResponse:
        try:
            cookies_path = app_settings.cookies_path if request.cookies_enabled and app_settings.cookies_path.exists() else None
            return service.extract_metadata(request.url, cookies_path=cookies_path)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/jobs", response_model=JobRead, status_code=201)
    async def create_job(request: CreateJobRequest, session: SessionDep) -> JobRead:
        try:
            analysis = service.extract_metadata(
                request.url,
                cookies_path=app_settings.cookies_path if app_settings.cookies_path.exists() else None,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        entries = _selected_entries(request.url, analysis, request.options.playlist_items)
        if not entries:
            raise HTTPException(status_code=400, detail="No downloadable playlist entries were selected.")

        job = Job(
            id=new_id(),
            url=request.url,
            title=analysis.title,
            options_json=request.options.model_dump_json(),
            total_items=len(entries),
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

    @app.get("/api/jobs/{job_id}", response_model=JobRead)
    def get_job(job_id: str, session: SessionDep) -> JobRead:
        return _read_job(session, job_id)

    @app.post("/api/jobs/{job_id}/cancel", response_model=JobRead)
    async def cancel_job(job_id: str, session: SessionDep) -> JobRead:
        if not session.get(Job, job_id):
            raise HTTPException(status_code=404, detail="Job not found.")
        await manager.cancel(job_id)
        return _read_job(session, job_id)

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
    def update_app_settings(update: SettingsUpdate, session: SessionDep) -> SettingsRead:
        if update.download_dir is not None:
            path = Path(update.download_dir).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            app_settings.download_dir = path
            service.download_dir = path
            _set_setting(session, "download_dir", str(path))
        if update.default_concurrency is not None:
            app_settings.default_concurrency = update.default_concurrency
            _set_setting(session, "default_concurrency", str(update.default_concurrency))
        if update.default_subtitle_languages is not None:
            app_settings.default_subtitle_languages = update.default_subtitle_languages
            _set_setting(session, "default_subtitle_languages", ",".join(update.default_subtitle_languages))
        if update.default_resolution is not None:
            app_settings.default_resolution = update.default_resolution
            _set_setting(session, "default_resolution", update.default_resolution)
        return _settings_response(session, app_settings, service)

    @app.post("/api/cookies", response_model=CookieStatus)
    async def upload_cookies(file: UploadFile = File(...)) -> CookieStatus:
        content = await file.read()
        app_settings.data_dir.mkdir(parents=True, exist_ok=True)
        app_settings.cookies_path.write_bytes(content)
        return CookieStatus(enabled=True, filename=file.filename or app_settings.cookies_filename)

    @app.delete("/api/cookies", response_model=CookieStatus)
    def delete_cookies() -> CookieStatus:
        if app_settings.cookies_path.exists():
            app_settings.cookies_path.unlink()
        return CookieStatus(enabled=False, filename=None)

    frontend_dist = REPO_ROOT / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

        @app.get("/")
        def frontend_index() -> FileResponse:
            return FileResponse(frontend_dist / "index.html")

    return app


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
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    items = session.exec(select(JobItem).where(JobItem.job_id == job_id).order_by(JobItem.index)).all()
    return JobRead(
        id=job.id,
        url=job.url,
        title=job.title,
        status=job.status,
        progress=job.progress,
        total_items=job.total_items,
        completed_items=job.completed_items,
        failed_items=job.failed_items,
        current_item_title=job.current_item_title,
        error=job.error,
        items=[
            JobItemRead(
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
                output_path=item.output_path,
                error=item.error,
            )
            for item in items
        ],
    )


def _set_setting(session: Session, key: str, value: str) -> None:
    setting = session.get(Setting, key) or Setting(key=key, value=value)
    setting.value = value
    session.add(setting)
    session.commit()


def _settings_response(session: Session, settings: AppSettings, service: YtDlpService) -> SettingsRead:
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
    return SettingsRead(
        download_dir=str(settings.download_dir),
        default_concurrency=settings.default_concurrency,
        default_subtitle_languages=settings.default_subtitle_languages,
        default_resolution=settings.default_resolution,
        cookies_enabled=settings.cookies_path.exists(),
        ffmpeg=service.get_ffmpeg_status(),
    )


app = create_app()
