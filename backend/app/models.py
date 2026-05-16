from datetime import UTC, datetime
from enum import StrEnum
from typing import Optional

from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(UTC)


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    paused = "paused"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class DownloadMode(StrEnum):
    video_subtitles = "video_subtitles"
    video_only = "video_only"
    subtitles_only = "subtitles_only"


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str


class Job(SQLModel, table=True):
    id: str = Field(primary_key=True)
    url: str
    title: str
    status: str = Field(default=JobStatus.queued.value, index=True)
    options_json: str
    progress: float = 0.0
    speed: Optional[float] = None
    eta: Optional[int] = None
    total_items: int = 0
    completed_items: int = 0
    failed_items: int = 0
    current_item_title: Optional[str] = None
    error: Optional[str] = None
    download_dir: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class JobItem(SQLModel, table=True):
    id: str = Field(primary_key=True)
    job_id: str = Field(index=True)
    source_url: str
    title: str
    index: int = 1
    status: str = Field(default=JobStatus.queued.value, index=True)
    progress: float = 0.0
    downloaded_bytes: Optional[int] = None
    total_bytes: Optional[int] = None
    speed: Optional[float] = None
    eta: Optional[int] = None
    output_path: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class JobEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: str = Field(index=True)
    item_id: Optional[str] = Field(default=None, index=True)
    event_type: str
    payload_json: str
    created_at: datetime = Field(default_factory=utc_now)
