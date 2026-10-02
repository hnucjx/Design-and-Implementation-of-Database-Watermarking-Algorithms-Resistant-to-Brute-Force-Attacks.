"""解析链接：`POST /api/analyze`。

只有一条路由也要独立成文件：它的变化原因（请求体字段、解析错误到状态码的映射）与别处无关。
"""

from fastapi import APIRouter, HTTPException

from .. import api_support
from ..api_context import ContextDep
from ..schemas import AnalyzeRequest, AnalyzeResponse
from ..ytdlp_service import BrowserCookieImportError

router = APIRouter()


@router.post("/api/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest, ctx: ContextDep) -> AnalyzeResponse:
    """解析元数据。失败一律 400，但「浏览器被锁」要给 409（见 `cookie_import_status_code`）。

    需要登录 / bot 校验时会在服务端自动导入一次浏览器 cookies 再重试，用户看不到这一步；
    重试失败会把原始错误与导入失败原因拼在一起抛出来（见 `extract_metadata_with_cookies`）。
    """
    try:
        return api_support.extract_metadata_with_cookies(ctx.service, ctx.settings, request.url, request.cookies_enabled)
    except BrowserCookieImportError as exc:
        raise HTTPException(status_code=api_support.cookie_import_status_code(exc), detail=exc.to_detail()) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
