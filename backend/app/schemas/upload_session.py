"""JSON shapes of the chunked, resumable .dem upload (docs/api_reference_v1.md).

Session errors use the structured `{"detail": {code, message, retryAfterSeconds?}}`
shape, with any extra fields (`sessionId`, `filename`, `size`, `receivedBytes` for
`upload_session_exists`; `missingParts` for `upload_parts_missing`) flat inside
`detail`. Intake rejections keep the legacy `{"detail": safe, "errorCode": code}`.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.demo import DemoListItem

UploadSessionState = Literal["open", "completing", "completed", "failed"]


class UploadSessionCreate(BaseModel):
    filename: str = Field(max_length=1024)
    size: int = Field(ge=0)
    contentType: str | None = Field(default=None, max_length=255)
    # Discard the owner's open session (and its received parts) first.
    replace: bool = False


class UploadSessionCreated(BaseModel):
    sessionId: str
    uploadToken: str
    partSize: int
    partCount: int
    maxParallelParts: int
    expiresAt: datetime
    receivedParts: list[int] = Field(default_factory=list)


class UploadSessionError(BaseModel):
    """Why a failed session failed: an intake error code and its safe message."""

    code: str
    message: str


class UploadSessionStatus(BaseModel):
    sessionId: str
    state: UploadSessionState
    filename: str
    size: int
    partSize: int
    partCount: int
    maxParallelParts: int
    receivedParts: list[int]
    receivedBytes: int
    # sha256 of the stored part 0, for the client's same-file spot check on resume.
    part0Sha256: str | None = None
    expiresAt: datetime
    demo: DemoListItem | None = None
    error: UploadSessionError | None = None


class UploadSessionTokenResponse(UploadSessionStatus):
    uploadToken: str


class UploadSessionCurrent(BaseModel):
    session: UploadSessionStatus | None = None


class UploadPartReceived(BaseModel):
    index: int
    sizeBytes: int
    sha256: str
    receivedCount: int


class UploadSessionCompleting(BaseModel):
    state: Literal["completing"] = "completing"
