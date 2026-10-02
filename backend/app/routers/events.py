"""实时事件流：`GET /api/events`（SSE）。

独立成模块的理由不是行数，而是**传输方式**：它是一条长连接、只写不读、
不参与请求/响应周期。混进任何一个业务 router 都会让那个模块多出一种生命周期。
"""

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..events import EventBroker

router = APIRouter()


@router.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    """订阅任务事件。前端收到任何一条就重新拉一次 `/api/jobs`，因此这里不必做增量语义。"""
    broker: EventBroker = request.app.state.event_broker

    async def event_stream():
        async for message in broker.subscribe():
            yield f"data: {message}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
