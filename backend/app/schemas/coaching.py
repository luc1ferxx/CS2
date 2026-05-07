from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CoachingEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    demo_id: str
    round_number: int
    player_id: str
    player_name: str
    tick_start: int
    tick_end: int
    category: str
    severity: str
    title: str
    message: str
    structured_context_json: dict[str, object]
    confidence: float
    created_at: datetime
