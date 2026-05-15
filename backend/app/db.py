from collections.abc import Generator

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from .config import AppSettings


def create_app_engine(settings: AppSettings) -> Engine:
    settings.ensure_directories()
    sqlite_url = f"sqlite:///{settings.database_path.as_posix()}"
    return create_engine(sqlite_url, connect_args={"check_same_thread": False})


def init_db(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine)


def session_dependency(engine: Engine):
    def get_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    return get_session
