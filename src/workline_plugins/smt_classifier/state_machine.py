"""SMT 粗分机显式状态机。"""

from __future__ import annotations

from typing import Any, ClassVar

from transitions import Machine


class SmtClassifierState:
    """SMT 粗分机插件状态。"""

    IDLE = "IDLE"
    WAITING_MEASUREMENT = "WAITING_MEASUREMENT"
    WAITING_CONVEYOR = "WAITING_CONVEYOR"
    WAITING_OUTPUT = "WAITING_OUTPUT"
    WAITING_PICK_PLACE = "WAITING_PICK_PLACE"
    MANUAL_HOLD = "MANUAL_HOLD"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"

    ALL: ClassVar[tuple[str, ...]] = (
        IDLE,
        WAITING_MEASUREMENT,
        WAITING_CONVEYOR,
        WAITING_OUTPUT,
        WAITING_PICK_PLACE,
        MANUAL_HOLD,
        COMPLETED,
        ERROR,
    )


class SmtClassifierStateMachine:
    """适配 runtime TransitionValidator 的 SMT 状态机。"""

    transitions: ClassVar[list[dict[str, Any]]] = [
        {"trigger": "scan_ok", "source": SmtClassifierState.IDLE, "dest": SmtClassifierState.WAITING_MEASUREMENT},
        {"trigger": "scan_ng", "source": SmtClassifierState.IDLE, "dest": SmtClassifierState.WAITING_PICK_PLACE},
        {
            "trigger": "measurement_ng",
            "source": SmtClassifierState.WAITING_MEASUREMENT,
            "dest": SmtClassifierState.ERROR,
        },
        {
            "trigger": "pick_ok",
            "source": (SmtClassifierState.WAITING_MEASUREMENT, SmtClassifierState.WAITING_PICK_PLACE),
            "dest": SmtClassifierState.WAITING_CONVEYOR,
        },
        {
            "trigger": "pick_ng",
            "source": SmtClassifierState.WAITING_PICK_PLACE,
            "dest": SmtClassifierState.COMPLETED,
        },
        {
            "trigger": "inspection_ng",
            "source": SmtClassifierState.WAITING_PICK_PLACE,
            "dest": SmtClassifierState.WAITING_PICK_PLACE,
        },
        {
            "trigger": "conveyor_ok",
            "source": SmtClassifierState.WAITING_CONVEYOR,
            "dest": SmtClassifierState.WAITING_OUTPUT,
        },
        {
            "trigger": "output_ok",
            "source": SmtClassifierState.WAITING_OUTPUT,
            "dest": SmtClassifierState.COMPLETED,
        },
        {
            "trigger": "manual_hold",
            "source": (SmtClassifierState.WAITING_PICK_PLACE, SmtClassifierState.WAITING_OUTPUT),
            "dest": SmtClassifierState.MANUAL_HOLD,
        },
        {
            "trigger": "timeout",
            "source": (
                SmtClassifierState.WAITING_MEASUREMENT,
                SmtClassifierState.WAITING_CONVEYOR,
                SmtClassifierState.WAITING_OUTPUT,
                SmtClassifierState.WAITING_PICK_PLACE,
            ),
            "dest": SmtClassifierState.ERROR,
        },
        {
            "trigger": "fail",
            "source": (
                SmtClassifierState.IDLE,
                SmtClassifierState.WAITING_MEASUREMENT,
                SmtClassifierState.WAITING_CONVEYOR,
                SmtClassifierState.WAITING_OUTPUT,
                SmtClassifierState.WAITING_PICK_PLACE,
                SmtClassifierState.MANUAL_HOLD,
            ),
            "dest": SmtClassifierState.ERROR,
        },
    ]

    def __init__(self, model: Any):
        self.model = model
        self.machine = Machine(
            model=model,
            states=SmtClassifierState.ALL,
            transitions=self.transitions,
            initial=getattr(model, "state", SmtClassifierState.IDLE) or SmtClassifierState.IDLE,
            auto_transitions=False,
        )

    def may_trigger(self, transition: str | None) -> bool:
        """供 runtime TransitionValidator 校验触发器。"""

        if not transition:
            return True
        return bool(self.model.may_trigger(transition))


__all__ = ["SmtClassifierState", "SmtClassifierStateMachine"]
