"""粗分机 logical typed inputs 与纯解析器。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from src.app.runtime.workline_plugins.contracts import (
    CapabilityEffectEvidence,
    CapabilityEffectResultData,
    CapabilityEffectResultInput,
    CommandCode,
    CommandResultInput,
)


class ScanCompletedInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route: Literal["SCAN_COMPLETED"] = "SCAN_COMPLETED"
    payload: dict[str, Any]


class BusinessTimeoutInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route: Literal["BUSINESS_TIMEOUT"] = "BUSINESS_TIMEOUT"
    command_code: CommandCode
    wait_type: Literal["COMMAND_RESULT"]


class ReplayRequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route: Literal["REPLAY_REQUEST"] = "REPLAY_REQUEST"
    idempotency_key: str
    payload_digest: str


type RoughSorterInput = (
    ScanCompletedInput | CommandResultInput | BusinessTimeoutInput | ReplayRequestInput | CapabilityEffectResultInput
)


def parse_scan_completed(payload: dict[str, Any]) -> ScanCompletedInput:
    return ScanCompletedInput(payload=payload)


def parse_command_result(payload: dict[str, Any]) -> CommandResultInput:
    return CommandResultInput.model_validate(payload)


def parse_business_timeout(payload: dict[str, Any]) -> BusinessTimeoutInput:
    return BusinessTimeoutInput.model_validate(payload)


def parse_replay_request(payload: dict[str, Any]) -> ReplayRequestInput:
    return ReplayRequestInput.model_validate(payload)


def parse_capability_effect_result(payload: dict[str, Any]) -> CapabilityEffectResultInput:
    return CapabilityEffectResultInput.model_validate(payload)


__all__ = [
    "BusinessTimeoutInput",
    "CapabilityEffectEvidence",
    "CapabilityEffectResultData",
    "CapabilityEffectResultInput",
    "ReplayRequestInput",
    "RoughSorterInput",
    "ScanCompletedInput",
    "parse_business_timeout",
    "parse_capability_effect_result",
    "parse_command_result",
    "parse_replay_request",
    "parse_scan_completed",
]
