"""粗分机扫码到准入决策窄闭环规格包测试。"""

import json
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.app.device.models.command import CommandCallbackResult, CommandResult, CommandStatus
from src.app.device.services.device_command_service import DeviceCommandService
from src.app.runtime.capabilities.material_flow.contracts.rough_sorter import normalize_six_in_one_payload
from src.app.runtime.orchestration.effect_result import WriteBackDisposition
from src.app.runtime.orchestration.models.material_unit import MaterialUnitStatus
from src.app.runtime.orchestration.models.runtime_hold import RuntimeHoldStatus
from src.app.runtime.orchestration.models.session import (
    RuntimeReconciliationReason,
    RuntimeReconciliationState,
    SessionStatus,
)
from src.app.runtime.orchestration.orchestrator_bridge import OrchestratorResult
from src.app.runtime.orchestration.runtime_intent import BlockScope, RuntimeIntent, RuntimeIntentKind
from src.app.runtime.orchestration.runtime_intent_effects import RuntimeIntentEffectApplier
from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
    WorklineRuntimeReconciliationService,
)
from src.app.runtime.orchestration.services.runtime_inbox import (
    RuntimeInboxCorrelationUnavailable,
    RuntimeInboxService,
    RuntimeInboxWriteBackService,
    WriteBackState,
)
from src.app.runtime.system_capabilities.outcomes import ContractViolation
from src.app.runtime.system_capabilities.wms.rough_sorter_inventory_admission.contracts import (
    RoughSorterBindingSnapshot,
)
from src.app.runtime.workline_plugins.contracts import PluginContext
from src.app.runtime.workline_plugins.rough_sorter.config import RoughSorterConfig
from src.app.runtime.workline_plugins.rough_sorter.definition import DEFINITION as ROUGH_SORTER_DEFINITION
from src.app.runtime.workline_plugins.rough_sorter.handlers import RoughSorterFacts, decide
from src.app.runtime.workline_plugins.rough_sorter.state import RoughSorterState
from src.utils.timezone import timezone

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = REPOSITORY_ROOT / "tests/fixtures/workline_contract/rough_sorter/scan_decision_cases.json"
SPEC_PATH = REPOSITORY_ROOT / "docs/business/rough_sorter_scan_decision_contract.md"
CHARACTERIZATION_PATH = (
    REPOSITORY_ROOT / "tests/characterization/workline_legacy/test_business_semantics_characterization.py"
)
UPSTREAM_DOCUMENT_CONTRACTS = {
    "docs/architecture/sorter-inbound-capability-spec.md": (
        "../business/rough_sorter_scan_decision_contract.md",
        "<!-- ownership: material-flow-architecture -->",
    ),
    "docs/business/rough_sorter_runtime_flow.md": (
        "./rough_sorter_scan_decision_contract.md",
        "<!-- ownership: end-to-end-device-protocol-examples -->",
    ),
    "docs/business/inbound_acceptance_steps.md": (
        "./rough_sorter_scan_decision_contract.md",
        "<!-- ownership: end-to-end-line-acceptance-steps -->",
    ),
    "docs/business/workline_business_data_event_flow_spec.md": (
        "./rough_sorter_scan_decision_contract.md",
        "<!-- ownership: cross-system-data-event-flow -->",
    ),
}

