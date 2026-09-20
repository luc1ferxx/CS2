from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The three verdicts docs/project_status_2026-09-13.md asks players for. A rule
# firing only proves a suggestion is consistent with the parsed facts; these
# say whether it was worth showing.
CoachingVerdict = Literal["helpful", "irrelevant", "unsure"]
COACHING_VERDICTS: tuple[str, ...] = ("helpful", "irrelevant", "unsure")
COACHING_NOTE_MAX_LENGTH = 240


class CoachingFeedbackIn(BaseModel):
    verdict: CoachingVerdict
    note: str | None = Field(default=None, max_length=COACHING_NOTE_MAX_LENGTH)


class CoachingFeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    verdict: str
    note: str | None
    updated_at: datetime


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
    # The requesting owner's own verdict on this suggestion; never another
    # owner's, and absent on owner-less internal reads.
    feedback: CoachingFeedbackOut | None = None


class CoachingRuleFeedbackSummary(BaseModel):
    rule_id: str
    total: int
    rated: int
    helpful: int
    irrelevant: int
    unsure: int


class CoachingFeedbackSummary(BaseModel):
    demo_count: int
    total: int
    rated: int
    helpful: int
    irrelevant: int
    unsure: int
    rules: list[CoachingRuleFeedbackSummary]
