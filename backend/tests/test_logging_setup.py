from pathlib import Path

import pytest

from app import logging_setup


@pytest.fixture(autouse=True)
def _clean_logging():
    logging_setup.reset_logging()
    yield
    logging_setup.reset_logging()


def test_configure_logging_writes_to_a_file(tmp_path: Path) -> None:
    """INFO 级别必须真的落盘 —— 这是本模块存在的唯一理由。"""
    import logging

    path = logging_setup.configure_logging(tmp_path)

    assert path == tmp_path / "app.log"
    assert logging_setup.log_path() == path
    logging.getLogger("cascade.test").info("hello-file-log")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert path is not None and path.exists()
    content = path.read_text(encoding="utf-8")
    assert "hello-file-log" in content
    # 格式必须带时间戳与模块名，否则排障时无法定位是谁写的。
    assert "cascade.test" in content


def test_configure_logging_is_idempotent(tmp_path: Path) -> None:
    """create_app 会被反复调用，重复配置不能叠加 handler。"""
    import logging

    first = logging_setup.configure_logging(tmp_path / "a")
    second = logging_setup.configure_logging(tmp_path / "b")

    assert first == second
    assert len(logging.getLogger().handlers) == 2  # 控制台 + 文件，各一个


def test_configure_logging_survives_an_unwritable_directory(tmp_path: Path) -> None:
    """日志目录不可写时只能降级，不能让应用起不来。"""
    blocker = tmp_path / "logs"
    blocker.write_text("this is a file, not a directory", encoding="utf-8")

    assert logging_setup.configure_logging(blocker) is None
    assert logging_setup.log_path() is None


def test_resolve_log_level_falls_back_to_info(monkeypatch) -> None:
    import logging

    monkeypatch.delenv("YTDL_LOG_LEVEL", raising=False)
    assert logging_setup.resolve_log_level() == logging.INFO
    assert logging_setup.resolve_log_level("debug") == logging.DEBUG
    assert logging_setup.resolve_log_level("not-a-level") == logging.INFO
