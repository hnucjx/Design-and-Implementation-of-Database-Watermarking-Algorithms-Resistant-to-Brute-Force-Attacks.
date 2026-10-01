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
    default_speed_limit_kbps: int | None = Field(default=None, ge=1)
    default_retries: int = Field(default=10, ge=0, le=20)
    youtube_po_token: str | None = None
    youtube_visitor_data: str | None = None
    youtube_po_browser_path: str | None = None
    # 显式指定 JS 运行时（Deno / Node）可执行文件。留空时按「PATH -> 常见安装目录」自动探测；
    # yt-dlp 用它解 YouTube 的 nsig（JS challenge），探测不到会直接导致提取失败。
    js_runtime_path: str | None = None
    youtube_max_parallel_downloads: int = Field(default_factory=default_download_concurrency, ge=1)
    anti403_http_chunk_size_mb: int = Field(default=16, ge=1)
    # 0 = 关闭节流守卫。> 0 时写入 yt-dlp 的 throttledratelimit，会在单条流速度低于该值时
    # 触发 ThrottledDownload(ReExtractInfo)，被 yt-dlp 的无计数重提取循环接住 -> 反复中断重启。
    # 仅建议在并发 = 1 时开启，见 PLAN.md §P0-1。
    throttled_rate_kbps: int = Field(default=0, ge=0)
    # 停滞看门狗：N 秒内没有任何新增字节就让任务失败（而不是永久 running）。0 = 关闭。
    stall_timeout_seconds: float = Field(default=90.0, ge=0)
    # aria2c 默认关闭：多连接是 YouTube 侧最敏感的触发条件，403/限速风险显著上升。
    # 仅在显式开启 aria2c 后生效，连接数保持保守（默认 2，上限 4）。
    aria2c_enabled: bool = False
    aria2c_path: str | None = None
    aria2c_connections: int = Field(default=2, ge=1, le=4)

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
