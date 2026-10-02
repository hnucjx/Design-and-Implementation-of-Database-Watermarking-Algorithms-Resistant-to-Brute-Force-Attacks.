"""FastAPI 组合根：装配依赖、挂载路由、托管前端静态资源。

**这一层只做装配。** 判断一段代码该不该放这里，只有一个问题：
*它能不能写成「输入 → 输出」的普通函数？* 能，就不该在这里 —— 路由去 `routers/`（每个资源一个模块），
共享辅助去 [`api_support.py`](api_support.py) / [`job_artifacts.py`](job_artifacts.py)，
依赖载体去 [`api_context.py`](api_context.py)。

重构前这里是一个 455 行的 `create_app`，同时承担路由声明、请求校验、路径解析与设置读写；
现在它只在启动时把对象装起来，再 `include_router` 一次。**新增接口只需要动 `routers/`，不碰本文件。**
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session

from .api_context import ApiContext
from .api_support import apply_stored_settings, select_directory_with_tkinter
from .config import REPO_ROOT, AppSettings, get_settings
from .db import checkpoint_wal, create_app_engine, init_db
from .events import EventBroker
from .job_manager import JobManager
from .logging_setup import configure_logging
from .routers import API_ROUTERS
from .runtime_env import sanitize_environment
from .system_open import open_path_with_default_app, open_video_with_best_player
from .ytdlp_service import YtDlpService

logger = logging.getLogger(__name__)


def create_app(
    settings: AppSettings | None = None,
    ytdlp_service: YtDlpService | None = None,
    directory_picker: Callable[[Path], Path | None] | None = None,
    system_opener: Callable[[Path], None] | None = None,
    video_opener: Callable[[Path, str | None], None] | None = None,
    sanitize_environment_variables: bool = True,
) -> FastAPI:
    app_settings = settings or get_settings()
    app_settings.ensure_directories()
    # 日志必须先初始化：后面每一步都可能出错，而「没有任何日志」会让排障从第一步就断掉。
    configure_logging(app_settings.data_dir / "logs")
    removed = sanitize_environment() if sanitize_environment_variables else []
    if removed:
        for item in removed:
            logger.warning("已摘除会破坏 JS 运行时的宿主环境变量：%s=%s（原因：%s）", item.name, item.value, item.reason)
        # 说明我们改的是本进程的环境，用户在别的 shell 里改名/删掉都不会影响应用行为。
        logger.warning("摘除生效范围：当前应用进程；如仍看到相关报错，请检查是否由启动脚本反复注入")
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
        stall_timeout_seconds=app_settings.stall_timeout_seconds,
        aria2c_enabled=app_settings.aria2c_enabled,
        aria2c_path=app_settings.aria2c_path,
        aria2c_connections=app_settings.aria2c_connections,
        js_runtime_path=app_settings.js_runtime_path,
        default_subtitle_languages=app_settings.default_subtitle_languages,
        proxy=app_settings.proxy,
    )
    with Session(engine) as session:
        apply_stored_settings(session, app_settings, service)
    manager = JobManager(engine, app_settings, service, broker)
    pick_directory = directory_picker or select_directory_with_tkinter
    open_local_path = system_opener or open_path_with_default_app
    open_video_path = video_opener or (
        (lambda path, _actual_format=None: system_opener(path)) if system_opener else open_video_with_best_player
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await asyncio.to_thread(checkpoint_wal, engine)
        await manager.start()
        try:
            yield
        finally:
            await manager.stop()
            await asyncio.to_thread(checkpoint_wal, engine)

    app = FastAPI(title="YouTube Downloader", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 装配结果挂在 app.state 上，路由通过 `api_context` 取 —— 这样路由定义不必再挤进本函数。
    app.state.db_engine = engine
    app.state.event_broker = broker
    app.state.api_context = ApiContext(
        settings=app_settings,
        service=service,
        manager=manager,
        pick_directory=pick_directory,
        open_local_path=open_local_path,
        open_video_path=open_video_path,
    )
    for route_module in API_ROUTERS:
        app.include_router(route_module)

    frontend_dist = REPO_ROOT / "frontend" / "dist"
    frontend_assets = frontend_dist / "assets"
    frontend_index_path = frontend_dist / "index.html"
    if frontend_index_path.is_file() and frontend_assets.is_dir():
        app.mount("/assets", StaticFiles(directory=frontend_assets), name="assets")

        @app.get("/")
        def frontend_index() -> FileResponse:
            return FileResponse(frontend_index_path)

    return app


app = create_app()
