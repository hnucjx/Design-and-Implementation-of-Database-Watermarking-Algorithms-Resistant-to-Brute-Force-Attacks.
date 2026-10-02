"""HTTP 路由，按资源分模块。

一个资源一个文件，改动原因互相独立：加一个诊断字段不必碰任务路由，改解析错误映射不必碰设置。
`API_ROUTERS` 只声明**挂载顺序**，不声明依赖关系 —— 依赖统一由 `api_context.ApiContext` 提供。

分层位置：L0 入口层（与 `main.py` 同层）。路由只做「取参 → 调辅助 → 定状态码」，
业务规则一律不在这一层；产物定位见 `job_artifacts.py`，设置读写与错误映射见 `api_support.py`。
"""

from .analyze import router as analyze_router
from .cookies import router as cookies_router
from .diagnostics import router as diagnostics_router
from .events import router as events_router
from .job_files import router as job_files_router
from .jobs import router as jobs_router
from .settings import router as settings_router

# 顺序沿用重构前 `main.py` 的声明次序，但把同一资源聚在一起：`/api/cookies/verify` 随 cookies 归位
# （原先它夹在自检路由中间），SSE 排在设置之后。路径之间没有前缀冲突，因此顺序不影响匹配结果，
# 它只决定 OpenAPI 里的 operation 排列。
API_ROUTERS = (
    diagnostics_router,
    cookies_router,
    analyze_router,
    jobs_router,
    job_files_router,
    settings_router,
    events_router,
)
