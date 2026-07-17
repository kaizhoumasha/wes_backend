"""粗分机 13-case 纯决策 handler；副作用由 Task 7 统一转换执行。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from src.app.runtime.capabilities.material_flow.contracts.rough_sorter import (
    ACTION_MOVE_FORWARD,
    ACTION_MOVE_TO_NG,
    ACTION_PICK_AND_PUT,
    REASON_COMMAND_RESULT_CORRELATION_MISMATCH,
    REASON_IDEMPOTENCY_CONFLICT,
    REASON_ROUGH_SORTER_CONTEXT_MISSING,
    REASON_ROUGH_SORTER_MEASUREMENT_INVALID,
    REASON_ROUGH_SORTER_PICK_RESULT_TIMEOUT,
    REASON_WMS_TIMEOUT,
    build_move_forward_payload,
    build_move_to_ng_payload,
    build_pick_and_put_payload,
    normalize_six_in_one_payload,
)
from src.app.runtime.orchestration.runtime_intent import BlockScope, RuntimeIntent
from src.app.runtime.system_capabilities.outcomes import BusinessReject, ContractViolation, RetryableFailure, Success
from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.contracts import (
    RoughSorterBindingSnapshot,
    RoughSorterInventoryAdmissionInput,
)
from src.app.runtime.workline_plugins.contracts import PluginContext, PluginDecision

from .inputs import (
    BusinessTimeoutInput,
    PickAndPutResultInput,
    ReplayRequestInput,
    RoughSorterInput,
    ScanCompletedInput,
)
from .state import RoughSorterState

if TYPE_CHECKING:
    from src.app.runtime.workline_plugins.dispatcher import AttemptSystemCapabilityGateway

    from .config import RoughSorterConfig

WMS_QUERY_IDENTITY = ("wms.rough_sorter_inventory_admission", "v1")
MATERIAL_EFFECT = "material_flow.material_unit_write@v1"
DEVICE_EFFECT = "device.device_command_write@v1"
HOLD_EFFECT = "runtime.session_hold@v1"
REASON_PHASE_MISMATCH = "ROUGH_SORTER_PHASE_MISMATCH"
REASON_QUERY_CONTRACT_INVALID = "ROUGH_SORTER_QUERY_CONTRACT_INVALID"
BusinessKey = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
QueryCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
SourceLocation = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class RoughSorterFacts(BaseModel):
    """单次 attempt 注入的权威事实摘要，不进入 PluginState。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    business_key: BusinessKey | None = None
    hhpn: QueryCode | None = None
    lot_code: QueryCode | None = None
    source_location: SourceLocation = "SCAN_POINT"
    correlation_matches: bool = True
    replay_digest_matches: bool | None = None
    binding_snapshot: RoughSorterBindingSnapshot


class RuntimeReconciliationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command_code: str
    reason_code: str


class RoughSorterDecision(PluginDecision[RoughSorterState]):
    """Task 6 决策元数据；Task 7 将 capability identity 转成通用 intent kind。"""

    reason_code: str | None = None
    capability_identities: tuple[str, ...] = ()
    reconciliation_request: RuntimeReconciliationRequest | None = None
    evidence_only: bool = False
    zero_new_effect: bool = False


async def decide(  # noqa: PLR0911 - route/state/correlation fail-closed 分支逐项对应稳定合同。
    logical_input: RoughSorterInput,
    *,
    state: RoughSorterState,
    config: RoughSorterConfig,
    facts: RoughSorterFacts,
    gateway: AttemptSystemCapabilityGateway,
    context: PluginContext[RoughSorterState] | None = None,
    replay: bool = False,
) -> RoughSorterDecision:
    """只读取 typed input/state/facts/QUERY outcome 并返回 typed decision。"""

    _ = context
    if replay:
        return _decision("REPLAY_ACCEPTED_NOOP", state, zero_new_effect=True, evidence_only=True)
    if isinstance(logical_input, ReplayRequestInput):
        if facts.replay_digest_matches is not False:
            return _decision("REPLAY_ACCEPTED_NOOP", state, zero_new_effect=True, evidence_only=True)
        return _hold(state, scope=BlockScope.MATERIAL, reason_code=REASON_IDEMPOTENCY_CONFLICT)
    if isinstance(logical_input, ScanCompletedInput):
        if state.phase != "READY":
            return _evidence_only(state, reason_code=REASON_PHASE_MISMATCH)
        return _scan_decision(logical_input, state=state, config=config, facts=facts)
    if isinstance(logical_input, BusinessTimeoutInput):
        if state.phase != "PICK_TO_PIPELINE":
            return _evidence_only(state, reason_code=REASON_PHASE_MISMATCH)
        if state.current_correlation != logical_input.command_code or not facts.correlation_matches:
            return _evidence_only(state, reason_code=REASON_COMMAND_RESULT_CORRELATION_MISMATCH)
        return _decision(
            "HOLD",
            state,
            reason_code=REASON_ROUGH_SORTER_PICK_RESULT_TIMEOUT,
            reconciliation_request=RuntimeReconciliationRequest(
                command_code=logical_input.command_code,
                reason_code=REASON_ROUGH_SORTER_PICK_RESULT_TIMEOUT,
            ),
        )
    if state.phase != "PICK_TO_PIPELINE":
        return _evidence_only(state, reason_code=REASON_PHASE_MISMATCH)
    if state.current_correlation != logical_input.command_code or not facts.correlation_matches:
        return _evidence_only(state, reason_code=REASON_COMMAND_RESULT_CORRELATION_MISMATCH)
    return await _pick_result_decision(logical_input, state=state, config=config, facts=facts, gateway=gateway)


