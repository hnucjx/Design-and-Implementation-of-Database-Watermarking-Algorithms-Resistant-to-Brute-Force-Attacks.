from functools import lru_cache
import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parents[2]


def default_download_concurrency() -> int:
    try:
        return max(1, int(os.getenv("YTDL_YOUTUBE_MAX_PARALLEL_DOWNLOADS", "5")))
    except ValueError:
        return 5


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="YTDL_", env_file=".env", extra="ignore")

    data_dir: Path = Field(default_factory=lambda: REPO_ROOT / "data")
    download_dir: Path = Field(default_factory=lambda: REPO_ROOT / "downloads")
    database_path: Path = Field(default_factory=lambda: REPO_ROOT / "data" / "app.sqlite3")
    cookies_filename: str = "cookies.txt"
    default_concurrency: int = Field(default_factory=default_download_concurrency)
    default_resolution: str = "1440p"
    default_subtitle_languages: list[str] = Field(default_factory=lambda: ["en"])
    youtube_po_token: str | None = None
    youtube_visitor_data: str | None = None
    youtube_po_browser_path: str | None = None
    youtube_max_parallel_downloads: int = Field(default_factory=default_download_concurrency, ge=1)
    anti403_http_chunk_size_mb: int = Field(default=16, ge=1)
    throttled_rate_kbps: int = Field(default=64, ge=0)
    aria2c_enabled: bool = False
    aria2c_path: str | None = None
    aria2c_connections: int = Field(default=1, ge=1, le=4)

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
