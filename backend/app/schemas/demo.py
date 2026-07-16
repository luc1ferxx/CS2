from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ParseFailureMetadata(BaseModel):
    errorCode: str
    message: str
    failedAt: datetime | None = None
    updatedAt: datetime
    retryable: bool
    attemptCount: int


class DemoIngestionStatus(BaseModel):
    phase: str
    active: bool
    stale: bool
    retryable: bool
    attemptCount: int
    jobId: str | None = None
    jobType: str | None = None
    jobStatus: str | None = None
    hasSourceDemo: bool
    updatedAt: datetime
    startedAt: datetime | None = None
    finishedAt: datetime | None = None
    failure: ParseFailureMetadata | None = None


class DemoListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    original_filename: str
    map_name: str
    tick_rate: int
    round_count: int
    coaching_event_count: int
    status: str
    archived: bool = False
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    video_status: str | None = None
    video_source: str | None = None
    latest_render_status: str | None = None
    ingestion: DemoIngestionStatus | None = None


class DemoStatus(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str | None = None
    original_filename: str | None = None
    status: str
    archived: bool = False
    map_name: str
    round_count: int
    coaching_event_count: int
    error_message: str | None
    updated_at: datetime
    completed_at: datetime | None
    ingestion: DemoIngestionStatus | None = None


class DemoUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    archived: bool | None = None


class ReplayVideoStatus(BaseModel):
    status: str
    url: str | None
    durationSeconds: float
    tickStart: int
    tickEnd: int
    tickRate: int
    source: str
    errorCode: str | None = None
    errorMessage: str | None = None
    timeOriginSeconds: float = 0


class VideoCalibrationUpdate(BaseModel):
    durationSeconds: float | None = None
    tickStart: int | None = None
    tickEnd: int | None = None
    tickRate: int | None = None
    timeOriginSeconds: float | None = None


class RenderClipRequest(BaseModel):
    eventId: str | None = None
    playerId: str | None = None
    povSteamId: str | None = None
    tickStart: int
    tickEnd: int
    tickRate: int
    roundNumber: int | None = None
    renderPreset: str | None = None


class RenderJobStatus(BaseModel):
    job_id: str
    demo_id: str
    job_type: str
    status: str
    source: str
    video_status: str | None = None
    tick_start: int | None = None
    tick_end: int | None = None
    tick_rate: int | None = None
    duration_seconds: float | None = None
    event_id: str | None = None
    player_id: str | None = None
    pov_steam_id: str | None = None
    round_number: int | None = None
    render_preset: str | None = None
    metadata: dict[str, Any]
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class RenderJobManifest(BaseModel):
    manifestVersion: str
    jobId: str
    demoId: str
    jobType: str
    status: str
    demoFilePath: str
    demoStorageKey: str | None = None
    replayStorageKey: str | None = None
    originalFilename: str
    mapName: str
    eventId: str | None = None
    playerId: str | None = None
    povSteamId: str | None = None
    tickStart: int
    tickEnd: int
    tickRate: int
    roundNumber: int | None = None
    renderPreset: str


class RenderWorkerResult(BaseModel):
    status: str
    videoUrl: str | None = None
    storageKey: str | None = None
    localMediaPath: str | None = None
    tickStart: int
    tickEnd: int
    tickRate: int
    timeOriginSeconds: float = 0
    durationSeconds: float
    errorMessage: str | None = None


class RenderWorkerResultAccepted(BaseModel):
    job: RenderJobStatus
    video: ReplayVideoStatus


class RenderWorkerMediaUpload(BaseModel):
    jobId: str
    demoId: str
    videoUrl: str
    storageKey: str
    originalFilename: str
    sizeBytes: int


class RenderJobCreated(RenderJobStatus):
    video: ReplayVideoStatus
