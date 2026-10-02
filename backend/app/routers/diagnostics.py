"""运行环境自检：健康检查、依赖与 JS 运行时状态、代理连通性探测。

共同点：它们回答的都是「**这个环境能不能干活**」，而不是「某条业务数据怎么样」。
自检只读不写：不改设置、不改任务、不碰 cookies 文件。
"""

import asyncio
import logging

from fastapi import APIRouter, Body

from ..api_context import ContextDep
from ..connectivity import test_proxy
from ..logging_setup import log_path
from ..proxy import resolve_proxy
from ..runtime_env import describe_environment_risks
from ..schemas import DiagnosticsRead, ProxyTestRead, ProxyTestRequest

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@router.get("/api/diagnostics", response_model=DiagnosticsRead)
def diagnostics(ctx: ContextDep) -> DiagnosticsRead:
    """依赖状态 + cookies 是否就绪 + 日志路径 + 被摘除的宿主环境变量。"""
    service = ctx.service
    dependencies = (
        service.get_dependency_status()
        if hasattr(service, "get_dependency_status")
        else {**service.get_ffmpeg_status(), "yt_dlp_version": None, "js_runtime": False}
    )
    dependencies = {
        **dependencies,
        "youtube_max_parallel_downloads": ctx.settings.youtube_max_parallel_downloads,
        "anti403_http_chunk_size_mb": ctx.settings.anti403_http_chunk_size_mb,
        "throttled_rate_kbps": ctx.settings.throttled_rate_kbps,
    }
    return DiagnosticsRead(
        cookies_enabled=ctx.settings.cookies_path.exists(),
        dependencies=dependencies,
        log_file=str(log_path()) if log_path() else None,
        sanitized_environment=describe_environment_risks(),
    )


@router.post("/api/diagnostics/runtime", response_model=DiagnosticsRead)
async def refresh_runtime_diagnostics(ctx: ContextDep) -> DiagnosticsRead:
    """重新做一次 JS 运行时/依赖探测。

    探测结果在服务里是记忆化的（每次构建 ydl_opts 都起子进程太贵），所以用户装完
    Node/Deno、或修好环境变量之后，需要一个明确的「重新自检」入口来打破缓存。
    """
    refresh = getattr(ctx.service, "refresh_js_runtime", None)
    if callable(refresh):
        await asyncio.to_thread(refresh)
    return diagnostics(ctx)


@router.post("/api/proxy/test", response_model=ProxyTestRead)
async def test_proxy_connection(ctx: ContextDep, request: ProxyTestRequest | None = Body(default=None)) -> ProxyTestRead:
    """用**当前生效**（或请求里临时指定）的代理真实访问一次 YouTube。

    不接受 ``proxy=""`` 之外的模糊语义：空 = 用当前设置，``direct`` = 强制直连，
    其余按代理地址处理 —— 与 ``PUT /api/settings`` 的语义完全一致，避免出现
    「测试用 A、下载用 B」这种最气人的偏差。
    """
    override = request.proxy if request else None
    # getattr 兜底：测试里的 fake service 没有 proxy 属性，而「读不到配置」的语义
    # 就是「没设置代理」，不该因此 500。
    configured = getattr(ctx.service, "proxy", None)
    resolution = resolve_proxy(override if override is not None else configured)
    result = await asyncio.to_thread(test_proxy, resolution)
    logger.info(
        "proxy test: ok=%s source=%s proxy=%s status=%s elapsed_ms=%s error=%s",
        result.ok,
        result.source,
        result.proxy or "<direct>",
        result.http_status,
        result.elapsed_ms,
        result.error or "-",
    )
    return ProxyTestRead(**result.to_detail())
