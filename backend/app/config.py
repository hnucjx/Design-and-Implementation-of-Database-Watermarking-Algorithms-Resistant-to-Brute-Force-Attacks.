from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parents[2]


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="YTDL_", env_file=".env", extra="ignore")

    data_dir: Path = Field(default_factory=lambda: REPO_ROOT / "data")
    download_dir: Path = Field(default_factory=lambda: REPO_ROOT / "downloads")
    database_path: Path = Field(default_factory=lambda: REPO_ROOT / "data" / "app.sqlite3")
    cookies_filename: str = "cookies.txt"
    default_concurrency: int = 2
    default_resolution: str = "best"
    default_subtitle_languages: list[str] = Field(default_factory=lambda: ["en"])

    @property
    def cookies_path(self) -> Path:
        return self.data_dir / self.cookies_filename

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> AppSettings:
    settings = AppSettings()
    settings.ensure_directories()
    return settings