def _scan_decision(
    logical_input: ScanCompletedInput,
    *,
    state: RoughSorterState,
    config: RoughSorterConfig,
    facts: RoughSorterFacts,
) -> RoughSorterDecision:
    six_in_one = normalize_six_in_one_payload(logical_input.payload)
    business_key = six_in_one.business_key or facts.business_key
    if not six_in_one.PkgID or not business_key:
        return _hold(state, scope=BlockScope.MATERIAL, reason_code=REASON_ROUGH_SORTER_CONTEXT_MISSING)
    six = six_in_one.model_dump(exclude_none=True)
    create = RuntimeIntent.create_material_unit(
        pkg_code=six_in_one.PkgID,
        material_identity_key=business_key,
        six_in_one=six,
        current_location=facts.source_location,
    )
    if any(token in six_in_one.PkgID.upper() for token in ("SIZENG", "THICKNESSNG")):
        next_state = state.model_copy(update={"phase": "NG_MOVING"})
        intents = (
            create,
            RuntimeIntent.update_context({"phase": "NG_MOVING"}),
            RuntimeIntent.mark_ng(reason_code="SCAN_NG_BY_RULE", message="扫码规则判定 NG"),
            RuntimeIntent.command(
                device_role=config.device_roles.input_arm,
                action=ACTION_MOVE_TO_NG,
                payload=build_move_to_ng_payload(
                    business_key=business_key,
                    source_location=facts.source_location,
                    ng_location=config.ng_location,
                    reason_code="SCAN_NG_BY_RULE",
                ),
            ),
        )
        return _decision("MOVE_TO_NG_PERSISTED", next_state, intents, reason_code="SCAN_NG_BY_RULE")
    next_state = state.model_copy(update={"phase": "PICK_TO_PIPELINE"})
    intents = (
        create,
        RuntimeIntent.update_context({"phase": "PICK_TO_PIPELINE"}),
        RuntimeIntent.command(
            device_role=config.device_roles.input_arm,
            action=ACTION_PICK_AND_PUT,
            payload=build_pick_and_put_payload(
                business_key=business_key,
                source_location=facts.source_location,
                target_location=config.pipeline_input_location,
                six_in_one=six_in_one,
            ),
        ),
    )
    return _decision("PICK_AND_PUT_PERSISTED", next_state, intents)


async def _pick_result_decision(  # noqa: PLR0911 - 封闭 outcome 分支逐项对应 approved cases。
    logical_input: PickAndPutResultInput,
    *,
    state: RoughSorterState,
    config: RoughSorterConfig,
    facts: RoughSorterFacts,
    gateway: AttemptSystemCapabilityGateway,
) -> RoughSorterDecision:
    if logical_input.result.value in {"FAILED", "ERROR"}:
        reason = str(logical_input.error_detail.get("error_code") or "DEVICE_COMMAND_FAILED")
        return _hold(
            state, scope=BlockScope.COMMAND, reason_code=reason, payload={"error_detail": logical_input.error_detail}
        )
    measurement = _measurement(logical_input.data)
    if measurement is None:
        return _hold(state, scope=BlockScope.MATERIAL, reason_code=REASON_ROUGH_SORTER_MEASUREMENT_INVALID)
    diameter, thickness = measurement
    measurement_result = str(logical_input.data.get("measurement_result") or "").upper()
    if measurement_result == "NG":
        return _move_to_ng(state, config=config, facts=facts, reason_code="MEASUREMENT_NG")
    if not facts.business_key or not facts.hhpn or not facts.lot_code:
        return _hold(state, scope=BlockScope.MATERIAL, reason_code=REASON_ROUGH_SORTER_CONTEXT_MISSING)
    try:
        query = RoughSorterInventoryAdmissionInput(
            business_key=facts.business_key,
            hhpn=facts.hhpn,
            lot_code=facts.lot_code,
            warehouse_code=config.warehouse_code,
            owner_code=config.owner_code,
            diameter_mm=diameter,
            thickness_mm=thickness,
            binding_snapshot=facts.binding_snapshot,
        )
    except ValidationError:
        return _hold(state, scope=BlockScope.MATERIAL, reason_code=REASON_QUERY_CONTRACT_INVALID)
    query_result = await gateway.execute(*WMS_QUERY_IDENTITY, query)
    evidence_ref = _evidence_reference(query_result.evidence)
    outcome = query_result.outcome
    if isinstance(outcome, Success) and getattr(outcome.payload, "accepted", False):
        next_state = state.model_copy(
            update={
                "phase": "MOVING_FORWARD",
                "measurement_evidence_ref": f"measurement:{logical_input.command_code}",
                "wms_evidence_ref": evidence_ref,
            }
        )
        intents = (
            RuntimeIntent.update_context({"phase": "MOVING_FORWARD"}),
            RuntimeIntent.command(
                device_role=config.device_roles.conveyor,
                action=ACTION_MOVE_FORWARD,
                payload=build_move_forward_payload(
                    business_key=facts.business_key,
                    source_location=config.pipeline_input_location,
                    target_location=config.pipeline_output_location,
                ),
            ),
        )
        return _decision("MOVE_FORWARD_PERSISTED", next_state, intents)
    if isinstance(outcome, BusinessReject):
        return _move_to_ng(state, config=config, facts=facts, reason_code="WMS_REJECTED", wms_ref=evidence_ref)
    if isinstance(outcome, RetryableFailure) and outcome.error_code == "TIMEOUT":
        return _hold(state, scope=BlockScope.MATERIAL, reason_code=REASON_WMS_TIMEOUT)
    if isinstance(outcome, ContractViolation):
        return _hold(state, scope=BlockScope.MATERIAL, reason_code=outcome.error_code)
    return _hold(state, scope=BlockScope.MATERIAL, reason_code=REASON_WMS_TIMEOUT)


