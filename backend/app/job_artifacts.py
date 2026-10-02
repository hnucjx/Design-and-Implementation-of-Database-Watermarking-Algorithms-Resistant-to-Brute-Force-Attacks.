"""任务产物的**定位**与**本机打开**。

一个条目下载完之后，界面上能对它做的本地操作只有两类：播放、打开它所在的文件夹。
「文件在哪」走的是一条候选链：数据库里记的 `output_path` → 按视频 id 在下载目录里发现的候选
→ 合并/后缀变体（`.f137.mp4` 这类）。

**这条链只定义一次**，在 [`output_paths.py`](output_paths.py)（`existing_output_file` /
`existing_item_folder`）。本模块只做两件事：把「找不到」翻译成合适的状态码，以及把打开动作
交给 [`system_open`](system_open.py)。

不负责：删文件（删除带白名单校验，属 [`job_manager.py`](job_manager.py)），
不负责决定用什么播放器（属 [`system_open.py`](system_open.py)）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from fastapi import HTTPException
from sqlmodel import Session, select

from .models import Job, JobItem
from .output_paths import existing_item_folder, existing_output_file
from .system_open import LocalOpenError


def output_file(item: JobItem, base_dir: str | Path | None = None) -> Path:
    """条目的成品视频文件。**找不到就 409**，不是 404：资源存在（这个条目在库里），只是产物还没就绪。"""
    path = existing_output_file(item.output_path, item.source_url, _base_path(base_dir))
    if path is None:
        raise HTTPException(status_code=409, detail="视频文件尚不可用。")
    if not path.is_file():
        raise HTTPException(status_code=409, detail="视频文件不存在。")
    return path


def item_folder(item: JobItem, base_dir: str | Path | None = None) -> Path:
    """条目产物所在的文件夹。产物还没出现时退回到任务下载目录 —— 打开文件夹比「不存在」有用。"""
    base_path = _base_path(base_dir)
    folder = existing_item_folder(item.output_path, item.source_url, base_path)
    if folder is not None and folder.is_dir():
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


def _base_path(base_dir: str | Path | None) -> Path | None:
    return Path(base_dir) if base_dir else None


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
