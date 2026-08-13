from collections.abc import Generator
import logging
import sqlite3

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from .config import AppSettings

logger = logging.getLogger(__name__)


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.fetchall()
        cursor.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.OperationalError:
        logger.warning("SQLite WAL pragma skipped because the database is locked")
    finally:
        cursor.close()


def create_app_engine(settings: AppSettings) -> Engine:
    settings.ensure_directories()
    sqlite_url = f"sqlite:///{settings.database_path.as_posix()}"
    engine = create_engine(
        sqlite_url,
        connect_args={"check_same_thread": False, "timeout": 5},
    )
    event.listen(engine, "connect", _configure_sqlite_connection)
    return engine


def init_db(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine)
    _ensure_columns(engine)


def _ensure_columns(engine: Engine) -> None:
    additions = {
        "job": {
            "speed": "FLOAT",
            "eta": "INTEGER",
            "started_at": "DATETIME",
            "finished_at": "DATETIME",
            "download_dir": "TEXT",
        },
        "jobitem": {
            "started_at": "DATETIME",
            "finished_at": "DATETIME",
            "actual_width": "INTEGER",
            "actual_height": "INTEGER",
            "actual_format": "TEXT",
            "options_json": "TEXT",
            "requested_resolution": "TEXT",
            "fallback_resolution": "TEXT",
            "fallback_reason": "TEXT",
        },
    }
    with engine.begin() as connection:
        for table_name, columns in additions.items():
            existing = {row[1] for row in connection.execute(text(f"PRAGMA table_info({table_name})"))}
            for column_name, column_type in columns.items():
                if column_name not in existing:
                    connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))


def session_dependency(engine: Engine):
    def get_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    return get_session
