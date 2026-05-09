"""入库料箱称重复核插件状态机。"""

from __future__ import annotations

from typing import Any, ClassVar

from transitions import Machine


class InboundToteQcState:
    """入库料箱复核插件状态。"""

    IDLE = "IDLE"
    WAITING_WEIGH = "WAITING_WEIGH"
    WAITING_DIVERT = "WAITING_DIVERT"

    ALL: ClassVar[tuple[str, ...]] = (IDLE, WAITING_WEIGH, WAITING_DIVERT)


class InboundToteQcStateMachine:
    """适配 runtime TransitionValidator 的入库料箱复核状态机。"""

    transitions: ClassVar[list[dict[str, Any]]] = [
        {"trigger": "tote_arrived", "source": InboundToteQcState.IDLE, "dest": InboundToteQcState.WAITING_WEIGH},
        {
            "trigger": "weight_ok",
            "source": InboundToteQcState.WAITING_WEIGH,
            "dest": InboundToteQcState.WAITING_DIVERT,
        },
        {
            "trigger": "weight_ng",
            "source": InboundToteQcState.WAITING_WEIGH,
            "dest": InboundToteQcState.WAITING_DIVERT,
        },
        {
            "trigger": "divert_ok",
            "source": InboundToteQcState.WAITING_DIVERT,
            "dest": None,
        },
        {
            "trigger": "manual_hold",
            "source": (InboundToteQcState.WAITING_WEIGH, InboundToteQcState.WAITING_DIVERT),
            "dest": None,
        },
        {
            "trigger": "fail",
            "source": (
                InboundToteQcState.IDLE,
                InboundToteQcState.WAITING_WEIGH,
                InboundToteQcState.WAITING_DIVERT,
            ),
            "dest": None,
        },
    ]

    def __init__(self, model: Any):
        self.model = model
        self.machine = Machine(
            model=model,
            states=InboundToteQcState.ALL,
            transitions=self.transitions,
            initial=getattr(model, "state", InboundToteQcState.IDLE) or InboundToteQcState.IDLE,
            auto_transitions=False,
        )

    def may_trigger(self, transition: str | None) -> bool:
        if not transition:
            return True
        return bool(self.model.may_trigger(transition))


__all__ = ["InboundToteQcState", "InboundToteQcStateMachine"]
