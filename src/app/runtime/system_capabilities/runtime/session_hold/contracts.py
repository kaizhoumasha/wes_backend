"""普通 Session Hold typed input/output。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

SessionFactVersion = Annotated[str, StringConstraints(pattern=r"^session:\d+$")]


class SessionHoldPrecondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_status: str = Field(min_length=1, max_length=40)


class SessionHoldAdmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    precondition: SessionHoldPrecondition
    fact_version: SessionFactVersion


class SessionHoldInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    failure_domain: str = Field(default="SYSTEM_CAPABILITY", min_length=1, max_length=100)
    reason_code: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=500)


class SessionHoldOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    held: bool
    reason_code: str


__all__ = ["SessionHoldAdmission", "SessionHoldInput", "SessionHoldOutput", "SessionHoldPrecondition"]
