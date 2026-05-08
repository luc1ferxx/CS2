from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


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
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class DemoStatus(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    map_name: str
    round_count: int
    coaching_event_count: int
    error_message: str | None
    updated_at: datetime
    completed_at: datetime | None


class ReplayVideoStatus(BaseModel):
    status: str
    url: str | None
    durationSeconds: float
    tickStart: int
    tickEnd: int
    tickRate: int
    source: str
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
    metadata: dict[str, Any]
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


class RenderJobCreated(BaseModel):
    job_id: str
    demo_id: str
    job_type: str
    status: str
    metadata: dict[str, Any]
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    video: ReplayVideoStatus
