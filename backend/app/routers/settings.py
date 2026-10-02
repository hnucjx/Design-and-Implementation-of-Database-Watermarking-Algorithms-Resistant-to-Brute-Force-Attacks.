"""应用设置：读取、更新、选下载目录。

更新语义里有两处必须保持的细节（改之前先读）：
1. **「显式传 null」与「没传」不同**：`proxy` 与 `default_speed_limit_kbps` 用 `model_fields_set`
   判断，传 null = 改回自动/不限速，没传 = 不动。
2. **并发即时生效**：改并发要立刻调 `manager.set_concurrency`；改限速/重试要让
   `manager.set_runtime_download_defaults` 把 queued/running/paused 任务的选项一起刷新。
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import api_support
from ..api_context import ContextDep, SessionDep
from ..schemas import SettingsRead, SettingsUpdate

router = APIRouter()


@router.get("/api/settings", response_model=SettingsRead)
def get_app_settings(session: SessionDep, ctx: ContextDep) -> SettingsRead:
    return api_support.settings_response(session, ctx.settings, ctx.service)


@router.put("/api/settings", response_model=SettingsRead)
async def update_app_settings(update: SettingsUpdate, session: SessionDep, ctx: ContextDep) -> SettingsRead:
    settings = ctx.settings
    service = ctx.service
    if update.download_dir is not None:
        path = Path(update.download_dir).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        settings.download_dir = path
        service.download_dir = path
        api_support.set_setting(session, "download_dir", str(path))
    if update.default_concurrency is not None:
        settings.default_concurrency = update.default_concurrency
        api_support.set_setting(session, "default_concurrency", str(update.default_concurrency))
        await ctx.manager.set_concurrency(update.default_concurrency)
    if update.default_subtitle_languages is not None:
        settings.default_subtitle_languages = update.default_subtitle_languages
        # 同时也是「请求没带语言」时的兜底，必须同步给服务。
        service.default_subtitle_languages = update.default_subtitle_languages
        api_support.set_setting(session, "default_subtitle_languages", ",".join(update.default_subtitle_languages))
    if update.default_resolution is not None:
        settings.default_resolution = update.default_resolution
        api_support.set_setting(session, "default_resolution", update.default_resolution)
    runtime_options_changed = False
    if "default_speed_limit_kbps" in update.model_fields_set:
        settings.default_speed_limit_kbps = update.default_speed_limit_kbps
        api_support.set_setting(
            session,
            "default_speed_limit_kbps",
            "" if update.default_speed_limit_kbps is None else str(update.default_speed_limit_kbps),
        )
        runtime_options_changed = True
    if update.default_retries is not None:
        settings.default_retries = update.default_retries
        api_support.set_setting(session, "default_retries", str(update.default_retries))
        runtime_options_changed = True
    if update.aria2c_connections is not None:
        settings.aria2c_connections = update.aria2c_connections
        service.aria2c_connections = update.aria2c_connections
        api_support.set_setting(session, "aria2c_connections", str(update.aria2c_connections))
    if "proxy" in update.model_fields_set:
        # 用 model_fields_set 而不是 `is not None`：显式传 null 表示「改回自动」。
        settings.proxy = update.proxy
        service.proxy = update.proxy
        api_support.set_setting(session, "proxy", (update.proxy or "").strip())
    if runtime_options_changed:
        await ctx.manager.set_runtime_download_defaults(
            settings.default_speed_limit_kbps,
            settings.default_retries,
        )
    return api_support.settings_response(session, settings, service)


@router.post("/api/settings/download-dir/select", response_model=SettingsRead)
async def select_download_dir(session: SessionDep, ctx: ContextDep) -> SettingsRead:
    """弹系统目录选择框。用户取消 = 什么都不改，仍然返回当前设置（不是 4xx）。"""
    settings = ctx.settings
    # 原实现里这一句是「先把持久化覆盖灌回来」——用户可能在别处改过设置，弹框前先对齐。
    api_support.settings_response(session, settings, ctx.service)
    try:
        selected = await asyncio.to_thread(ctx.pick_directory, settings.download_dir)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if selected is None:
        return api_support.settings_response(session, settings, ctx.service)

    path = Path(selected).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    settings.download_dir = path
    ctx.service.download_dir = path
    api_support.set_setting(session, "download_dir", str(path))
    return api_support.settings_response(session, settings, ctx.service)
