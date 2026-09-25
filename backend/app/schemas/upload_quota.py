from pydantic import BaseModel


class UploadQuotaResponse(BaseModel):
    """The owner's upload allowance; a null limit means that limit is off."""

    dailyLimit: int | None
    dailyUsed: int
    dailyResetSeconds: int | None
    activeLimit: int | None
    activeCount: int
    maxUploadBytes: int