EXPECTED_CASE_OVERVIEW = {
    # trigger(event, discriminator), outcome, state(material, command, session, context_phase),
    # intents(kind, action, scope), implementation status, reason code
    "RS-SD-001": (
        ("SCAN_COMPLETED", (("barcode_decision", "OK"), ("pkg_id_condition", "PRESENT"))),
        "PICK_AND_PUT_PERSISTED",
        ("IN_TRANSIT", None, "WAITING_DEVICE_RESULT", "PICK_TO_PIPELINE"),
        (
            ("CREATE_MATERIAL_UNIT", None, None),
            ("UPDATE_CONTEXT", None, None),
            ("COMMAND", "PICK_AND_PUT", None),
        ),
        "covered",
        None,
    ),
    "RS-SD-002": (
        ("SCAN_COMPLETED", (("barcode_decision", "RULE_NG"), ("pkg_ng_rule", "SIZENG"))),
        "MOVE_TO_NG_PERSISTED",
        ("NG", None, "WAITING_DEVICE_RESULT", "NG_MOVING"),
        (
            ("CREATE_MATERIAL_UNIT", None, None),
            ("UPDATE_CONTEXT", None, None),
            ("MARK_NG", None, None),
            ("COMMAND", "MOVE_TO_NG", None),
        ),
        "covered",
        "SCAN_NG_BY_RULE",
    ),
    "RS-SD-003": (
        ("SCAN_COMPLETED", (("barcode_decision", "INCOMPLETE"), ("pkg_id_condition", "MISSING"))),
        "HOLD",
        ("NOT_CREATED", "NOT_CREATED", "MANUAL_HOLD", "UNCHANGED"),
        (("BLOCK", None, "MATERIAL"),),
        "covered",
        "ROUGH_SORTER_CONTEXT_MISSING",
    ),
    "RS-SD-004": (
        (
            "COMMAND_RESULT",
            (("command_result", "SUCCESS"), ("measurement", "OK"), ("wms_admission", "ADMIT")),
        ),
        "MOVE_FORWARD_PERSISTED",
        ("IN_TRANSIT", None, "WAITING_DEVICE_RESULT", "MOVING_FORWARD"),
        (("UPDATE_CONTEXT", None, None), ("COMMAND", "MOVE_FORWARD", None)),
        "covered",
        None,
    ),
    "RS-SD-005": (
        (
            "COMMAND_RESULT",
            (("command_result", "SUCCESS"), ("measurement", "NG"), ("wms_admission", "NOT_QUERIED")),
        ),
        "MOVE_TO_NG_PERSISTED",
        ("NG", None, "WAITING_DEVICE_RESULT", "NG_MOVING"),
        (("UPDATE_CONTEXT", None, None), ("MARK_NG", None, None), ("COMMAND", "MOVE_TO_NG", None)),
        "covered",
        "MEASUREMENT_NG",
    ),
    "RS-SD-006": (
        (
            "COMMAND_RESULT",
            (("command_result", "SUCCESS"), ("measurement", "OK"), ("wms_admission", "REJECT")),
        ),
        "MOVE_TO_NG_PERSISTED",
        ("NG", None, "WAITING_DEVICE_RESULT", "NG_MOVING"),
        (("UPDATE_CONTEXT", None, None), ("MARK_NG", None, None), ("COMMAND", "MOVE_TO_NG", None)),
        "covered",
        "WMS_REJECTED",
    ),
    "RS-SD-007": (
        (
            "COMMAND_RESULT",
            (
                ("command_result", "SUCCESS"),
                ("measurement_contract", "INVALID"),
                ("wms_admission", "NOT_QUERIED"),
            ),
        ),
        "HOLD",
        ("IN_TRANSIT", "COMPLETED", "MANUAL_HOLD", "PICK_TO_PIPELINE"),
        (("BLOCK", None, "MATERIAL"),),
        "covered",
        "ROUGH_SORTER_MEASUREMENT_INVALID",
    ),
    "RS-SD-008": (
        ("COMMAND_RESULT", (("command_result", "FAILURE"),)),
        "HOLD",
        ("IN_TRANSIT", "FAILED", "MANUAL_HOLD", "PICK_TO_PIPELINE"),
        (("BLOCK", None, "COMMAND"),),
        "covered",
        "DEVICE_BUSY",
    ),
    "RS-SD-009": (
        ("TIMER_TIMEOUT", (("command_result", "TIMEOUT"),)),
        "HOLD",
        ("IN_TRANSIT", "ACK_RECEIVED", "MANUAL_HOLD", "PICK_TO_PIPELINE"),
        (),
        "covered",
        "ROUGH_SORTER_PICK_RESULT_TIMEOUT",
    ),
    "RS-SD-010": (
        (
            "COMMAND_RESULT",
            (("command_result", "SUCCESS"), ("measurement", "OK"), ("wms_admission", "TIMEOUT")),
        ),
        "HOLD",
        ("IN_TRANSIT", "COMPLETED", "MANUAL_HOLD", "PICK_TO_PIPELINE"),
        (("BLOCK", None, "MATERIAL"),),
        "covered",
        "WMS_TIMEOUT",
    ),
    "RS-SD-011": (
        ("REPLAY_REQUEST", (("duplicate_digest", "SAME"),)),
        "REPLAY_ACCEPTED_NOOP",
        ("UNCHANGED", "UNCHANGED", "UNCHANGED", "UNCHANGED"),
        (),
        "covered",
        None,
    ),
    "RS-SD-012": (
        ("REPLAY_REQUEST", (("duplicate_digest", "DIFFERENT"),)),
        "HOLD",
        ("UNCHANGED", "UNCHANGED", "MANUAL_HOLD", "UNCHANGED"),
        (("BLOCK", None, "MATERIAL"),),
        "covered",
        "IDEMPOTENCY_CONFLICT",
    ),
    "RS-SD-013": (
        ("COMMAND_RESULT", (("correlation", "LATE_OR_UNKNOWN_MISMATCH"),)),
        "ARCHIVED_EVIDENCE",
        ("UNCHANGED", "UNCHANGED", "UNCHANGED", "UNCHANGED"),
        (),
        "covered",
        "COMMAND_RESULT_CORRELATION_MISMATCH",
    ),
}
REQUIRED_CASE_FIELDS = {
    "case_id",
    "trigger",
    "preconditions",
    "recorded_evidence",
    "expected_state",
    "expected_intents",
    "expected_outcome",
    "replay_expectation",
    "replay_policy",
    "source_refs",
    "implementation_status",
}
ALLOWED_TRIGGER_TYPES = {"SCAN_COMPLETED", "COMMAND_RESULT", "TIMER_TIMEOUT", "REPLAY_REQUEST"}
ALLOWED_STATE_VALUES = {
    "material": {status.value for status in MaterialUnitStatus} | {"NOT_CREATED", "UNCHANGED"},
    "context_phase": {"PICK_TO_PIPELINE", "NG_MOVING", "MOVING_FORWARD", "UNCHANGED"},
    "command": {status.value for status in CommandStatus} | {"NOT_CREATED", "UNCHANGED"},
    "session": {status.value for status in SessionStatus} | {"UNCHANGED"},
    "runtime_hold": {status.value for status in RuntimeHoldStatus} | {"NOT_CREATED", "UNCHANGED"},
}
ALLOWED_INTENT_KINDS = {"CREATE_MATERIAL_UNIT", "UPDATE_CONTEXT", "MARK_NG", "COMMAND", "BLOCK"}
ALLOWED_COMMAND_ACTIONS = {"PICK_AND_PUT", "MOVE_FORWARD", "MOVE_TO_NG"}
ALLOWED_BLOCK_SCOPES = {"MATERIAL", "COMMAND"}
ALLOWED_OUTCOMES = {
    "PICK_AND_PUT_PERSISTED",
    "MOVE_FORWARD_PERSISTED",
    "MOVE_TO_NG_PERSISTED",
    "HOLD",
    "REPLAY_ACCEPTED_NOOP",
    "ARCHIVED_EVIDENCE",
}
QUERY_REPLAY_CASES = {"RS-SD-004", "RS-SD-006", "RS-SD-010", "RS-SD-011"}
CURRENT_IMPLEMENTATION_STATUS = {
    "RS-SD-001": "covered",
    "RS-SD-002": "covered",
    "RS-SD-003": "covered",
    "RS-SD-004": "covered",
    "RS-SD-008": "covered",
    "RS-SD-009": "covered",
    "RS-SD-013": "covered",
}


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_typed_plugin_definition_covers_all_approved_logical_trigger_routes() -> None:
    """锁定业务触发与本地 EFFECT BusinessReject 回流的全部 typed route。"""

    assert (ROUGH_SORTER_DEFINITION.plugin_key, ROUGH_SORTER_DEFINITION.contract_version) == (
        "rough_sorter",
        "rough_sorter.v2",
    )
    assert set(ROUGH_SORTER_DEFINITION.routes) == {
        "SCAN_COMPLETED",
        "PICK_AND_PUT_RESULT",
        "BUSINESS_TIMEOUT",
        "REPLAY_REQUEST",
        "CAPABILITY_EFFECT_RESULT",
    }


def _case(case_id: str) -> dict[str, Any]:
    return next(case for case in _load_fixture()["cases"] if case["case_id"] == case_id)


def _normalize_trigger_signature(case: dict) -> tuple[str, tuple[tuple[str, str], ...]]:
    trigger = case["trigger"]
    return trigger["event_type"], tuple(sorted(trigger["decision_discriminator"].items()))


