"""粗分机 logical typed inputs 与纯解析器。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ScanCompletedInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route: Literal["SCAN_COMPLETED"] = "SCAN_COMPLETED"
    payload: dict[str, Any]


class PickAndPutResultInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route: Literal["PICK_AND_PUT_RESULT"] = "PICK_AND_PUT_RESULT"
    command_code: str
    command_type: Literal["PICK_AND_PUT"]
    result: str
    data: dict[str, Any] = Field(default_factory=dict)
    error_detail: dict[str, Any] = Field(default_factory=dict)


class BusinessTimeoutInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route: Literal["BUSINESS_TIMEOUT"] = "BUSINESS_TIMEOUT"
    command_code: str
    wait_type: Literal["COMMAND_RESULT"]


class ReplayRequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route: Literal["REPLAY_REQUEST"] = "REPLAY_REQUEST"
    idempotency_key: str
    payload_digest: str


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
    "ReplayRequestInput",
    "RoughSorterInput",
    "ScanCompletedInput",
    "parse_business_timeout",
    "parse_pick_and_put_result",
    "parse_replay_request",
    "parse_scan_completed",
]
