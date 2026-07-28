"""SMT 分拣入库的 pure facts builder 与 pure decisions。"""

from __future__ import annotations

from hashlib import sha256

from src.app.runtime.orchestration.runtime_intent import BlockScope, RuntimeIntent
from src.app.runtime.workline_plugins.contracts import CapabilityEffectResultInput, CommandResultInput, PluginDecision
from src.app.runtime.workline_plugins.dispatcher import PluginAttemptFactSource  # noqa: TC001

from .contracts import SmtSortingInboundConfig, SmtSortingInboundFacts, SmtSortingInboundState, SourcePickRequestInput


def build_facts(source: PluginAttemptFactSource) -> SmtSortingInboundFacts:
    source_arm_facts = tuple(fact for fact in source.device_fact_versions if fact[0] == "SORTING_SOURCE_ARM")
    route = source.raw_input.get("route")
    if route == "SOURCE_PICK_REQUESTED" and len(source_arm_facts) != 1:
        raise ValueError("SMT source-pick requires exactly one pinned SORTING_SOURCE_ARM")
    source_arm_device_id: int | None = None
    source_arm_device_version: int | None = None
    if len(source_arm_facts) == 1:
        _, source_arm_device_id, source_arm_device_version = source_arm_facts[0]
    return SmtSortingInboundFacts(
        correlation_matches=source.correlation_matches,
        route_diagnostic=source.route_diagnostic,
        binding_snapshot=source.snapshot,
        source_arm_device_id=source_arm_device_id,
        source_arm_device_version=source_arm_device_version,
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
        if facts.source_arm_device_id is None or facts.source_arm_device_version is None:
            raise ValueError("SMT source-pick pinned device fact is missing")
        command_fact = sha256(
            (
                f"{facts.binding_snapshot.binding_identity}:"
                f"{logical_input.source_pick_request_event_id}:"
                f"{logical_input.claim_attempt_no}"
            ).encode()
        ).hexdigest()[:32]
        command_code = f"SC-{command_fact.upper()}"
        return _decision(
            "SOURCE_PICK_REQUESTED",
            state,
            RuntimeIntent.system_capability(
                capability_key="material_flow.smt_source_pick_command",
                contract_version="v1",
                operation_key=f"smt-source-pick:{command_fact}:command",
                dispatch_key=f"device-command:{command_code}",
                payload={
                    "target_device_id": facts.source_arm_device_id,
                    "action": "SORTING_SOURCE_PICK",
                    "payload": {
                        "handoff_demand_id": logical_input.handoff_demand_id,
                        "handoff_source_item_id": logical_input.handoff_source_item_id,
                        "claim_attempt_no": logical_input.claim_attempt_no,
                        "source_pick_request_event_id": logical_input.source_pick_request_event_id,
                    },
                    "priority": 5,
                    "timeout_ms": 30000,
                    "command_code": command_code,
                    "result_policy": "COMMAND_RESULT",
                },
                precondition={"expected_available": True},
                fact_version=f"device:v{facts.source_arm_device_version}",
                timeout_seconds=5,
                creator_authority="WORKLINE_PLUGIN",
                authorization_policy="PLUGIN_DECLARED_CAPABILITY",
                binding_snapshot={
                    "binding_id": facts.binding_snapshot.binding_id,
                    "binding_version": facts.binding_snapshot.binding_version,
                },
                provider_snapshot={"provider_code": "RUNTIME", "profile": "runtime"},
            ),
        )
    if not facts.correlation_matches or state.current_correlation != logical_input.command_code:
        return _decision("COMMAND_RESULT_CORRELATION_MISMATCH", state)
    if logical_input.command_type != "SORTING_SOURCE_PICK":
        return _decision("COMMAND_TASK_TYPE_UNSUPPORTED", state)
    if logical_input.result.value == "SUCCESS":
        command_fact = sha256(logical_input.command_code.encode()).hexdigest()[:32]
        ledger_effect = RuntimeIntent.system_capability(
            capability_key="material_flow.smt_source_pick_ledger",
            contract_version="v1",
            operation_key=f"smt-source-pick:{command_fact}:picked",
            dispatch_key=f"system-capability:material_flow.smt_source_pick_ledger:{command_fact}",
            payload={
                "operation": "RECORD_PICKED",
                "command_code": logical_input.command_code,
            },
            precondition={"expected_status": "CLAIMED_BY_SORTING"},
            fact_version=f"command:{command_fact}:SUCCESS",
            timeout_seconds=5,
            creator_authority="WORKLINE_PLUGIN",
            authorization_policy="PLUGIN_DECLARED_CAPABILITY",
            binding_snapshot={
                "binding_id": facts.binding_snapshot.binding_id,
                "binding_version": facts.binding_snapshot.binding_version,
            },
            provider_snapshot={"provider_code": "RUNTIME", "profile": "runtime"},
        )
        return _decision(
            "SOURCE_PICK_COMPLETED",
            state.model_copy(update={"phase": "WAITING_SCAN", "current_correlation": None}),
            ledger_effect,
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