def _intent_signature(intents: list[RuntimeIntent]) -> tuple[tuple[str, str | None, str | None], ...]:
    return tuple(
        (
            intent.kind.value,
            intent.action,
            intent.block_scope.value if intent.block_scope is not None else None,
        )
        for intent in intents
    )


def _expected_intent_signature(case: dict[str, Any]) -> tuple[tuple[str, str | None, str | None], ...]:
    return tuple((intent["kind"], intent.get("action"), intent.get("scope")) for intent in case["expected_intents"])


@asynccontextmanager
async def _noop_lock():
    yield


class _ScalarResult:
    def scalar_one_or_none(self) -> None:
        return None


class _RecordingDb:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.commits = 0

    async def execute(self, *_args: Any, **_kwargs: Any) -> _ScalarResult:
        return _ScalarResult()

    def add(self, entity: Any) -> None:
        self.added.append(entity)

    async def flush(self) -> None:
        return None

    async def refresh(self, _entity: Any) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None


async def _process_case(case_id: str, *, payload: dict[str, Any] | None = None) -> list[RuntimeIntent]:
    case = _case(case_id)
    event_type = case["trigger"]["event_type"]
    raw_input = deepcopy(payload if payload is not None else case["trigger"]["payload"])
    route = "SCAN_COMPLETED" if event_type == "SCAN_COMPLETED" else "PICK_AND_PUT_RESULT"
    logical_input = ROUGH_SORTER_DEFINITION.parsers[route](raw_input)
    command_code = raw_input.get("command_code") if isinstance(raw_input.get("command_code"), str) else None
    state = RoughSorterState(
        phase="READY" if route == "SCAN_COMPLETED" else "PICK_TO_PIPELINE",
        current_correlation=command_code,
    )
    config = RoughSorterConfig.model_validate(
        {
            "device_roles": {
                "input_arm": "ROUGH_SORTER_INPUT_ARM",
                "conveyor": "ROUGH_SORTER_CONVEYOR",
                "output_arm": "ROUGH_SORTER_OUTPUT_ARM",
            },
            "pipeline_input_location": "PIPELINE-IN-01",
            "pipeline_output_location": "PIPELINE-OUT-01",
            "ng_location": "NG-01",
            "warehouse_code": "WH-01",
            "owner_code": "OWNER-01",
            "provider_profile": "wms.2026-07-06.material-flow.sandbox",
        }
    )
    facts = RoughSorterFacts(
        binding_snapshot=RoughSorterBindingSnapshot(
            binding_id=1,
            binding_version=1,
            profile_identity="wms.2026-07-06.material-flow.sandbox",
            plugin_config_hash="0" * 64,
            generated_index_digest="1" * 64,
        )
    )

    class _NoQueryGateway:
        async def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            return SimpleNamespace(
                outcome=ContractViolation(error_code="QUERY_NOT_CONFIGURED", message="characterization"),
                evidence=None,
            )

    decision = await decide(
        logical_input,
        state=state,
        config=config,
        facts=facts,
        context=PluginContext(state=state),
        gateway=_NoQueryGateway(),
    )
    return list(decision.intents)


def test_bc05_characterization_hands_off_scan_decision_target_semantics() -> None:
    content = CHARACTERIZATION_PATH.read_text(encoding="utf-8")

    for source in (SPEC_PATH, FIXTURE_PATH, Path(__file__)):
        assert source.relative_to(REPOSITORY_ROOT).as_posix() in content


def test_six_in_one_normalizer_reads_only_data_and_normalizes_data_aliases() -> None:
    payload = deepcopy(_case("RS-SD-001")["trigger"]["payload"])
    data = payload["data"]
    data["ProductNo"] = data.pop("HHPN")
    data["PONumber"] = data.pop("PkgID")
    payload.update({"HHPN": "TOP-LEVEL-MUST-NOT-WIN", "PkgID": "TOP-LEVEL-MUST-NOT-WIN"})

    normalized = normalize_six_in_one_payload(payload)

    assert data["ProductNo"] == normalized.HHPN
    assert normalized.PkgID == data["PONumber"]
    assert "TOP-LEVEL-MUST-NOT-WIN" not in normalized.barcode_values


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id", ["RS-SD-001", "RS-SD-002"])
async def test_covered_scan_cases_match_fixture_core_intent_signature(case_id: str) -> None:
    case = _case(case_id)
    intents = await _process_case(case_id)

    assert case["implementation_status"] == CURRENT_IMPLEMENTATION_STATUS[case_id] == "covered"
    assert _intent_signature(intents) == _expected_intent_signature(case)
    if case_id == "RS-SD-002":
        mark_ng = next(intent for intent in intents if intent.kind == RuntimeIntentKind.MARK_NG)
        assert mark_ng.reason_code == case["expected_outcome"]["reason_code"] == "SCAN_NG_BY_RULE"


@pytest.mark.asyncio
@pytest.mark.parametrize("ng_keyword", ["SIZENG", "THICKNESSNG"])
async def test_rule_ng_pkg_id_variants_use_stable_scan_ng_reason(ng_keyword: str) -> None:
    case = _case("RS-SD-002")
    payload = deepcopy(case["trigger"]["payload"])
    payload["data"]["PkgID"] = payload["data"]["PkgID"].replace("SIZENG", ng_keyword)

    intents = await _process_case("RS-SD-002", payload=payload)

    assert _intent_signature(intents) == _expected_intent_signature(case)
    mark_ng = next(intent for intent in intents if intent.kind == RuntimeIntentKind.MARK_NG)
    assert mark_ng.reason_code == case["expected_outcome"]["reason_code"] == "SCAN_NG_BY_RULE"


@pytest.mark.asyncio
async def test_missing_pkg_id_uses_generated_plugin_hold_contract() -> None:
    case = _case("RS-SD-003")
    intents = await _process_case("RS-SD-003")

    assert case["implementation_status"] == CURRENT_IMPLEMENTATION_STATUS["RS-SD-003"] == "covered"
    assert _intent_signature(intents) == _expected_intent_signature(case)
    assert all(intent.kind != RuntimeIntentKind.CREATE_MATERIAL_UNIT for intent in intents)
    [hold] = intents
    assert hold.reason_code == "ROUGH_SORTER_CONTEXT_MISSING"
    assert case["expected_outcome"] == {"result": "HOLD", "reason_code": "ROUGH_SORTER_CONTEXT_MISSING"}


