"""Workline 插件决策最小合同。"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from src.app.runtime.orchestration.runtime_intent import RuntimeIntent  # noqa: TC001
from src.app.runtime.system_capabilities.outcomes import BusinessReject  # noqa: TC001

MAX_PLUGIN_DECISION_INTENTS = 32
OutcomeCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
StableInputString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
CommandCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class CommandResultStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    ERROR = "ERROR"


class CommandResultInput(BaseModel):
    """所有 command callback 共用的逻辑 route 输入。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    route: StableInputString = "COMMAND_RESULT"
    command_code: CommandCode
    command_type: StableInputString
    result: CommandResultStatus
    data: dict[str, Any] = Field(default_factory=dict)
    error_detail: dict[str, Any] = Field(default_factory=dict)


class CapabilityEffectEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_key: StableInputString
    contract_version: StableInputString
    operation_key: StableInputString
    idempotency_key: StableInputString
    payload_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    outcome_kind: Literal["business_reject"]
    outcome_code: StableInputString
    outcome: BusinessReject
    occurred_at_ms: int = Field(ge=0)


class CapabilityEffectResultData(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: int = Field(gt=0)
    effect_evidence: CapabilityEffectEvidence


class CapabilityEffectResultInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    logical_route: Literal["CAPABILITY_EFFECT_RESULT"] = "CAPABILITY_EFFECT_RESULT"
    data: CapabilityEffectResultData

    @property
    def effect_evidence(self) -> CapabilityEffectEvidence:
        return self.data.effect_evidence


class PluginContext[TState: BaseModel](BaseModel):
    """插件只读决策上下文；state 类型由具体插件合同锁定。"""

    model_config = ConfigDict(frozen=True)

    state: TState


class PluginDecision[TState: BaseModel](BaseModel):
    """插件决策数据；runtime 后续执行 intents，本合同自身不执行。"""

    model_config = ConfigDict(frozen=True)

    intents: tuple[RuntimeIntent, ...] = Field(max_length=MAX_PLUGIN_DECISION_INTENTS)
    next_state: TState
    outcome_code: OutcomeCode


__all__ = [
    "MAX_PLUGIN_DECISION_INTENTS",
    "CapabilityEffectEvidence",
    "CapabilityEffectResultData",
    "CapabilityEffectResultInput",
    "CommandCode",
    "CommandResultInput",
    "CommandResultStatus",
    "PluginContext",
    "PluginDecision",
]