def _measurement(data: dict[str, Any]) -> tuple[Decimal, Decimal] | None:
    try:
        diameter = Decimal(str(data["reel_diameter"]))
        thickness = Decimal(str(data["reel_thickness"]))
    except (InvalidOperation, KeyError, TypeError, ValueError):
        return None
    if not diameter.is_finite() or not thickness.is_finite() or diameter <= 0 or thickness <= 0:
        return None
    return diameter, thickness


def _move_to_ng(
    state: RoughSorterState,
    *,
    config: RoughSorterConfig,
    facts: RoughSorterFacts,
    reason_code: str,
    wms_ref: str | None = None,
) -> RoughSorterDecision:
    next_state = state.model_copy(update={"phase": "NG_MOVING", "wms_evidence_ref": wms_ref})
    intents = (
        RuntimeIntent.update_context({"phase": "NG_MOVING"}),
        RuntimeIntent.mark_ng(reason_code=reason_code, message="粗分机业务判定 NG"),
        RuntimeIntent.command(
            device_role=config.device_roles.input_arm,
            action=ACTION_MOVE_TO_NG,
            payload=build_move_to_ng_payload(
                business_key=facts.business_key or "UNKNOWN",
                source_location=config.pipeline_input_location,
                ng_location=config.ng_location,
                reason_code=reason_code,
            ),
        ),
    )
    return _decision("MOVE_TO_NG_PERSISTED", next_state, intents, reason_code=reason_code)


def _hold(
    state: RoughSorterState,
    *,
    scope: BlockScope,
    reason_code: str,
    payload: dict[str, Any] | None = None,
) -> RoughSorterDecision:
    return _decision(
        "HOLD",
        state,
        (
            RuntimeIntent.block(
                scope=scope, reason_code=reason_code, message="粗分机决策进入人工 Hold", payload=payload
            ),
        ),
        reason_code=reason_code,
    )


def _evidence_only(state: RoughSorterState, *, reason_code: str) -> RoughSorterDecision:
    return _decision(
        "ARCHIVED_EVIDENCE",
        state,
        reason_code=reason_code,
        evidence_only=True,
        zero_new_effect=True,
    )


def _decision(
    outcome_code: str,
    next_state: RoughSorterState,
    intents: tuple[RuntimeIntent, ...] = (),
    **metadata: Any,
) -> RoughSorterDecision:
    capability_identities: list[str] = []
    for intent in intents:
        if intent.kind.value in {"CREATE_MATERIAL_UNIT", "UPDATE_CONTEXT", "MARK_NG"}:
            intent.source_system = MATERIAL_EFFECT
        elif intent.kind.value == "COMMAND":
            intent.source_system = DEVICE_EFFECT
        elif intent.kind.value == "BLOCK":
            intent.source_system = HOLD_EFFECT
        if intent.source_system and intent.source_system not in capability_identities:
            capability_identities.append(intent.source_system)
    return RoughSorterDecision(
        intents=intents,
        next_state=next_state,
        outcome_code=outcome_code,
        capability_identities=tuple(capability_identities),
        **metadata,
    )


def _evidence_reference(evidence: Any) -> str | None:
    reference = getattr(evidence, "reference", None)
    if isinstance(reference, str) and reference:
        return reference
    input_hash = getattr(evidence, "input_hash", None)
    return f"query:{input_hash}" if isinstance(input_hash, str) and input_hash else None


__all__ = [
    "WMS_QUERY_IDENTITY",
    "RoughSorterDecision",
    "RoughSorterFacts",
    "RuntimeReconciliationRequest",
    "decide",
]