@pytest.mark.asyncio
async def test_failed_command_result_matches_covered_command_hold_contract() -> None:
    case = _case("RS-SD-008")
    intents = await _process_case("RS-SD-008")

    assert case["implementation_status"] == CURRENT_IMPLEMENTATION_STATUS["RS-SD-008"] == "covered"
    assert _intent_signature(intents) == _expected_intent_signature(case)
    [block] = intents
    assert block.reason_code == case["expected_outcome"]["reason_code"] == "DEVICE_BUSY"
    assert block.payload_json["error_detail"]["error_code"] == "DEVICE_BUSY"


@pytest.mark.parametrize(
    ("result", "expected_status"),
    [
        (CommandResult.SUCCESS, CommandStatus.COMPLETED),
        (CommandResult.FAILED, CommandStatus.FAILED),
    ],
)
@pytest.mark.asyncio
async def test_command_callback_persists_real_terminal_status_before_runtime_block(
    monkeypatch: pytest.MonkeyPatch,
    result: CommandResult,
    expected_status: CommandStatus,
) -> None:
    command = SimpleNamespace(
        id=808,
        command_code="CMD-PICK-008",
        status=CommandStatus.ACK_RECEIVED,
        task_type="PICK_AND_PUT",
        get_duration_ms=lambda: 0,
    )

    class _CommandRepository:
        async def get_by_command_code(self, _db: Any, command_code: str) -> Any:
            assert command_code == command.command_code
            return command

        async def update(self, _db: Any, command_id: int, update_data: dict[str, Any]) -> Any:
            assert command_id == command.id
            for key, value in update_data.items():
                setattr(command, key, value)
            return command

    service = DeviceCommandService()
    service.repo = _CommandRepository()  # type: ignore[assignment]
    service._invalidate_command_cache = AsyncMock()  # type: ignore[method-assign]
    from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
        workline_runtime_reconciliation_service,
    )

    monkeypatch.setattr(
        workline_runtime_reconciliation_service,
        "record_late_callback_if_pending",
        AsyncMock(return_value=False),
    )
    callback = CommandCallbackResult(
        command_code=command.command_code,
        device_code="ROUGH-SORTER-01",
        source_event_id=f"evt-{result.value.lower()}-008",
        result=result,
        finish_time=1_700_000_000_000,
        data={"measurement_result": "OK"},
    )

    outcome = await service.handle_callback_result(SimpleNamespace(), callback)

    assert outcome.command is command
    assert command.status == expected_status


@pytest.mark.asyncio
async def test_block_effect_holds_only_session_and_preserves_entity_statuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.runtime.orchestration.repositories.session_repository import WorklineSessionRepository
    from src.app.workline.services import write_back_service as workline_effects

    persist_manual_hold = AsyncMock()
    emit_timeline = AsyncMock()
    monkeypatch.setattr(WorklineSessionRepository, "persist_manual_hold", persist_manual_hold)
    monkeypatch.setattr(workline_effects, "_effect_trace_payload", lambda _ctx: {})
    monkeypatch.setattr(workline_effects, "_emit_timeline", emit_timeline)
    now = timezone.now_for_db()
    session = SimpleNamespace(
        id=808,
        status=SessionStatus.WAITING_DEVICE_RESULT,
        current_wait_type="COMMAND_RESULT",
        waiting_since=now,
        deadline_at=now,
        current_wait_timeout_seconds=300,
        awaiting_device_command_code="CMD-PICK-008",
        ended_at=None,
        failure_domain=None,
        failure_code=None,
        failure_message=None,
        trace_id=None,
    )
    material = SimpleNamespace(status=MaterialUnitStatus.IN_TRANSIT)
    command = SimpleNamespace(status=CommandStatus.FAILED)
    db = SimpleNamespace()

    effect_result = await RuntimeIntentEffectApplier().apply(
        {
            "db": db,
            "session": session,
            "inbox": SimpleNamespace(id=8808, payload_json={}),
            "trace_id": "trace-rs-sd-008",
            "current_status": SessionStatus.WAITING_DEVICE_RESULT.value,
            "now": now,
            "material_unit": material,
            "command": command,
        },
        [
            RuntimeIntent.block(
                scope=BlockScope.COMMAND,
                reason_code="DEVICE_BUSY",
                message="设备忙",
            )
        ],
    )

    assert effect_result.disposition == WriteBackDisposition.PROCESSED
    assert session.status == SessionStatus.MANUAL_HOLD
    assert session.failure_domain == BlockScope.COMMAND.value
    assert material.status == MaterialUnitStatus.IN_TRANSIT
    assert command.status == CommandStatus.FAILED
    persist_manual_hold.assert_awaited_once_with(
        db,
        session_id=session.id,
        occurred_at=now,
        failure_domain=BlockScope.COMMAND.value,
        failure_code="DEVICE_BUSY",
        failure_message="设备忙",
    )
    emit_timeline.assert_awaited_once()


@pytest.mark.asyncio
async def test_success_command_result_without_pinned_material_facts_holds() -> None:
    case = _case("RS-SD-004")
    intents = await _process_case("RS-SD-004")

    assert case["implementation_status"] == CURRENT_IMPLEMENTATION_STATUS["RS-SD-004"] == "covered"
    assert _intent_signature(intents) == (("BLOCK", None, "MATERIAL"),)
    assert intents[0].reason_code == "ROUGH_SORTER_CONTEXT_MISSING"
    assert _expected_intent_signature(case) == (
        ("UPDATE_CONTEXT", None, None),
        ("COMMAND", "MOVE_FORWARD", None),
    )


