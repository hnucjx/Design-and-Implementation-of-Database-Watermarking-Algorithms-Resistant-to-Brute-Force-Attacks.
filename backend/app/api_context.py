"""API 层的依赖容器。

**为什么要有这一层**：路由处理函数需要 settings / yt-dlp 服务 / 任务管理器，以及三个「本机副作用」回调
（选目录、打开文件、打开视频）。重构前这些对象都活在 `create_app` 的闭包里，于是路由定义也只能写在
同一个函数里 —— 那是 `main.py` 455 行工厂的成因。现在改成
「**装配一次 → 放进 `app.state` → 路由按需取**」，组合根与路由定义就各自独立了。

约束：这里只放**装配结果的载体**，不放业务判断；`ApiContext` 是只读的（`frozen=True`），
路由可以改 `settings` 上的字段（那是既有行为），但不许把 context 当作可变注册表。
"""

from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from sqlmodel import Session

from .config import AppSettings
from .db import session_dependency
from .job_manager import JobManager
from .ytdlp_service import YtDlpService


@dataclass(frozen=True)
class ApiContext:
    settings: AppSettings
    service: YtDlpService
    manager: JobManager
    pick_directory: Callable[[Path], Path | None]
    open_local_path: Callable[[Path], None]
    open_video_path: Callable[[Path, str | None], None]


def get_context(request: Request) -> ApiContext:
    return request.app.state.api_context


def get_session(request: Request) -> Generator[Session, None, None]:
    """请求级 Session。

    engine 从 `app.state` 读，因此路由模块不必知道 engine 是怎么建的；
    **作用域规则仍然只有 `db.session_dependency` 一处定义**，不在这里重写一遍
    （同一条规则两处实现，就是明天的分叉）。
    """
    yield from session_dependency(request.app.state.db_engine)()


ContextDep = Annotated[ApiContext, Depends(get_context)]
SessionDep = Annotated[Session, Depends(get_session)]
