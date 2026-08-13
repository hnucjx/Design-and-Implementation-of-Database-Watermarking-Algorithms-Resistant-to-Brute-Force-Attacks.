from pathlib import Path

from sqlalchemy import text

from app.config import AppSettings
from app.db import create_app_engine, init_db
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
