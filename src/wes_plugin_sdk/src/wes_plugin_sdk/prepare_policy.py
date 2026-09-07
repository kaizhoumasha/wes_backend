"""prepare 选择与准入的纯值合同，不携带 ORM 或外部访问能力。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol


class PrepareTaskType(StrEnum):
    MANUAL = "MANUAL"
    AUTO = "AUTO"


@dataclass(frozen=True, slots=True)
class PrepareContext:
    is_active: bool
    line_type: str | None
    run_mode: str | None
    plugin_key: str | None
    flow_mode: str | None


@dataclass(frozen=True, slots=True)
class PrepareDeviceFact:
    contract_key: str
    contract_version: str
    status_max_age_ms: int
    observed_contract_key: str | None
    observed_contract_version: str | None
    received_at: datetime | None
    mode: str | None
    status: str | None
    current_command_code: str | None


@dataclass(frozen=True, slots=True)
class PrepareRuntimeFacts:
    has_active_incident: bool
    has_position_bindings: bool
    devices: tuple[PrepareDeviceFact, ...]
    has_positioned_object: bool


class PickingTaskPreparePolicy(Protocol):
    def select_task_type(self, context: PrepareContext) -> PrepareTaskType | None: ...

    def is_ready(self, facts: PrepareRuntimeFacts, *, now: datetime) -> bool: ...