@pytest.mark.asyncio
async def test_timer_timeout_facade_holds_session_with_approved_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.device.repositories import command_repository as command_repository_module
    from src.app.workline.services.diagnostic_service import workline_diagnostic_service

    case = _case("RS-SD-009")
    command_code = case["trigger"]["payload"]["command_code"]
    now = timezone.now_for_db()
    deadline_at = now - timedelta(seconds=1)
    command = SimpleNamespace(
        id=909,
        command_code=command_code,
        device_id=3,
        correlation_id=None,
        status=CommandStatus.ACK_RECEIVED,
        task_type="PICK_AND_PUT",
        ack_received_at=now - timedelta(seconds=30),
    )
    session = SimpleNamespace(
        id=9,
        workline_id=7,
        trace_id="trace-rs-sd-009",
        status=SessionStatus.WAITING_DEVICE_RESULT,
        current_wait_type="COMMAND_RESULT",
        current_wait_timeout_seconds=300,
        waiting_since=now - timedelta(seconds=301),
        deadline_at=deadline_at,
        awaiting_device_command_code=command_code,
        reconciliation_state=None,
        context_json={},
        ended_at=None,
    )
    runtime_hold = SimpleNamespace(id=9009, source_reason="CALLBACK_DEADLINE_EXPIRED", evidence_snapshot_json={})
    runtime_hold_creation = AsyncMock(return_value=runtime_hold)
    service = WorklineRuntimeReconciliationService(
        session_repository=SimpleNamespace(get_for_update=AsyncMock(return_value=session)),
        workline_repository=SimpleNamespace(get_for_update=AsyncMock(return_value=SimpleNamespace(id=7))),
        system_outbox_repository=SimpleNamespace(cancel_active_by_session=AsyncMock(return_value=0)),
        device_service=SimpleNamespace(mark_callback_deadline_expired=AsyncMock(return_value=None)),
        runtime_hold_creation_service=SimpleNamespace(create_for_callback_deadline_expired=runtime_hold_creation),
        rack_task_repository=SimpleNamespace(cancel_active_by_material_session=AsyncMock(return_value=0)),
        workline_status_projection_service=SimpleNamespace(project_reconciling=AsyncMock(return_value=True)),
    )
    timeout_payload = {
        "event_type": "TIMER_TIMEOUT",
        "data": {
            **case["trigger"]["payload"],
            "workline_id": session.workline_id,
            "deadline_at": deadline_at.isoformat(),
            "awaiting_device_command_code": command_code,
            "ack_received_at": command.ack_received_at.isoformat(),
        },
    }

    class _CommandRepository:
        async def get_by_command_code(self, _db: Any, requested_command_code: str) -> Any:
            assert requested_command_code == command_code
            return command

    diagnostic_record = AsyncMock(return_value=None)
    monkeypatch.setattr(command_repository_module, "DeviceCommandRepository", _CommandRepository)
    monkeypatch.setattr(workline_diagnostic_service, "record_event", diagnostic_record)
    db = _RecordingDb()

    result = await service.handle_timer_timeout(
        db,
        session_id=session.id,
        inbox_id=9009,
        payload=timeout_payload,
        source_inbox_id=9008,
        trace_id=session.trace_id,
    )

    assert case["implementation_status"] == CURRENT_IMPLEMENTATION_STATUS["RS-SD-009"] == "covered"
    assert case["expected_intents"] == []
    assert case["expected_state"]["runtime_hold"] == RuntimeHoldStatus.OPEN.value
    assert result.disposition == "RECONCILED"
    assert result.session is session
    assert session.status == SessionStatus.MANUAL_HOLD
    assert session.reconciliation_state == RuntimeReconciliationState.PENDING
    assert session.reconciliation_reason == RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED
    assert session.reconciliation_wait_token == command_code
    assert session.reconciliation_deadline_at == deadline_at
    assert session.reconciliation_ack_received_at == command.ack_received_at
    assert session.context_json["runtime_reconciliation_registration"]["decision"]["evidence_refs"] == [
        "inbox:9009",
        "command:909",
    ]
    runtime_hold_creation.assert_awaited_once()
    diagnostic_record.assert_awaited_once()
    assert len(db.added) == 1
    timeout_timeline = db.added[0]
    assert timeout_timeline.message == "Callback deadline expired; runtime reconciliation started."
    assert timeout_timeline.payload_json["reason"] == "ROUGH_SORTER_PICK_RESULT_TIMEOUT"
    assert timeout_timeline.payload_json["wait_token"] == command_code
    assert timeout_timeline.payload_json["deadline_at"] == deadline_at.isoformat()
    assert timeout_timeline.payload_json["ack_received_at"] == command.ack_received_at.isoformat()
    assert runtime_hold.source_reason == "ROUGH_SORTER_PICK_RESULT_TIMEOUT"
    assert runtime_hold.evidence_snapshot_json["reason"] == "ROUGH_SORTER_PICK_RESULT_TIMEOUT"
    assert timeout_timeline.payload_json["reason"] == case["expected_outcome"]["reason_code"]
    assert case["expected_outcome"]["reason_code"] == "ROUGH_SORTER_PICK_RESULT_TIMEOUT"


@pytest.mark.asyncio
async def test_late_duplicate_callback_records_idempotent_evidence_without_advancing_session() -> None:
    case = _case("RS-SD-013")
    command_code = case["trigger"]["payload"]["command_code"]
    session = SimpleNamespace(
        id=13,
        workline_id=7,
        trace_id="trace-rs-sd-013",
        status=SessionStatus.MANUAL_HOLD,
        current_wait_type="COMMAND_RESULT",
        awaiting_device_command_code="CMD-CURRENT-013",
        reconciliation_state=RuntimeReconciliationState.PENDING,
        reconciliation_reason=RuntimeReconciliationReason.CALLBACK_DEADLINE_EXPIRED,
        reconciliation_command_id=991,
        context_json={},
        reconciliation_late_evidence_received=False,
    )
    command = SimpleNamespace(
        id=991,
        command_code=command_code,
        device_id=3,
        correlation_id=None,
        status="COMPLETED",
    )
    session_repository = SimpleNamespace(
        get_pending_reconciliation_by_command_id=AsyncMock(return_value=session),
        get_for_update=AsyncMock(return_value=session),
    )
    service = WorklineRuntimeReconciliationService(
        session_repository=session_repository,
        workline_repository=SimpleNamespace(get_for_update=AsyncMock(return_value=SimpleNamespace(id=7))),
        workline_status_projection_service=SimpleNamespace(project_reconciling=AsyncMock(return_value=True)),
    )
    db = _RecordingDb()
    session_anchor = (session.status, session.current_wait_type, session.awaiting_device_command_code)

    first = await service.record_late_callback_if_pending(
        db,
        command=command,
        callback_payload=deepcopy(case["trigger"]["payload"]),
    )
    duplicate = await service.record_late_callback_if_pending(
        db,
        command=command,
        callback_payload=deepcopy(case["trigger"]["payload"]),
    )

    assert first is duplicate is True
    assert (session.status, session.current_wait_type, session.awaiting_device_command_code) == session_anchor
    assert session.reconciliation_state == RuntimeReconciliationState.PENDING
    evidence = session.context_json["runtime_reconciliation_late_callback_evidence"]
    assert len(evidence) == 1
    assert evidence[0]["command_code"] == command_code
    assert len(db.added) == 1
    assert db.added[0].message == "Late callback recorded as runtime reconciliation evidence."


