from datetime import datetime

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


class RenderJobCreated(BaseModel):
    job_id: str
    demo_id: str
    job_type: str
    status: str
    video: ReplayVideoStatus
