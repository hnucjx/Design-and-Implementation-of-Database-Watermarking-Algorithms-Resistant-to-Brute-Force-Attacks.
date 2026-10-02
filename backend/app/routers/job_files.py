"""任务产物的本机操作：播放、打开所在文件夹（任务级与条目级各两个）。

这四个路由是**唯一的「应用去碰本机文件」入口**：路径全部由后端从数据库记录推导，
不接受前端传路径（安全审计里有这一条）。产物定位在 `job_artifacts.py`，本文件只定状态码。
"""

from fastapi import APIRouter, Response

from .. import api_support
from ..api_context import ContextDep, SessionDep
from ..job_artifacts import job_folder, open_local_path, open_video_path, output_file, output_folder

router = APIRouter()


@router.post("/api/jobs/{job_id}/play", status_code=204)
async def play_job_video(job_id: str, session: SessionDep, ctx: ContextDep) -> Response:
    """播放单视频任务的成品。合集任务没有「唯一产物」→ 409（`single_job_item`）。"""
    job = api_support.require_job(session, job_id)
    item = api_support.single_job_item(session, job)
    await open_video_path(ctx.open_video_path, output_file(item, job.download_dir), item.actual_format)
    return Response(status_code=204)


@router.post("/api/jobs/{job_id}/open-folder", status_code=204)
async def open_job_video_folder(job_id: str, session: SessionDep, ctx: ContextDep) -> Response:
    job = api_support.require_job(session, job_id)
    await open_local_path(ctx.open_local_path, job_folder(session, job))
    return Response(status_code=204)


@router.post("/api/jobs/{job_id}/items/{item_id}/play", status_code=204)
async def play_job_item_video(job_id: str, item_id: str, session: SessionDep, ctx: ContextDep) -> Response:
    job = api_support.require_job(session, job_id)
    item = api_support.require_job_item(session, job_id, item_id)
    await open_video_path(ctx.open_video_path, output_file(item, job.download_dir), item.actual_format)
    return Response(status_code=204)


@router.post("/api/jobs/{job_id}/items/{item_id}/open-folder", status_code=204)
async def open_job_item_video_folder(job_id: str, item_id: str, session: SessionDep, ctx: ContextDep) -> Response:
    job = api_support.require_job(session, job_id)
    item = api_support.require_job_item(session, job_id, item_id)
    await open_local_path(ctx.open_local_path, output_folder(item, job.download_dir))
    return Response(status_code=204)
