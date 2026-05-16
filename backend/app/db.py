from collections.abc import Generator

from sqlalchemy.engine import Engine
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from .config import AppSettings


def create_app_engine(settings: AppSettings) -> Engine:
    settings.ensure_directories()
    sqlite_url = f"sqlite:///{settings.database_path.as_posix()}"
    return create_engine(sqlite_url, connect_args={"check_same_thread": False})


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
