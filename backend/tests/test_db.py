from pathlib import Path
from types import SimpleNamespace
import sqlite3

from sqlalchemy import text

from sqlmodel import Session

from app.config import AppSettings
from app.db import _configure_sqlite_connection, checkpoint_wal, create_app_engine, init_db
from app.models import Job  # noqa: F401 — register SQLModel metadata


def test_sqlite_engine_enables_wal_and_busy_timeout(tmp_path: Path) -> None:
    settings = AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
    )
    engine = create_app_engine(settings)
    init_db(engine)

    with engine.connect() as connection:
        journal_mode = connection.execute(text("PRAGMA journal_mode")).scalar()
        busy_timeout = connection.execute(text("PRAGMA busy_timeout")).scalar()
        synchronous = connection.execute(text("PRAGMA synchronous")).scalar()

    assert str(journal_mode).lower() == "wal"
    assert int(busy_timeout) == 5000
    assert int(synchronous) == 1


def test_checkpoint_wal_folds_the_write_ahead_log_back_into_the_database(tmp_path: Path) -> None:
    settings = AppSettings(
        data_dir=tmp_path / "data",
        download_dir=tmp_path / "downloads",
        database_path=tmp_path / "data" / "app.sqlite3",
    )
    engine = create_app_engine(settings)
    init_db(engine)

    with Session(engine) as session:
        session.add(
            Job(
                id="job-1",
                url="https://youtu.be/one",
                title="One",
                status="queued",
                options_json="{}",
            )
        )
        session.commit()

    wal_path = tmp_path / "data" / "app.sqlite3-wal"
    assert wal_path.exists()

    checkpoint_wal(engine)

    with engine.connect() as connection:
        result = connection.execute(text("PRAGMA wal_checkpoint(TRUNCATE)")).fetchone()
    assert result is not None
    busy, _log_size, _checkpointed = int(result[0]), int(result[1]), int(result[2])
    assert busy == 0


class _FailingEngine:
    def begin(self):
        raise RuntimeError("database is locked")


def test_checkpoint_wal_swallows_errors_so_lifespan_never_fails() -> None:
    checkpoint_wal(_FailingEngine())  # type: ignore[arg-type]


class _LockedWalCursor:
    def execute(self, sql: str) -> None:
        if "journal_mode=WAL" in sql:
            raise sqlite3.OperationalError("database is locked")

    def fetchall(self) -> list:
        return []

    def close(self) -> None:
        return None


def test_wal_pragma_does_not_raise_when_database_is_locked() -> None:
    connection = SimpleNamespace(cursor=lambda: _LockedWalCursor())
    _configure_sqlite_connection(connection, None)
