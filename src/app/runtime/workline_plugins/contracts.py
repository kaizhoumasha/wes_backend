"""Workline 插件决策最小合同。"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from src.app.runtime.orchestration.runtime_intent import RuntimeIntent  # noqa: TC001

MAX_PLUGIN_DECISION_INTENTS = 32
OutcomeCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


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


__all__ = ["MAX_PLUGIN_DECISION_INTENTS", "PluginContext", "PluginDecision"]
