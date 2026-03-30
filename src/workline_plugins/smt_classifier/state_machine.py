"""
SMT 粗分机状态机定义

这里同时维护两套契约：
1. `SmtClassifierStateMachine` / `SmtClassifierStatus`
   保留既有公开 API，供历史调用方与旧测试使用。
2. `SmtClassifierStageMachine` / `SmtClassifierStageStatus`
   供新的 workline runtime 校验插件内部 stage 迁移。
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


class SmtClassifierStatus(str, Enum):
    """历史 SMT 状态机枚举，保留公开导出契约。"""

    NEW = "NEW"
    RUNNING = "RUNNING"
    WAITING_SCAN_RESULT = "WAITING_SCAN_RESULT"
    WAITING_DETECT_RESULT = "WAITING_DETECT_RESULT"
    WAITING_MOVE_RESULT = "WAITING_MOVE_RESULT"
    WAITING_PUT_RESULT = "WAITING_PUT_RESULT"
    MANUAL_HOLD = "MANUAL_HOLD"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


STATES: list[str] = [status.value for status in SmtClassifierStatus]

TRANSITIONS: list[dict[str, Any]] = [
    {"trigger": "start", "source": SmtClassifierStatus.NEW.value, "dest": SmtClassifierStatus.RUNNING.value},
    {
        "trigger": "wait_scan",
        "source": SmtClassifierStatus.RUNNING.value,
        "dest": SmtClassifierStatus.WAITING_SCAN_RESULT.value,
    },
    {
        "trigger": "wait_detect",
        "source": SmtClassifierStatus.RUNNING.value,
        "dest": SmtClassifierStatus.WAITING_DETECT_RESULT.value,
    },
    {
        "trigger": "wait_move",
        "source": SmtClassifierStatus.RUNNING.value,
        "dest": SmtClassifierStatus.WAITING_MOVE_RESULT.value,
    },
    {
        "trigger": "wait_put",
        "source": SmtClassifierStatus.RUNNING.value,
        "dest": SmtClassifierStatus.WAITING_PUT_RESULT.value,
    },
    {
        "trigger": "scan_ok",
        "source": SmtClassifierStatus.WAITING_SCAN_RESULT.value,
        "dest": SmtClassifierStatus.RUNNING.value,
    },
    {
        "trigger": "scan_ng",
        "source": SmtClassifierStatus.WAITING_SCAN_RESULT.value,
        "dest": SmtClassifierStatus.FAILED.value,
    },
    {
        "trigger": "detect_ok",
        "source": SmtClassifierStatus.WAITING_DETECT_RESULT.value,
        "dest": SmtClassifierStatus.RUNNING.value,
    },
    {
        "trigger": "detect_ng",
        "source": SmtClassifierStatus.WAITING_DETECT_RESULT.value,
        "dest": SmtClassifierStatus.RUNNING.value,
    },
    {
        "trigger": "move_ok",
        "source": SmtClassifierStatus.WAITING_MOVE_RESULT.value,
        "dest": SmtClassifierStatus.RUNNING.value,
    },
    {
        "trigger": "put_ok",
        "source": SmtClassifierStatus.WAITING_PUT_RESULT.value,
        "dest": SmtClassifierStatus.COMPLETED.value,
    },
    {"trigger": "retry", "source": SmtClassifierStatus.MANUAL_HOLD.value, "dest": SmtClassifierStatus.RUNNING.value},
    {
        "trigger": "cancel",
        "source": SmtClassifierStatus.RUNNING.value,
        "dest": SmtClassifierStatus.CANCELLED.value,
    },
    {
        "trigger": "complete",
        "source": SmtClassifierStatus.RUNNING.value,
        "dest": SmtClassifierStatus.COMPLETED.value,
    },
    {"trigger": "failed", "source": "*", "dest": SmtClassifierStatus.FAILED.value},
    {"trigger": "fail", "source": "*", "dest": SmtClassifierStatus.FAILED.value},
    {"trigger": "estop", "source": "*", "dest": SmtClassifierStatus.MANUAL_HOLD.value},
]


class SmtClassifierStateMachine(_BaseTransitionsMachine):
    """历史 SMT 状态机实现。"""

    def __init__(self, model: Any):
        super().__init__(model, states=STATES, transitions=TRANSITIONS)


def get_valid_transitions(current_state: str) -> list[str]:
    """获取历史状态机在指定状态下的可用迁移。"""

    class _TempModel:
        def __init__(self, state: str):
            self.state = state

    return SmtClassifierStateMachine(_TempModel(current_state)).get_valid_transitions()


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
    "STATES",
    "TRANSITIONS",
    "SmtClassifierStageMachine",
    "SmtClassifierStageStatus",
    "SmtClassifierStateMachine",
    "SmtClassifierStatus",
    "get_valid_stage_transitions",
    "get_valid_transitions",
]
