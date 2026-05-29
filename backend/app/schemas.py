from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


DownloadMode = Literal["video_subtitles", "video_only", "subtitles_only"]
SubtitleSource = Literal["human", "auto", "both"]
SubtitleFormat = Literal["best", "srt", "vtt"]
JobBatchAction = Literal["pause", "restart", "delete"]
CookieSource = Literal["none", "file", "browser"]


class AnalyzeRequest(BaseModel):
    url: str = Field(min_length=1)
    cookies_enabled: bool = True


class FormatOption(BaseModel):
    format_id: str
    label: str
    height: int | None = None
    ext: str | None = None
    fps: float | None = None
    filesize: int | None = None


class SubtitleOption(BaseModel):
    language: str
    name: str | None = None
    formats: list[str] = Field(default_factory=list)


class VideoEntry(BaseModel):
    index: int
    id: str | None = None
    title: str
    url: str
    duration: float | None = None
    thumbnail: str | None = None


class AnalyzeResponse(BaseModel):
    url: str
    title: str
    is_playlist: bool
    duration: float | None = None
    thumbnail: str | None = None
    entries: list[VideoEntry] = Field(default_factory=list)
    formats: list[FormatOption] = Field(default_factory=list)
    subtitles: list[SubtitleOption] = Field(default_factory=list)
    automatic_subtitles: list[SubtitleOption] = Field(default_factory=list)
    ffmpeg: dict[str, bool] = Field(default_factory=dict)


class DownloadOptions(BaseModel):
    mode: DownloadMode = "video_subtitles"
    resolution: str = "1440p"
    format_id: str | None = None
    subtitle_languages: list[str] = Field(default_factory=list)
    subtitle_source: SubtitleSource = "both"
    subtitle_format: SubtitleFormat = "best"
    playlist_items: list[int] | None = None
    write_metadata: bool = False
    write_thumbnail: bool = False
    skip_existing: bool = True
    speed_limit_kbps: int | None = Field(default=None, ge=1)
    retries: int = Field(default=10, ge=0, le=20)
    notify_on_complete: bool = False


class CreateJobRequest(BaseModel):
    url: str = Field(min_length=1)
    options: DownloadOptions = Field(default_factory=DownloadOptions)


class RestartJobRequest(BaseModel):
    resolution: str | None = None


class JobBatchActionRequest(BaseModel):
    action: JobBatchAction
    job_ids: list[str] = Field(min_length=1)
    delete_files: bool = False


class DeleteJobItemsRequest(BaseModel):
    item_ids: list[str] = Field(min_length=1)
    delete_files: bool = False


class ResolutionFallback(BaseModel):
    requested_resolution: str
    fallback_resolution: str
    reason: str | None = None
    restart_resolution: str | None = None
    message: str


class JobItemRead(BaseModel):
    id: str
    job_id: str
    source_url: str
    title: str
    index: int
    status: str
    progress: float
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    speed: float | None = None
    eta: int | None = None
    output_path: str | None = None
    actual_width: int | None = None
    actual_height: int | None = None
    actual_format: str | None = None
    requested_resolution: str | None = None
    fallback_resolution: str | None = None
    fallback_reason: str | None = None
    resolution_fallback: ResolutionFallback | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    elapsed_seconds: int = 0


class JobRead(BaseModel):
    id: str
    url: str
    title: str
    status: str
    progress: float
    speed: float | None = None
    eta: int | None = None
    total_items: int
    completed_items: int
    failed_items: int
    current_item_title: str | None = None
    error: str | None = None
    download_dir: str | None = None
    actual_resolution: str | None = None
    actual_format: str | None = None
    resolution_fallback: ResolutionFallback | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    elapsed_seconds: int = 0
    items: list[JobItemRead] = Field(default_factory=list)


class JobBatchActionResponse(BaseModel):
    affected_job_ids: list[str]
    jobs: list[JobRead] = Field(default_factory=list)


class DeleteJobItemsResponse(BaseModel):
    deleted_item_ids: list[str]
    job_deleted: bool
    job: JobRead | None = None


class SettingsRead(BaseModel):
    download_dir: str
    default_concurrency: int
    default_subtitle_languages: list[str]
    default_resolution: str
    cookies_enabled: bool
    ffmpeg: dict[str, bool]


class SettingsUpdate(BaseModel):
    download_dir: str | None = None
    default_concurrency: int | None = Field(default=None, ge=1)
    default_subtitle_languages: list[str] | None = None
    default_resolution: str | None = None


class CookieStatus(BaseModel):
    enabled: bool
    filename: str | None = None
    source: CookieSource = "none"
    browser: str | None = None
    imported_count: int | None = None


class BrowserCookieImportRequest(BaseModel):
    browser: str = "auto"
    close_browser_if_locked: bool = False


class DiagnosticsRead(BaseModel):
    cookies_enabled: bool
    dependencies: dict[str, bool | int | str | None | list[str]]
