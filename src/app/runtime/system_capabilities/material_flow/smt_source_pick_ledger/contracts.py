"""SMT source-pick ledger LOCAL_TRANSACTIONAL typed contract。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

CommandSuccessFactVersion = Annotated[
    str,
    StringConstraints(pattern=r"^command:[0-9a-f]{32}:SUCCESS$"),
]


class SmtSourcePickLedgerPrecondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_status: Literal["CLAIMED_BY_SORTING"]


class SmtSourcePickLedgerAdmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    precondition: SmtSourcePickLedgerPrecondition
    fact_version: CommandSuccessFactVersion


class SmtSourcePickLedgerInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: Literal["RECORD_PICKED"]
    command_code: str = Field(min_length=1, max_length=160)


class SmtSourcePickLedgerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["PICKED"]
    advanced: bool


__all__ = [
    "SmtSourcePickLedgerAdmission",
    "SmtSourcePickLedgerInput",
    "SmtSourcePickLedgerOutput",
    "SmtSourcePickLedgerPrecondition",
]
