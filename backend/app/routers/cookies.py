"""cookies 资源：上传、从浏览器导入、清除、体检。

`/api/cookies/verify` 放在这里而不是自检模块：它验的是 cookies 这一件事，
而自检模块验的是「运行环境」（依赖、JS 运行时、代理）。两者失败时的处置方式完全不同。
"""

import asyncio
import logging

from fastapi import APIRouter, Body, File, HTTPException, UploadFile

from .. import api_support
from ..api_context import ContextDep
from ..cookie_health import verify_cookies
from ..proxy import resolve_proxy
from ..schemas import BrowserCookieImportRequest, CookieHealthRead, CookieStatus, CookieVerifyRequest
from ..ytdlp_service import BrowserCookieImportError

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/cookies/verify", response_model=CookieHealthRead)
async def verify_cookies_endpoint(
    ctx: ContextDep,
    request: CookieVerifyRequest | None = Body(default=None),
) -> CookieHealthRead:
    # 首句按原样保留（含既有的 RST 双反引号）：它已经发布在 `/openapi.json` 里，
    # 本轮重构不改写任何既有的对外文字，新解释只允许追加。
    """校验 ``data/cookies.txt`` 是否真的能登录（而不是「文件存在」）。

    `deep=false` 只做离线体检（格式/域名/鉴权项/过期），不联网 —— 没有代理的环境里也能用。
    """
    deep = request.deep if request else True
    # getattr 兜底：测试里的 fake service 没有 proxy_resolution，而「读不到」的语义就是「按设置解析」。
    resolution_for_cookies = (
        ctx.service.proxy_resolution()
        if hasattr(ctx.service, "proxy_resolution")
        else resolve_proxy(getattr(ctx.service, "proxy", None))
    )
    health = await asyncio.to_thread(
        verify_cookies,
        ctx.settings.cookies_path,
        resolution_for_cookies,
        deep=deep,
    )
    logger.info(
        "cookies verify: present=%s count=%s youtube_domain_count=%s auth=%s logged_in=%s verdict=%s",
        health.present,
        health.cookie_count,
        health.youtube_domain_count,
        ",".join(health.auth_cookie_names) or "-",
        health.logged_in,
        health.verdict,
    )
    return CookieHealthRead(**health.to_detail())


@router.post("/api/cookies", response_model=CookieStatus)
async def upload_cookies(ctx: ContextDep, file: UploadFile = File(...)) -> CookieStatus:
    """上传一份 Netscape 格式的 cookies.txt。

    **这里不做格式校验**：体检是独立入口（`/api/cookies/verify`）。上传时拒收会让用户卡在
    「传不上去、也验不了」——先落盘，再由体检给出结论。
    """
    content = await file.read()
    ctx.settings.data_dir.mkdir(parents=True, exist_ok=True)
    ctx.settings.cookies_path.write_bytes(content)
    return CookieStatus(enabled=True, filename=file.filename or ctx.settings.cookies_filename, source="file")


@router.post("/api/cookies/from-browser", response_model=CookieStatus)
async def import_cookies_from_browser(request: BrowserCookieImportRequest, ctx: ContextDep) -> CookieStatus:
    """从本机浏览器导入。浏览器被占用时返回 409，前端据此显示「关闭 Edge 并导入」。"""
    try:
        result = await asyncio.to_thread(
            ctx.service.import_browser_cookies,
            request.browser,
            ctx.settings.cookies_path,
            request.close_browser_if_locked,
        )
    except BrowserCookieImportError as exc:
        raise HTTPException(status_code=api_support.cookie_import_status_code(exc), detail=exc.to_detail()) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return api_support.cookie_status_from_import(result)


@router.delete("/api/cookies", response_model=CookieStatus)
def delete_cookies(ctx: ContextDep) -> CookieStatus:
    if ctx.settings.cookies_path.exists():
        ctx.settings.cookies_path.unlink()
    return CookieStatus(enabled=False, filename=None, source="none")