@pytest.mark.asyncio
async def test_current_wait_anchor_mismatch_archives_without_applying_followup_command() -> None:
    case = _case("RS-SD-013")
    command_code = case["trigger"]["payload"]["command_code"]
    session = SimpleNamespace(
        id=13,
        workline_id=7,
        trace_id="trace-rs-sd-013",
        status=SessionStatus.WAITING_DEVICE_RESULT,
        current_wait_type="COMMAND_RESULT",
        awaiting_device_command_code="CMD-CURRENT-013",
        context_json={},
    )
    command = SimpleNamespace(id=991, command_code=command_code, status="COMPLETED")
    inbox = SimpleNamespace(
        id=1301,
        kind="COMMAND_RESULT",
        trace_id="trace-rs-sd-013",
        payload_json=deepcopy(case["trigger"]["payload"]),
    )
    business_writeback = AsyncMock()
    inbox_service = SimpleNamespace(mark_processed=AsyncMock(return_value=True))
    state = WriteBackState()
    db = _RecordingDb()
    session_anchor = (session.status, session.current_wait_type, session.awaiting_device_command_code)
    callback = RuntimeInboxWriteBackService(
        write_back_service=SimpleNamespace(write_back=business_writeback),
        inbox_service=inbox_service,
    ).build_write_callback(
        db,
        session=session,
        workline=SimpleNamespace(id=7),
        inbox=inbox,
        devices_by_role={},
        device=None,
        command=command,
        inbox_pk=inbox.id,
        session_snapshot=(session.status, session.awaiting_device_command_code),
        sse_workline_id=7,
        sse_session_id=session.id,
        processor_token="lease-rs-sd-013",
        state=state,
    )
    followup = RuntimeIntent.command(
        device_role="ROUGH_SORTER_CONVEYOR", action="MOVE_FORWARD", result_policy="COMMAND_RESULT"
    )

    await callback(OrchestratorResult(success=True, intents=[followup]))

    assert (session.status, session.current_wait_type, session.awaiting_device_command_code) == session_anchor
    business_writeback.assert_not_awaited()
    inbox_service.mark_processed.assert_awaited_once_with(
        db,
        inbox_id=inbox.id,
        lease_token="lease-rs-sd-013",
    )
    assert state.disposition == WriteBackDisposition.PROCESSED
    assert state.enqueue_outbox_dispatch is False
    assert len(db.added) == 1
    assert db.added[0].message == "LATE_COMMAND_RESULT_ARCHIVED"
    assert db.added[0].payload_json["reason"] == "COMMAND_RESULT_BECAME_STALE_BEFORE_WRITE"


@pytest.mark.asyncio
async def test_unknown_command_correlation_is_rejected_at_unpinned_accept_seam() -> None:
    case = _case("RS-SD-013")
    repository = SimpleNamespace(
        get_by_source_event_identity=AsyncMock(return_value=None),
        correlation_id_exists=AsyncMock(return_value=False),
        add_received=AsyncMock(),
    )

    with pytest.raises(RuntimeInboxCorrelationUnavailable):
        await RuntimeInboxService(repository=repository).accept_received(
            SimpleNamespace(),
            provider_code="DEVICE",
            event_type="COMMAND_RESULT",
            source_event_id=case["trigger"]["source_event_id"],
            payload_hash="hash-rs-sd-013",
            kind="COMMAND_RESULT",
            payload_json=deepcopy(case["trigger"]["payload"]),
            payload_schema_version=1,
            correlation_id="corr-unknown-rs-sd-013",
        )

    assert case["implementation_status"] == CURRENT_IMPLEMENTATION_STATUS["RS-SD-013"] == "covered"
    repository.add_received.assert_not_awaited()
    # 完整 CallbackIngress 会先固定当前 Session，再由 processor 归档 mismatch；
    # 此处只验证缺少固定归属的低层 accept seam 必须 fail closed。
    assert case["expected_outcome"]["result"] == "ARCHIVED_EVIDENCE"
    assert case["expected_intents"] == []
    assert case["replay_policy"]["session_progress"] == "NO_PROGRESS"


def test_fixture_has_fixed_case_semantic_signatures() -> None:
    fixture = _load_fixture()
    cases = fixture["cases"]
    cases_by_id = {case["case_id"]: case for case in cases}

    assert fixture["schema_version"] == "rough-sorter-scan-decision.v1"
    assert fixture["slice_id"] == "rough_sorter.scan_to_admission_decision"
    assert list(cases_by_id) == list(EXPECTED_CASE_OVERVIEW)
    assert len(cases_by_id) == len(cases)
    for case_id, expected_signature in EXPECTED_CASE_OVERVIEW.items():
        case = cases_by_id[case_id]
        state = case["expected_state"]
        actual_signature = (
            _normalize_trigger_signature(case),
            case["expected_outcome"]["result"],
            (state["material"], state.get("command"), state["session"], state["context_phase"]),
            tuple((intent["kind"], intent.get("action"), intent.get("scope")) for intent in case["expected_intents"]),
            case["implementation_status"],
            case["expected_outcome"]["reason_code"],
        )
        assert actual_signature == expected_signature, case_id


