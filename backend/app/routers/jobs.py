"""任务资源：创建、列表、批量操作、单任务生命周期。

任务（`Job`/`JobItem`）是应用的主资源，这一组路由只做三件事：校验存在性、转调 `JobManager`、
把结果投影成读模型。**任务状态机不在这一层** —— 暂停/重启/删除的真实语义见 `job_manager.py`。
"""

from fastapi import APIRouter, Body, HTTPException, Response
from sqlmodel import select

from .. import api_support
from ..api_context import ContextDep, SessionDep
from ..job_manager import new_id
from ..models import Job, JobItem
from ..schemas import (
    CreateJobRequest,
    DeleteJobItemsRequest,
    DeleteJobItemsResponse,
    JobBatchActionRequest,
    JobBatchActionResponse,
    JobRead,
    RestartJobRequest,
)
from ..ytdlp_service import BrowserCookieImportError

router = APIRouter()


@router.post("/api/jobs", response_model=JobRead, status_code=201)
async def create_job(request: CreateJobRequest, session: SessionDep, ctx: ContextDep) -> JobRead:
    """建任务：再解析一次（不信任前端传回的元数据）→ 选条目 → 写 Job + N 个 JobItem → 入队。

    解析失败与 `POST /api/analyze` 同语义；一条条目都没选出来是 400 而不是建一个空任务。
    """
    try:
        analysis = api_support.extract_metadata_with_cookies(ctx.service, ctx.settings, request.url, True)
    except BrowserCookieImportError as exc:
        raise HTTPException(status_code=api_support.cookie_import_status_code(exc), detail=exc.to_detail()) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    entries = api_support.selected_entries(request.url, analysis, request.options.playlist_items)
    if not entries:
        raise HTTPException(status_code=400, detail="No downloadable playlist entries were selected.")

    job_id = new_id()
    download_dir = api_support.job_download_dir(ctx.settings.download_dir, analysis, job_id)
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
    await ctx.manager.enqueue(job.id)
    return api_support.read_job_or_404(session, job.id)


@router.get("/api/jobs", response_model=list[JobRead])
def list_jobs(session: SessionDep) -> list[JobRead]:
    jobs = session.exec(select(Job).order_by(Job.created_at.desc())).all()
    return [api_support.read_job_or_404(session, job.id) for job in jobs]


@router.post("/api/jobs/batch", response_model=JobBatchActionResponse)
async def batch_job_action(request: JobBatchActionRequest, session: SessionDep, ctx: ContextDep) -> JobBatchActionResponse:
    """批量暂停/重启/删除。找不到任何目标任务时 404，而不是「成功 0 条」。"""
    affected: list[str] = []
    for job_id in request.job_ids:
        if not session.get(Job, job_id):
            continue
        if request.action == "pause":
            await ctx.manager.pause(job_id)
        elif request.action == "restart":
            await ctx.manager.restart(job_id)
        else:
            await ctx.manager.delete(job_id, delete_files=request.delete_files)
        affected.append(job_id)

    if not affected:
        raise HTTPException(status_code=404, detail="No matching jobs found.")

    if request.action == "delete":
        return JobBatchActionResponse(affected_job_ids=affected, jobs=[])

    session.expire_all()
    return JobBatchActionResponse(
        affected_job_ids=affected,
        jobs=[api_support.read_job_or_404(session, job_id) for job_id in affected],
    )


@router.get("/api/jobs/{job_id}", response_model=JobRead)
def get_job(job_id: str, session: SessionDep) -> JobRead:
    return api_support.read_job_or_404(session, job_id)


@router.post("/api/jobs/{job_id}/cancel", response_model=JobRead)
async def cancel_job(job_id: str, session: SessionDep, ctx: ContextDep) -> JobRead:
    if not session.get(Job, job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    await ctx.manager.cancel(job_id)
    return api_support.read_job_or_404(session, job_id)


@router.post("/api/jobs/{job_id}/pause", response_model=JobRead)
async def pause_job(job_id: str, session: SessionDep, ctx: ContextDep) -> JobRead:
    if not session.get(Job, job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    await ctx.manager.pause(job_id)
    session.expire_all()
    return api_support.read_job_or_404(session, job_id)


@router.post("/api/jobs/{job_id}/restart", response_model=JobRead)
async def restart_job(
    job_id: str,
    session: SessionDep,
    ctx: ContextDep,
    request: RestartJobRequest | None = Body(default=None),
) -> JobRead:
    """重启任务；请求体里带 `resolution` 表示换清晰度重下（按需覆盖原选项）。"""
    if not session.get(Job, job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    await ctx.manager.restart(job_id, resolution=request.resolution if request else None)
    session.expire_all()
    return api_support.read_job_or_404(session, job_id)


@router.post("/api/jobs/{job_id}/items/{item_id}/restart", response_model=JobRead)
async def restart_job_item(
    job_id: str,
    item_id: str,
    session: SessionDep,
    ctx: ContextDep,
    request: RestartJobRequest | None = Body(default=None),
) -> JobRead:
    if not await ctx.manager.restart_item(job_id, item_id, resolution=request.resolution if request else None):
        raise HTTPException(status_code=404, detail="Job item not found.")
    session.expire_all()
    return api_support.read_job_or_404(session, job_id)


@router.post("/api/jobs/{job_id}/items/delete", response_model=DeleteJobItemsResponse)
async def delete_job_items(
    job_id: str,
    request: DeleteJobItemsRequest,
    session: SessionDep,
    ctx: ContextDep,
) -> DeleteJobItemsResponse:
    """删子视频。删掉最后一条时任务本身也会消失，因此响应里要带回 `job_deleted`。"""
    if not session.get(Job, job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    deleted_item_ids, job_deleted = await ctx.manager.delete_items(
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
        job=api_support.read_job_or_404(session, job_id),
    )


@router.delete("/api/jobs/{job_id}", status_code=204)
async def delete_job(job_id: str, session: SessionDep, ctx: ContextDep, delete_files: bool = False) -> Response:
    """删任务。`delete_files=true` 才会动磁盘上的产物，默认只删记录。"""
    if not session.get(Job, job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    await ctx.manager.delete(job_id, delete_files=delete_files)
    return Response(status_code=204)
