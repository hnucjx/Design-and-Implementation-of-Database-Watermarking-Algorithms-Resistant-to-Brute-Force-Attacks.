"""任务产物的**定位**与**本机打开**。

一个条目下载完之后，界面上能对它做的本地操作只有两类：播放、打开它所在的文件夹。
「文件在哪」这件事在两类操作里走的是同一条候选链：数据库里记的 `output_path` →
按视频 id 在下载目录里发现的候选 → 合并/后缀变体（`.f137.mp4` 这类）。
本模块把这条链与 `system_open` 的调用包在一起，路由层只写状态码。

**本模块是「产物定位只有一条链」的落点**：`job_manager` 里另有一套为删除而写的路径计算
（`_item_output_paths`），两者的收敛已登记为下一轮候选，见 [ai/refactor/refactor.md §4.1](../../ai/refactor/refactor.md)。
在收敛之前，请**不要**在路由层再写第三处候选计算。

不负责：删文件（删除带白名单校验，属 `job_manager`），不负责决定用什么播放器（属 `system_open`）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from fastapi import HTTPException
from sqlmodel import Session, select

from .models import Job, JobItem
from .output_paths import discover_existing_output_path, discover_output_file_candidates, resolve_existing_output_path
from .system_open import LocalOpenError


def output_file(item: JobItem, base_dir: str | Path | None = None) -> Path:
    """条目的成品视频文件。**找不到就 409**，不是 404：资源存在（这个条目在库里），只是产物还没就绪。"""
    base_path = Path(base_dir) if base_dir else None
    path = resolve_existing_output_path(Path(item.output_path), base_path) if item.output_path else None
    if path is None:
        path = discover_existing_output_path(item.source_url, base_path)
    if path is None:
        raise HTTPException(status_code=409, detail="视频文件尚不可用。")
    if not path.is_file():
        raise HTTPException(status_code=409, detail="视频文件不存在。")
    return path


def item_folder(item: JobItem, base_dir: str | Path | None = None) -> Path:
    """条目产物所在的文件夹。产物还没出现时退回到任务下载目录 —— 打开文件夹比「不存在」有用。"""
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


def output_folder(item: JobItem, base_dir: str | Path | None = None) -> Path:
    return item_folder(item, base_dir)


def job_folder(session: Session, job: Job) -> Path:
    """任务文件夹：单视频 = 该视频所在目录；合集 = 任务自己的下载目录。"""
    items = session.exec(select(JobItem).where(JobItem.job_id == job.id).order_by(JobItem.index)).all()
    if len(items) == 1:
        return output_folder(items[0], job.download_dir)
    if not job.download_dir:
        raise HTTPException(status_code=409, detail="合集文件夹尚不可用。")
    folder = Path(job.download_dir).expanduser()
    if not folder.is_dir():
        raise HTTPException(status_code=409, detail="合集文件夹不存在。")
    return folder


async def open_local_path(path_opener: Callable[[Path], None], path: Path) -> None:
    """在线程里打开文件夹。`HTTPException` 直接放行，其它异常统一降级成 400 + 原文。"""
    try:
        await asyncio.to_thread(path_opener, path)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"无法打开本地路径：{exc}") from exc


async def open_video_path(
    video_opener: Callable[[Path, str | None], None],
    path: Path,
    actual_format: str | None,
) -> None:
    """在线程里播放视频。播放器不可用是 409（环境问题，重试或换播放器可解），其余是 400。"""
    try:
        await asyncio.to_thread(video_opener, path, actual_format)
    except HTTPException:
        raise
    except LocalOpenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"无法打开本地视频：{exc}") from exc