def test_case_fields_use_closed_non_empty_structures() -> None:
    for case in _load_fixture()["cases"]:
        case_id = case["case_id"]
        assert case.keys() >= REQUIRED_CASE_FIELDS, case_id
        assert case["trigger"].keys() >= {
            "event_type",
            "source_event_id",
            "payload",
            "decision_discriminator",
        }, case_id
        assert case["trigger"]["event_type"] in ALLOWED_TRIGGER_TYPES, case_id
        assert case["trigger"]["source_event_id"].strip(), case_id
        assert isinstance(case["trigger"]["payload"], dict) and case["trigger"]["payload"], case_id
        assert isinstance(case["trigger"]["decision_discriminator"], dict), case_id
        assert case["trigger"]["decision_discriminator"] and all(
            isinstance(key, str) and key and isinstance(value, str) and value
            for key, value in case["trigger"]["decision_discriminator"].items()
        ), case_id
        assert isinstance(case["preconditions"], list) and all(case["preconditions"]), case_id
        expected_evidence_phases = (
            {"first_attempt", "replay", "subsequent_replay"} if case_id == "RS-SD-012" else {"first_attempt", "replay"}
        )
        assert set(case["recorded_evidence"]) == expected_evidence_phases, case_id
        assert all(
            isinstance(items, list) and items and all(isinstance(item, str) and item for item in items)
            for items in case["recorded_evidence"].values()
        ), case_id
        assert case["expected_state"].keys() >= {"material", "session"}, case_id
        assert all(
            key in ALLOWED_STATE_VALUES and value in ALLOWED_STATE_VALUES[key]
            for key, value in case["expected_state"].items()
        ), case_id
        assert isinstance(case["expected_intents"], list), case_id
        assert set(case["expected_outcome"]) == {"result", "reason_code"}, case_id
        assert case["expected_outcome"]["result"] in ALLOWED_OUTCOMES, case_id
        assert isinstance(case["replay_expectation"], str) and case["replay_expectation"].strip(), case_id
        assert set(case["replay_policy"]) == {"query", "effect", "session_progress"}, case_id
        assert case["replay_policy"]["query"] in {"NOT_APPLICABLE", "REUSE_RECORDED"}, case_id
        expected_effect_policy = "PERSIST_HOLD_ONCE_THEN_NO_NEW_EFFECT" if case_id == "RS-SD-012" else "NO_NEW_EFFECT"
        assert case["replay_policy"]["effect"] == expected_effect_policy, case_id
        assert case["replay_policy"]["session_progress"] == "NO_PROGRESS", case_id

    cases = {case["case_id"]: case for case in _load_fixture()["cases"]}
    ng_pkg_id = cases["RS-SD-002"]["trigger"]["payload"]["data"]["PkgID"].upper()
    assert cases["RS-SD-002"]["trigger"]["decision_discriminator"]["pkg_ng_rule"] in ng_pkg_id
    assert "PkgID" not in cases["RS-SD-003"]["trigger"]["payload"]["data"]


def test_block_cases_preserve_persisted_entity_states() -> None:
    cases = {case["case_id"]: case for case in _load_fixture()["cases"]}

    for case in cases.values():
        if any(intent["kind"] == "BLOCK" for intent in case["expected_intents"]):
            assert case["expected_state"]["material"] != "MANUAL_HOLD", case["case_id"]
            assert case["expected_state"]["command"] != "MANUAL_HOLD", case["case_id"]
            assert case["expected_state"]["session"] == "MANUAL_HOLD", case["case_id"]

    for case_id in ("RS-SD-007", "RS-SD-010"):
        assert cases[case_id]["trigger"]["payload"]["result"] == "SUCCESS"
        assert cases[case_id]["expected_state"]["command"] == CommandStatus.COMPLETED.value


def test_measurement_cases_follow_authoritative_payload_contract() -> None:
    def is_positive_number(value: Any) -> bool:
        try:
            return float(value) > 0
        except (TypeError, ValueError):
            return False

    cases = {case["case_id"]: case for case in _load_fixture()["cases"]}
    measurement_fields = ("reel_diameter", "reel_thickness")

    for case_id in ("RS-SD-004", "RS-SD-005", "RS-SD-006", "RS-SD-010"):
        data = cases[case_id]["trigger"]["payload"]["data"]
        assert all(is_positive_number(data.get(field)) for field in measurement_fields), case_id

    invalid_data = cases["RS-SD-007"]["trigger"]["payload"]["data"]
    assert not all(is_positive_number(invalid_data.get(field)) for field in measurement_fields)


def test_intents_outcomes_and_replay_policies_are_consistent() -> None:
    for case in _load_fixture()["cases"]:
        case_id = case["case_id"]
        intents = case["expected_intents"]
        outcome = case["expected_outcome"]["result"]
        intent_kinds = [intent["kind"] for intent in intents]

        assert set(intent_kinds) <= ALLOWED_INTENT_KINDS, case_id
        for intent in intents:
            if intent["kind"] == "COMMAND":
                assert intent.get("action") in ALLOWED_COMMAND_ACTIONS, case_id
            if intent["kind"] == "BLOCK":
                assert intent.get("scope") in ALLOWED_BLOCK_SCOPES, case_id

        if outcome.endswith("_PERSISTED"):
            assert intent_kinds.count("COMMAND") == 1 and "BLOCK" not in intent_kinds, case_id
        elif outcome == "HOLD":
            if case["trigger"]["event_type"] == "TIMER_TIMEOUT":
                assert not intents, case_id
            else:
                assert intent_kinds == ["BLOCK"], case_id
        else:
            assert outcome in {"REPLAY_ACCEPTED_NOOP", "ARCHIVED_EVIDENCE"} and not intents, case_id

        expected_query_policy = "REUSE_RECORDED" if case_id in QUERY_REPLAY_CASES else "NOT_APPLICABLE"
        assert case["replay_policy"]["query"] == expected_query_policy, case_id

        if case_id == "RS-SD-012":
            assert intent_kinds == ["BLOCK"]
            assert case["replay_policy"]["effect"] == "PERSIST_HOLD_ONCE_THEN_NO_NEW_EFFECT"

    late_callback = _load_fixture()["cases"][-1]
    assert late_callback["case_id"] == "RS-SD-013"
    assert late_callback["expected_state"] == {
        "material": "UNCHANGED",
        "command": "UNCHANGED",
        "session": "UNCHANGED",
        "context_phase": "UNCHANGED",
    }
    assert late_callback["expected_intents"] == []
    assert late_callback["expected_outcome"] == {
        "result": "ARCHIVED_EVIDENCE",
        "reason_code": "COMMAND_RESULT_CORRELATION_MISMATCH",
    }


