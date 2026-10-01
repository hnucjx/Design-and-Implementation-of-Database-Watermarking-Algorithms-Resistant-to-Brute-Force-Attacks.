"""应用日志的落盘与格式化。

为什么需要它：**在这之前应用根本没有配置过 logging**。``YtDlpService`` 里的
``logger.info("proxy resolved: ...")`` 只会向上冒泡到 root logger —— 而 root 没有
handler，于是由 ``logging.lastResort``（级别 WARNING、无格式）接手：INFO 级别的
诊断信息**被静默丢弃**，WARNING 则只剩一行没有时间戳、没有模块名的裸消息。

结果就是「代理明明没生效，可日志里什么都看不到」。对这类本地桌面工具来说，
日志是用户唯一的取证手段，所以这里把它做成：

* **一定会落盘**：``data/logs/app.log``，UTF-8，2 MiB × 3 轮转；
* **人能读**：``时间 级别 模块 | 消息``，消息里既有中文说明也有 ``key=value`` 结构化尾巴；
* **不漏 uvicorn**：把同一个文件 handler 挂到 ``uvicorn`` / ``uvicorn.error`` /
  ``uvicorn.access`` 上（uvicorn 默认 ``propagate=False``，不挂就会漏掉启动与访问日志）；
* **幂等**：``create_app()`` 会被反复调用（测试里尤其多），重复调用只保留一套 handler。
"""

from logging.handlers import RotatingFileHandler
import logging
import os
import sys
from pathlib import Path


LOG_FILENAME = "app.log"
LOG_FORMAT = "%(asctime)s %(levelname)-5s %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")

# 模块级标记，保证幂等。
_configured = False
_file_handler: RotatingFileHandler | None = None
_log_path: Path | None = None


def log_path() -> Path | None:
    """当前正在写入的日志文件（未配置时为 None）。供 /api/diagnostics 展示。"""
    return _log_path


def resolve_log_level(value: str | None = None) -> int:
    raw = (value or os.getenv("YTDL_LOG_LEVEL") or "INFO").strip().upper()
    return getattr(logging, raw, logging.INFO) if isinstance(getattr(logging, raw, None), int) else logging.INFO


def configure_logging(log_dir: Path, level: str | None = None, enable_file: bool = True) -> Path | None:
    """配置 root logger（控制台 + 文件）。返回日志文件路径；``enable_file=False`` 时返回 None。

    重复调用不会叠加 handler。
    """
    global _configured, _file_handler, _log_path
    if _configured:
        return _log_path

    numeric_level = resolve_log_level(level)
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    root = logging.getLogger()
    root.setLevel(numeric_level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(stream=sys.stderr)
    console.setFormatter(formatter)
    console.setLevel(numeric_level)
    root.addHandler(console)

    created: Path | None = None
    if enable_file:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            created = log_dir / LOG_FILENAME
            handler = RotatingFileHandler(created, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8")
            handler.setFormatter(formatter)
            handler.setLevel(numeric_level)
            root.addHandler(handler)
            _file_handler = handler
            _log_path = created
        except OSError as exc:  # 只读目录 / 权限问题不该让应用起不来
            root.warning("无法创建日志文件（%s），本次运行只有控制台日志", exc)
            created = None

    # uvicorn 的 logger 默认 propagate=False，不挂上就会漏掉启动与访问日志。
    for name in UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.setLevel(numeric_level)
        uvicorn_logger.propagate = False
        for handler in list(uvicorn_logger.handlers):
            uvicorn_logger.removeHandler(handler)
        uvicorn_logger.addHandler(console)
        if _file_handler is not None:
            uvicorn_logger.addHandler(_file_handler)

    _configured = True
    logging.getLogger(__name__).info(
        "日志已就绪 文件=%s 级别=%s",
        created or "<仅控制台>",
        logging.getLevelName(numeric_level),
    )
    return created


def reset_logging() -> None:
    """只给测试用：让 ``configure_logging`` 可以再次生效。"""
    global _configured, _file_handler, _log_path
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001 - 关闭失败不影响测试结论
            pass
    _configured = False
    _file_handler = None
    _log_path = None
