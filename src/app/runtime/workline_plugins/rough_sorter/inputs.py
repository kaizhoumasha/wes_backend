"""粗分机 logical typed inputs 与纯解析器。"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

CommandCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
StableInputString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]


class PickAndPutTerminalResult(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    ERROR = "ERROR"


class ScanCompletedInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route: Literal["SCAN_COMPLETED"] = "SCAN_COMPLETED"
    payload: dict[str, Any]


class PickAndPutResultInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route: Literal["PICK_AND_PUT_RESULT"] = "PICK_AND_PUT_RESULT"
    command_code: CommandCode
    command_type: Literal["PICK_AND_PUT"]
    result: PickAndPutTerminalResult
    data: dict[str, Any] = Field(default_factory=dict)
    error_detail: dict[str, Any] = Field(default_factory=dict)


class BusinessTimeoutInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route: Literal["BUSINESS_TIMEOUT"] = "BUSINESS_TIMEOUT"
    command_code: CommandCode
    wait_type: Literal["COMMAND_RESULT"]


class ReplayRequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route: Literal["REPLAY_REQUEST"] = "REPLAY_REQUEST"
    idempotency_key: StableInputString
    payload_digest: StableInputString


type RoughSorterInput = ScanCompletedInput | PickAndPutResultInput | BusinessTimeoutInput | ReplayRequestInput


def parse_scan_completed(payload: dict[str, Any]) -> ScanCompletedInput:
    return ScanCompletedInput(payload=payload)


def parse_pick_and_put_result(payload: dict[str, Any]) -> PickAndPutResultInput:
    return PickAndPutResultInput.model_validate(payload)


def parse_business_timeout(payload: dict[str, Any]) -> BusinessTimeoutInput:
    return BusinessTimeoutInput.model_validate(payload)


def parse_replay_request(payload: dict[str, Any]) -> ReplayRequestInput:
    return ReplayRequestInput.model_validate(payload)


__all__ = [
    "BusinessTimeoutInput",
    "PickAndPutResultInput",
    "PickAndPutTerminalResult",
    "ReplayRequestInput",
    "RoughSorterInput",
    "ScanCompletedInput",
    "parse_business_timeout",
    "parse_pick_and_put_result",
    "parse_replay_request",
    "parse_scan_completed",
]
