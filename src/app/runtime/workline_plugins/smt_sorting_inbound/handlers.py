"""SMT 分拣入库的 pure facts builder 与 pure decisions。"""

from __future__ import annotations

from src.app.runtime.orchestration.runtime_intent import BlockScope, RuntimeIntent
from src.app.runtime.workline_plugins.contracts import CapabilityEffectResultInput, CommandResultInput, PluginDecision
from src.app.runtime.workline_plugins.dispatcher import PluginAttemptFactSource  # noqa: TC001

from .contracts import SmtSortingInboundConfig, SmtSortingInboundFacts, SmtSortingInboundState, SourcePickRequestInput


def build_facts(source: PluginAttemptFactSource) -> SmtSortingInboundFacts:
    return SmtSortingInboundFacts(
        correlation_matches=source.correlation_matches,
        route_diagnostic=source.route_diagnostic,
        binding_snapshot=source.snapshot,
    )


async def decide(
    logical_input: SourcePickRequestInput | CommandResultInput | CapabilityEffectResultInput,
    *,
    state: SmtSortingInboundState,
    config: SmtSortingInboundConfig,
    facts: SmtSortingInboundFacts,
    gateway: object,
    **_kwargs: object,
) -> PluginDecision[SmtSortingInboundState]:
    """只根据 route input/facts 返回 SMT 的确定性下一步。"""

    _ = gateway
    if facts.route_diagnostic is not None:
        return _decision(facts.route_diagnostic, state)
    if isinstance(logical_input, CapabilityEffectResultInput):
        return _decision("CAPABILITY_REJECTED", state)
    if isinstance(logical_input, SourcePickRequestInput):
        if state.phase != "WAITING_SOURCE_PICK" or state.current_correlation is not None:
            return _decision("SOURCE_PICK_REQUEST_IGNORED", state)
        return _decision(
            "SOURCE_PICK_REQUESTED",
            state,
            RuntimeIntent.command(
                device_role=config.source_arm_role,
                action="SORTING_SOURCE_PICK",
                payload={
                    "handoff_demand_id": logical_input.handoff_demand_id,
                    "handoff_source_item_id": logical_input.handoff_source_item_id,
                    "claim_attempt_no": logical_input.claim_attempt_no,
                    "source_pick_inbox_id": logical_input.source_pick_inbox_id,
                    "source_pick_request_event_id": logical_input.source_pick_request_event_id,
                },
                result_policy="COMMAND_RESULT",
            ),
        )
    if not facts.correlation_matches or state.current_correlation != logical_input.command_code:
        return _decision("COMMAND_RESULT_CORRELATION_MISMATCH", state)
    if logical_input.command_type != "SORTING_SOURCE_PICK":
        return _decision("COMMAND_TASK_TYPE_UNSUPPORTED", state)
    if logical_input.result.value == "SUCCESS":
        return _decision(
            "SOURCE_PICK_COMPLETED",
            state.model_copy(update={"phase": "WAITING_SCAN", "current_correlation": None}),
            RuntimeIntent.continue_next(action="SOURCE_PICK_COMPLETED"),
        )
    return _decision(
        "SOURCE_PICK_FAILED",
        state,
        RuntimeIntent.block(
            scope=BlockScope.COMMAND,
            reason_code="SOURCE_PICK_FAILED",
            message="SMT 来源抓取命令失败",
        ),
    )


def _decision(
    outcome_code: str,
    state: SmtSortingInboundState,
    *intents: RuntimeIntent,
) -> PluginDecision[SmtSortingInboundState]:
    return PluginDecision(intents=intents, next_state=state, outcome_code=outcome_code)


__all__ = ["build_facts", "decide"]
