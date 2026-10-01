"""行为层回归：`finally` 收尾**不再吞掉**逃逸中的异常（008）。

静态守卫（`tests/test_finally_guards.py`）只证明「源码里没有 `return` in `finally`」，
证明不了行为。这条测试把真实的 `JobManager._run_item` 推到下面这个组合状态：

1. 条目在下载**过程中**被删除（`_deleted_items` 里已经有它），并且
2. 异常处理路径**自己也出错**（这里用 monkeypatch 让 `_log_item_failure` 抛异常）。

这正是 `finally` 里那句 `return` 唯一的伤害窗口 —— 它只在
「已经在传播一个异常」且「分支命中 `return`」时生效：

- 修复前：`return` 把逃逸中的异常吞掉，`_run_item` 正常返回。条目永远停在 `running`，
  日志里连一行都没有（收尾日志自己就是抛异常的那一步），比直接失败难查得多。
- 修复后：异常向上传播，worker 与测试都能看到它。

注意必须**直接调用** `_run_item`：`_run_item_work` 在入队阶段就会因为
`_deleted_items` 提前 return，走不到收尾逻辑。这对应「下载已经开始，用户才删掉条目」。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlmodel import Session

from app.config import AppSettings
from app.db import create_app_engine, init_db
from app.events import EventBroker
from app.job_manager import JobManager, new_id
from app.models import Job, JobItem, JobStatus
from app.schemas import DownloadOptions

from fakes import FakeYtDlpService

LOG_FAILURE_MESSAGE = "收尾日志炸了"


class ExplodingDownloadService(FakeYtDlpService):
    """下载必然失败：把流程推进到 `except Exception` 分支。"""

    def download(self, url, options, progress_hook, should_cancel, cookies_path=None, download_dir=None):
        raise RuntimeError("network down")


def _raise_log_failure(item, options, exc):
    """顶替 `_log_item_failure`：模拟收尾阶段自己出错。"""
    raise RuntimeError(LOG_FAILURE_MESSAGE)


def _make_manager(tmp_path: Path):
    settings = AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
        default_concurrency=1,
    )
    settings.ensure_directories()
    engine = create_app_engine(settings)
    init_db(engine)
    return JobManager(engine, settings, ExplodingDownloadService(), EventBroker()), engine


def _persist_queued_item(session: Session, tmp_path: Path, options: DownloadOptions) -> tuple[Job, JobItem]:
    job = Job(
        id=new_id(),
        url="https://youtu.be/boom",
        title="Boom",
        options_json=options.model_dump_json(),
        total_items=1,
        download_dir=str(tmp_path),
    )
    item = JobItem(
        id=new_id(),
        job_id=job.id,
        source_url="https://youtu.be/boom",
        title="Boom",
        index=1,
        status=JobStatus.queued.value,
    )
    session.add(job)
    session.add(item)
    session.commit()
    return job, item


def test_run_item_propagates_error_raised_while_the_item_is_deleted(tmp_path: Path, monkeypatch) -> None:
    manager, engine = _make_manager(tmp_path)
    options = DownloadOptions(resolution="720p")

    with Session(engine) as session:
        job, item = _persist_queued_item(session, tmp_path, options)
        # 下载过程中条目被删掉 —— 收尾分支命中，旧代码在这里 `return`。
        manager._deleted_items.add(item.id)
        monkeypatch.setattr(manager, "_log_item_failure", _raise_log_failure)

        with pytest.raises(RuntimeError, match=LOG_FAILURE_MESSAGE):
            manager._run_item(session, job, item, options, tmp_path)


def test_run_item_skips_bookkeeping_when_the_item_is_deleted(tmp_path: Path, monkeypatch) -> None:
    """对照面：删除竞态下，收尾**确实**没有回写状态 —— 去掉 `return` 不能顺手改掉这个语义。"""
    manager, engine = _make_manager(tmp_path)
    options = DownloadOptions(resolution="720p")

    with Session(engine) as session:
        job, item = _persist_queued_item(session, tmp_path, options)
        manager._deleted_items.add(item.id)

        manager._run_item(session, job, item, options, tmp_path)

    with Session(engine) as session:
        assert session.get(JobItem, item.id).status == JobStatus.running.value
        assert session.get(Job, job.id).status != JobStatus.succeeded.value


async def test_worker_survives_an_item_that_crashes_while_finishing(tmp_path: Path, monkeypatch) -> None:
    """异常既然不再被吞，就必须有人接住 —— 否则只是把「静默卡住」换成「静默少一个并发」。"""
    manager, engine = _make_manager(tmp_path)
    options = DownloadOptions(resolution="720p")
    with Session(engine) as session:
        crashing_job, crashing_item = _persist_queued_item(session, tmp_path, options)
        healthy_job, healthy_item = _persist_queued_item(session, tmp_path, options)
        # commit 会 expire 属性，跨 session 再取就会 DetachedInstanceError —— 先把 id 取出来。
        crashing_job_id, crashing_item_id = crashing_job.id, crashing_item.id
        healthy_job_id, healthy_item_id = healthy_job.id, healthy_item.id

    handled: list[str] = []
    real_work = manager._run_item_work

    def flaky(item_id: str) -> None:
        handled.append(item_id)
        if item_id == crashing_item_id:
            raise RuntimeError("bookkeeping exploded")
        real_work(item_id)

    monkeypatch.setattr(manager, "_run_item_work", flaky)
    await manager.start()
    try:
        await manager.enqueue(crashing_job_id)
        await manager.enqueue(healthy_job_id)
        for _ in range(300):
            if healthy_item_id in handled:
                break
            await asyncio.sleep(0.01)

        # 崩掉的那条被隔离，队列继续被消费 —— worker 还活着。
        assert handled == [crashing_item_id, healthy_item_id]
        assert not manager._workers[0].done()
        with Session(engine) as session:
            assert session.get(JobItem, crashing_item_id).status == JobStatus.failed.value
    finally:
        await manager.stop()


def test_crashed_item_is_marked_failed_instead_of_stuck_running(tmp_path: Path) -> None:
    manager, engine = _make_manager(tmp_path)
    options = DownloadOptions(resolution="720p")
    with Session(engine) as session:
        job, item = _persist_queued_item(session, tmp_path, options)
        item.status = JobStatus.running.value
        session.add(item)
        session.commit()

        manager._mark_item_failed_after_crash(item.id)

    with Session(engine) as session:
        stored = session.get(JobItem, item.id)
        assert stored.status == JobStatus.failed.value
        assert stored.error
        assert stored.finished_at is not None


@pytest.mark.parametrize("terminal_status", [JobStatus.succeeded.value, JobStatus.failed.value])
def test_crashed_item_marker_leaves_finished_items_alone(tmp_path: Path, terminal_status: str) -> None:
    """兜底只负责「抢救 running / queued」，不能改写已经落定的结果。"""
    manager, engine = _make_manager(tmp_path)
    options = DownloadOptions(resolution="720p")
    with Session(engine) as session:
        job, item = _persist_queued_item(session, tmp_path, options)
        item.status = terminal_status
        item.error = "原始失败原因"
        session.add(item)
        session.commit()

        manager._mark_item_failed_after_crash(item.id)

    with Session(engine) as session:
        stored = session.get(JobItem, item.id)
        assert stored.status == terminal_status
        assert stored.error == "原始失败原因"


def test_crashed_item_marker_ignores_an_unknown_item(tmp_path: Path) -> None:
    """workspace 里可能有已经不存在的 item_id（删除竞态），兜底必须安静地什么也不做。"""
    manager, _ = _make_manager(tmp_path)
    manager._mark_item_failed_after_crash("does-not-exist")
