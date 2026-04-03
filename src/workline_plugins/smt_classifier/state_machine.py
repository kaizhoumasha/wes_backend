"""
SMT 粗分机状态机定义

`SmtClassifierStageMachine` / `SmtClassifierStageStatus`
供 workline runtime 校验插件内部 stage 迁移。
"""

from enum import Enum
from typing import Any

from transitions import Machine


class _BaseTransitionsMachine:
    """轻量包装 transitions Machine，统一公共接口。"""

    def __init__(self, model: Any, *, states: list[str], transitions: list[dict[str, Any]]):
        self.model = model
        self.machine = Machine(
            model=model,
            states=states,
            transitions=transitions,
            initial=model.state,
            send_event=False,
            auto_transitions=False,
            ordered_transitions=False,
        )

    def may_trigger(self, transition: str | None) -> bool:
        if not isinstance(transition, str) or not transition:
            return True
        return self.model.may_trigger(transition)

    def trigger(self, transition: str, **kwargs: Any) -> bool:
        return self.model.trigger(transition, **kwargs)

    def get_valid_transitions(self) -> list[str]:
        return self.machine.get_triggers(self.model.state)


class SmtClassifierStageStatus(str, Enum):
    """插件运行时 stage 枚举。"""

    IDLE = "IDLE"
    WAITING_INSPECTION = "WAITING_INSPECTION"
    WAITING_PICK_PLACE = "WAITING_PICK_PLACE"
    WAITING_CONVEYOR = "WAITING_CONVEYOR"
    WAITING_AGV_DELIVERY = "WAITING_AGV_DELIVERY"
    WAITING_OUTPUT = "WAITING_OUTPUT"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


STAGE_STATES: list[str] = [status.value for status in SmtClassifierStageStatus]

STAGE_TRANSITIONS: list[dict[str, Any]] = [
    {
        "trigger": "scan_ok",
        "source": SmtClassifierStageStatus.IDLE.value,
        "dest": SmtClassifierStageStatus.WAITING_INSPECTION.value,
    },
    {
        "trigger": "scan_ng",
        "source": SmtClassifierStageStatus.IDLE.value,
        "dest": SmtClassifierStageStatus.WAITING_PICK_PLACE.value,
    },
    {
        "trigger": "inspection_ok",
        "source": SmtClassifierStageStatus.WAITING_INSPECTION.value,
        "dest": SmtClassifierStageStatus.WAITING_CONVEYOR.value,
    },
    {
        "trigger": "inspection_ng",
        "source": SmtClassifierStageStatus.WAITING_INSPECTION.value,
        "dest": SmtClassifierStageStatus.WAITING_PICK_PLACE.value,
    },
    {
        "trigger": "conveyor_complete",
        "source": SmtClassifierStageStatus.WAITING_CONVEYOR.value,
        "dest": SmtClassifierStageStatus.WAITING_OUTPUT.value,
    },
    {
        "trigger": "agv_requested",
        "source": SmtClassifierStageStatus.WAITING_CONVEYOR.value,
        "dest": SmtClassifierStageStatus.WAITING_AGV_DELIVERY.value,
    },
    {
        "trigger": "agv_completed",
        "source": SmtClassifierStageStatus.WAITING_AGV_DELIVERY.value,
        "dest": SmtClassifierStageStatus.WAITING_OUTPUT.value,
    },
    {
        "trigger": "pick_place_ok",
        "source": SmtClassifierStageStatus.WAITING_PICK_PLACE.value,
        "dest": SmtClassifierStageStatus.WAITING_INSPECTION.value,
    },
    {
        "trigger": "ng_handled",
        "source": SmtClassifierStageStatus.WAITING_PICK_PLACE.value,
        "dest": SmtClassifierStageStatus.COMPLETED.value,
    },
    {
        "trigger": "output_handled",
        "source": SmtClassifierStageStatus.WAITING_OUTPUT.value,
        "dest": SmtClassifierStageStatus.COMPLETED.value,
    },
    {"trigger": "command_failed", "source": "*", "dest": SmtClassifierStageStatus.ERROR.value},
    {"trigger": "timeout", "source": "*", "dest": SmtClassifierStageStatus.ERROR.value},
    {"trigger": "estop", "source": "*", "dest": SmtClassifierStageStatus.ERROR.value},
    {"trigger": "manual_hold", "source": "*", "dest": "="},
    {"trigger": "manual_resume", "source": "*", "dest": "="},
    {"trigger": "manual_cancel", "source": "*", "dest": SmtClassifierStageStatus.COMPLETED.value},
    {"trigger": "wcs_task_complete", "source": "*", "dest": "="},
    {"trigger": "wcs_task_failed", "source": "*", "dest": SmtClassifierStageStatus.ERROR.value},
]


class SmtClassifierStageMachine(_BaseTransitionsMachine):
    """供 workline runtime 使用的 stage 状态机。"""

    def __init__(self, model: Any):
        super().__init__(model, states=STAGE_STATES, transitions=STAGE_TRANSITIONS)


def get_valid_stage_transitions(current_stage: str) -> list[str]:
    """获取插件 stage 状态机在指定状态下的可用迁移。"""

    class _TempModel:
        def __init__(self, state: str):
            self.state = state

    return SmtClassifierStageMachine(_TempModel(current_stage)).get_valid_transitions()


__all__ = [
    "STAGE_STATES",
    "STAGE_TRANSITIONS",
    "SmtClassifierStageMachine",
    "SmtClassifierStageStatus",
    "get_valid_stage_transitions",
]
