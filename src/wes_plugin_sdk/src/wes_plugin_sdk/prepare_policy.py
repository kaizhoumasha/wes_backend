"""prepare 选择与准入的纯值合同，不携带 ORM 或外部访问能力。"""

from dataclasses import dataclass
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


class PickingTaskPreparePolicy(Protocol):
    def select_task_type(self, context: PrepareContext) -> PrepareTaskType | None: ...
