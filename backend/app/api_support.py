"""API 层的共享辅助：请求校验包装、读模型包装、设置读写、cookies 导入错误映射、元数据解析策略。

这些函数原先都是 `main.py` 里的模块级私有函数（`_require_job` 之类），但它们**不是路由**
—— 它们是「把业务对象翻译成 HTTP 响应」的胶水。分开之后，路由模块只剩路径、入参与状态码。

不负责：产物路径解析与本地打开（在 `job_artifacts.py`），业务规则（在各自领域模块）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlmodel import Session, select

from .config import AppSettings
from .job_read_model import read_job
from .models import Job, JobItem, Setting
from .paths import safe_path_name
from .proxy import redact_proxy_credentials
from .schemas import AnalyzeResponse, CookieStatus, JobRead, SettingsRead, VideoEntry
from .ytdlp_service import BrowserCookieImportError, YtDlpService


def read_job_or_404(session: Session, job_id: str) -> JobRead:
    payload = read_job(session, job_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return payload


def require_job(session: Session, job_id: str) -> Job:
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


def require_job_item(session: Session, job_id: str, item_id: str) -> JobItem:
    item = session.get(JobItem, item_id)
    if not item or item.job_id != job_id:
        raise HTTPException(status_code=404, detail="Job item not found.")
    return item


def single_job_item(session: Session, job: Job) -> JobItem:
    """单视频任务的唯一条目。合集任务没有「唯一产物」，只能让用户去点具体条目。"""
    items = session.exec(select(JobItem).where(JobItem.job_id == job.id).order_by(JobItem.index)).all()
    if len(items) != 1:
        raise HTTPException(status_code=409, detail="合集任务请打开具体视频。")
    return items[0]


def selected_entries(url: str, analysis: AnalyzeResponse, playlist_items: list[int] | None) -> list[VideoEntry]:
    """把解析结果收敛成「要建几条 JobItem」。

    单视频不走 `entries`（yt-dlp 对单视频不给 entries），这里合成一条；
    playlist 传了 `playlist_items` 就按 index 过滤，传空列表 = 全选。
    """
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


def job_download_dir(root_dir: Path, analysis: AnalyzeResponse, job_id: str) -> Path:
    """playlist 建同名子目录；单视频直接落根目录。目录名走 `safe_path_name`。"""
    if not analysis.is_playlist:
        return root_dir
    folder = safe_path_name(analysis.title, fallback=f"playlist-{job_id[:8]}")
    return root_dir / folder


def extract_metadata_with_cookies(
    service: YtDlpService,
    settings: AppSettings,
    url: str,
    cookies_enabled: bool,
) -> AnalyzeResponse:
    """解析元数据；若失败且错误命中「需要登录 / bot 校验」，自动导入浏览器 cookies 后**重试一次**。

    只重试一次，且只在 `cookies_enabled` 时重试 —— 关掉 cookies 的用户不该被应用偷偷去读浏览器。
    """
    cookies_path = settings.cookies_path if cookies_enabled and settings.cookies_path.exists() else None
    try:
        return service.extract_metadata(url, cookies_path=cookies_path)
    except Exception as exc:
        if not cookies_enabled or not is_cookie_required_error(exc):
            raise
        try:
            service.import_browser_cookies("auto", settings.cookies_path)
        except BrowserCookieImportError:
            raise
        except Exception as import_exc:
            raise RuntimeError(f"{exc} Browser cookies import failed: {import_exc}") from import_exc
        return service.extract_metadata(url, cookies_path=settings.cookies_path)


def cookie_status_from_import(result: Any) -> CookieStatus:
    """浏览器 cookies 导入的返回值有两种形状（dict / 带属性的对象），测试里的 fake 两种都有。"""
    if isinstance(result, dict):
        return CookieStatus(**result)
    return CookieStatus(
        enabled=True,
        filename=getattr(result, "filename", None),
        source="browser",
        browser=getattr(result, "browser", None),
        imported_count=getattr(result, "imported_count", None),
    )


def is_cookie_required_error(exc: Exception) -> bool:
    return YtDlpService.is_cookie_required_error(exc)


def cookie_import_status_code(exc: BrowserCookieImportError) -> int:
    """浏览器被占用是可重试的「状态冲突」（409），其余导入失败是「请求本身不行」（400）。"""
    return 409 if exc.code == "browser_locked" else 400


def set_setting(session: Session, key: str, value: str) -> None:
    setting = session.get(Setting, key) or Setting(key=key, value=value)
    setting.value = value
    session.add(setting)
    session.commit()


def apply_stored_settings(session: Session, settings: AppSettings, service: YtDlpService) -> None:
    """把 `Setting` 表里的持久化覆盖灌回 `AppSettings`（并把需要同步的字段推给 service）。

    为什么 `service` 也要跟着改：`default_subtitle_languages` / `aria2c_connections` / `proxy`
    是「请求没带就用设置」的兜底值，只改 settings 不推给服务就等于设置不生效。
    """
    stored = {setting.key: setting.value for setting in session.exec(select(Setting)).all()}
    if stored.get("download_dir"):
        settings.download_dir = Path(stored["download_dir"])
        service.download_dir = settings.download_dir
    if stored.get("default_concurrency"):
        settings.default_concurrency = int(stored["default_concurrency"])
    if stored.get("default_resolution"):
        settings.default_resolution = stored["default_resolution"]
    if "default_speed_limit_kbps" in stored:
        settings.default_speed_limit_kbps = int(stored["default_speed_limit_kbps"]) if stored["default_speed_limit_kbps"] else None
    if stored.get("default_retries"):
        settings.default_retries = int(stored["default_retries"])
    if stored.get("default_subtitle_languages") is not None:
        settings.default_subtitle_languages = [
            lang for lang in stored["default_subtitle_languages"].split(",") if lang
        ]
        # 同步给服务：它是「请求未指定语言」时的兜底集合，必须跟着设置走。
        service.default_subtitle_languages = settings.default_subtitle_languages
    if stored.get("aria2c_connections"):
        settings.aria2c_connections = int(stored["aria2c_connections"])
        service.aria2c_connections = settings.aria2c_connections
    if "proxy" in stored:
        # 空串 = 未配置（自动），不是「强制直连」——见 app/proxy.py 的 DIRECT_PROXY_VALUES。
        settings.proxy = stored["proxy"].strip() or None
        service.proxy = settings.proxy


def settings_response(session: Session, settings: AppSettings, service: YtDlpService) -> SettingsRead:
    apply_stored_settings(session, settings, service)
    resolution = service.proxy_resolution()
    return SettingsRead(
        download_dir=str(settings.download_dir),
        default_concurrency=settings.default_concurrency,
        default_subtitle_languages=settings.default_subtitle_languages,
        default_resolution=settings.default_resolution,
        default_speed_limit_kbps=settings.default_speed_limit_kbps,
        default_retries=settings.default_retries,
        aria2c_connections=settings.aria2c_connections,
        proxy=settings.proxy,
        proxy_source=resolution.source,
        proxy_effective=redact_proxy_credentials(resolution.url),
        cookies_enabled=settings.cookies_path.exists(),
        ffmpeg=service.get_ffmpeg_status(),
    )


def select_directory_with_tkinter(initial_dir: Path) -> Path | None:
    """默认的目录选择器。tkinter 在无 GUI 的 Python 里不可用，因此失败必须变成可读的 400。"""
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