def test_case_categories_retain_required_evidence_ownership() -> None:
    cases = {case["case_id"]: case for case in _load_fixture()["cases"]}

    required_block_evidence = {
        "block_intent_identity",
        "block_effect_identity",
        "block_scope",
        "block_reason_code",
        "session_hold_write_result",
    }
    for case in cases.values():
        if any(intent["kind"] == "BLOCK" for intent in case["expected_intents"]):
            processing_phase = "replay" if case["case_id"] == "RS-SD-012" else "first_attempt"
            subsequent_replay_phase = "subsequent_replay" if case["case_id"] == "RS-SD-012" else "replay"
            assert required_block_evidence <= set(case["recorded_evidence"][processing_phase]), case["case_id"]
            assert {
                "reused_block_intent_identity",
                "reused_block_effect_identity",
                "original_session_hold_write_result",
                "zero_new_hold_write",
            } <= set(case["recorded_evidence"][subsequent_replay_phase]), case["case_id"]

    for case_id in ("RS-SD-001", "RS-SD-002", "RS-SD-003"):
        evidence = cases[case_id]["recorded_evidence"]
        assert {
            "normalized_input_snapshot",
            "payload_digest",
            "business_key",
            "barcode_rule_version",
            "barcode_decision",
            "barcode_reason",
        } <= set(evidence["first_attempt"]), case_id
        assert "payload_digest" in evidence["replay"], case_id

    for case_id in ("RS-SD-004", "RS-SD-005", "RS-SD-006", "RS-SD-007", "RS-SD-010"):
        assert "command_result_snapshot" in cases[case_id]["recorded_evidence"]["first_attempt"], case_id
    assert "measurement_snapshot" in cases["RS-SD-005"]["recorded_evidence"]["first_attempt"]
    assert "measurement_validation_errors" in cases["RS-SD-007"]["recorded_evidence"]["first_attempt"]
    for case_id in ("RS-SD-004", "RS-SD-006", "RS-SD-010", "RS-SD-011"):
        evidence = cases[case_id]["recorded_evidence"]
        assert {"wms_query_identity", "wms_request_summary"} <= set(evidence["first_attempt"]), case_id
    for case_id in ("RS-SD-004", "RS-SD-006"):
        evidence = cases[case_id]["recorded_evidence"]
        assert "wms_response_summary" in evidence["first_attempt"], case_id
        assert "original_wms_response_summary" in evidence["replay"], case_id

    for case_id in ("RS-SD-001", "RS-SD-002", "RS-SD-004", "RS-SD-005", "RS-SD-006"):
        assert {
            "material_context_effect_identity",
            "material_context_target_state",
            "material_context_write_result",
        } <= set(cases[case_id]["recorded_evidence"]["first_attempt"]), case_id
        assert {
            "command_intent_identity",
            "command_action",
            "command_correlation_key",
            "command_wait_deadline",
        } <= set(cases[case_id]["recorded_evidence"]["first_attempt"]), case_id

    assert {"wms_timeout_summary", "payload_digest"} <= set(cases["RS-SD-010"]["recorded_evidence"]["first_attempt"])
    assert {"original_timeout_summary", "no_successful_wms_evidence"} <= set(
        cases["RS-SD-010"]["recorded_evidence"]["replay"]
    )
    assert {"incoming_payload_digest", "digest_mismatch", "conflict_audit"} <= set(
        cases["RS-SD-012"]["recorded_evidence"]["replay"]
    )
    assert {"command_result_snapshot", "device_error_summary", "hold_reason", "payload_digest"} <= set(
        cases["RS-SD-008"]["recorded_evidence"]["first_attempt"]
    )
    assert {
        "command_identity",
        "deadline_snapshot",
        "timeout_event",
        "payload_digest",
        "reconciliation_identity",
        "reconciliation_reason",
        "runtime_hold_identity",
        "runtime_hold_write_result",
        "session_hold_write_result",
    } <= set(cases["RS-SD-009"]["recorded_evidence"]["first_attempt"])
    assert {
        "reused_reconciliation_identity",
        "reused_runtime_hold_identity",
        "original_session_hold_write_result",
        "zero_new_runtime_hold_write",
    } <= set(cases["RS-SD-009"]["recorded_evidence"]["replay"])
    assert {"normalized_input_snapshot", "query_response_summary", "decision", "intent_identity"} <= set(
        cases["RS-SD-011"]["recorded_evidence"]["first_attempt"]
    )
    assert {"replay_request", "matched_payload_digest", "reused_decision", "reused_intent_identity"} <= set(
        cases["RS-SD-011"]["recorded_evidence"]["replay"]
    )
    assert {
        "callback_snapshot",
        "correlation_lookup",
        "current_wait_anchor",
        "mismatch_reason",
        "payload_digest",
    } <= set(cases["RS-SD-013"]["recorded_evidence"]["first_attempt"])
    assert {"original_mismatch_evidence", "payload_digest"} <= set(cases["RS-SD-013"]["recorded_evidence"]["replay"])


def test_source_refs_resolve_to_repository_files() -> None:
    for case in _load_fixture()["cases"]:
        assert case["source_refs"] and all(
            isinstance(source_ref, str) and source_ref.strip() and (REPOSITORY_ROOT / source_ref).is_file()
            for source_ref in case["source_refs"]
        ), case["case_id"]


def test_upstream_documents_reference_scan_decision_ssot_with_stable_ownership() -> None:
    for document_path, (relative_spec_path, ownership_marker) in UPSTREAM_DOCUMENT_CONTRACTS.items():
        content = (REPOSITORY_ROOT / document_path).read_text(encoding="utf-8")

        assert f"]({relative_spec_path})" in content, document_path
        assert ownership_marker in content, document_path


def test_business_spec_has_strict_metadata_and_stable_sections() -> None:
    content = SPEC_PATH.read_text(encoding="utf-8")
    lines = content.splitlines()
    metadata = {}
    for line in lines[1:]:
        if not line.strip():
            continue
        if line.startswith(">"):
            break
        key, separator, value = line.partition(":")
        assert separator, line
        metadata[key.strip()] = value.strip()

    assert lines[0] == "# 粗分机扫码到准入决策窄闭环合同"
    assert set(metadata) == {"contract_version", "status", "owner", "approved_by", "approved_at"}
    assert metadata["contract_version"] == "rough-sorter-scan-decision.v1"
    assert metadata["status"] == "Approved"
    assert metadata["owner"] == metadata["approved_by"] == "kaizhou"
    approved_at = datetime.fromisoformat(metadata["approved_at"])
    assert approved_at.tzinfo is not None
    assert approved_at.utcoffset() is not None
    assert "待明确" not in content
    assert "当前未获得业务 Owner 明确批准" not in content
    for heading in (
        "## 切片边界",
        "## 输入身份与归一化",
        "## 状态与决策表",
        "## 能力与 Evidence 所有权",
        "## 异常矩阵",
        "## Replay 契约",
        "## 原因码决策记录",
        "## 当前实现对照",
        "## 验收标准",
    ):
        assert heading in content
    assert (
        "本切片有四类合法终点：下一设备命令已持久化并等待终态结果、稳定原因码 Hold、late/unknown callback 的 "
        "evidence-only 归档、replay no-op；后两类均不得推进当前 Session。"
    ) in content
    assert "`PICK_AND_PUT`、`MOVE_FORWARD`、`MOVE_TO_NG` 均使用 `COMMAND_RESULT`" in content
