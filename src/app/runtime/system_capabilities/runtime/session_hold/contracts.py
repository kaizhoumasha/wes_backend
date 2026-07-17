"""普通 Session Hold typed input/output。"""

from pydantic import BaseModel, ConfigDict, Field


class SessionHoldInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    failure_domain: str = Field(default="SYSTEM_CAPABILITY", min_length=1, max_length=100)
    reason_code: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=500)


class SessionHoldOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    held: bool
    reason_code: str


__all__ = ["SessionHoldInput", "SessionHoldOutput"]
