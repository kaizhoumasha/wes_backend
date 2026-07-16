"""粗分机扫码到准入决策窄闭环规格包测试。"""

import json
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.app.device.models.command import CommandStatus
from src.app.runtime.capabilities.material_flow.contracts.rough_sorter import normalize_six_in_one_payload
from src.app.runtime.orchestration.effect_result import WriteBackDisposition
from src.app.runtime.orchestration.models.session import (
    RuntimeReconciliationReason,
    RuntimeReconciliationState,
    SessionStatus,
)
from src.app.runtime.orchestration.orchestrator_bridge import OrchestratorResult, OrchestratorService
from src.app.runtime.orchestration.runtime_intent import RuntimeIntent, RuntimeIntentKind
from src.app.runtime.orchestration.services.reconciliation.runtime_reconciliation_service_impl import (
    WorklineRuntimeReconciliationService,
)
from src.app.runtime.orchestration.services.runtime_inbox import (
    RuntimeInboxCorrelationUnavailable,
    RuntimeInboxService,
    RuntimeInboxWriteBackService,
    WriteBackState,
)
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
        ("IN_TRANSIT", None, "WAITING_COMMAND_RESULT", "PICK_TO_PIPELINE"),
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
        ("NG", None, "WAITING_COMMAND_RESULT", "NG_MOVING"),
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
        "gap",
        "ROUGH_SORTER_CONTEXT_MISSING",
    ),
    "RS-SD-004": (
        (
            "COMMAND_RESULT",
            (("command_result", "SUCCESS"), ("measurement", "OK"), ("wms_admission", "ADMIT")),
        ),
        "MOVE_FORWARD_PERSISTED",
        ("IN_TRANSIT", None, "WAITING_COMMAND_RESULT", "MOVING_FORWARD"),
        (("UPDATE_CONTEXT", None, None), ("COMMAND", "MOVE_FORWARD", None)),
        "gap",
        None,
    ),
    "RS-SD-005": (
        (
            "COMMAND_RESULT",
            (("command_result", "SUCCESS"), ("measurement", "NG"), ("wms_admission", "NOT_QUERIED")),
        ),
        "MOVE_TO_NG_PERSISTED",
        ("NG", None, "WAITING_COMMAND_RESULT", "NG_MOVING"),
        (("UPDATE_CONTEXT", None, None), ("MARK_NG", None, None), ("COMMAND", "MOVE_TO_NG", None)),
        "gap",
        "MEASUREMENT_NG",
    ),
    "RS-SD-006": (
        (
            "COMMAND_RESULT",
            (("command_result", "SUCCESS"), ("measurement", "OK"), ("wms_admission", "REJECT")),
        ),
        "MOVE_TO_NG_PERSISTED",
        ("NG", None, "WAITING_COMMAND_RESULT", "NG_MOVING"),
        (("UPDATE_CONTEXT", None, None), ("MARK_NG", None, None), ("COMMAND", "MOVE_TO_NG", None)),
        "gap",
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
        ("MANUAL_HOLD", "UNCHANGED", "MANUAL_HOLD", "PICK_TO_PIPELINE"),
        (("BLOCK", None, "MATERIAL"),),
        "gap",
        "ROUGH_SORTER_MEASUREMENT_INVALID",
    ),
    "RS-SD-008": (
        ("COMMAND_RESULT", (("command_result", "FAILURE"),)),
        "HOLD",
        ("IN_TRANSIT", "MANUAL_HOLD", "MANUAL_HOLD", "PICK_TO_PIPELINE"),
        (("BLOCK", None, "COMMAND"),),
        "covered",
        "DEVICE_BUSY",
    ),
    "RS-SD-009": (
        ("TIMER_TIMEOUT", (("command_result", "TIMEOUT"),)),
        "HOLD",
        ("IN_TRANSIT", "MANUAL_HOLD", "MANUAL_HOLD", "PICK_TO_PIPELINE"),
        (("BLOCK", None, "COMMAND"),),
        "partial",
        "ROUGH_SORTER_PICK_RESULT_TIMEOUT",
    ),
    "RS-SD-010": (
        (
            "COMMAND_RESULT",
            (("command_result", "SUCCESS"), ("measurement", "OK"), ("wms_admission", "TIMEOUT")),
        ),
        "HOLD",
        ("MANUAL_HOLD", "UNCHANGED", "MANUAL_HOLD", "PICK_TO_PIPELINE"),
        (("BLOCK", None, "MATERIAL"),),
        "gap",
        "WMS_TIMEOUT",
    ),
    "RS-SD-011": (
        ("REPLAY_REQUEST", (("duplicate_digest", "SAME"),)),
        "REPLAY_ACCEPTED_NOOP",
        ("UNCHANGED", "UNCHANGED", "UNCHANGED", "UNCHANGED"),
        (),
        "gap",
        None,
    ),
    "RS-SD-012": (
        ("REPLAY_REQUEST", (("duplicate_digest", "DIFFERENT"),)),
        "HOLD",
        ("MANUAL_HOLD", "UNCHANGED", "MANUAL_HOLD", "UNCHANGED"),
        (("BLOCK", None, "MATERIAL"),),
        "gap",
        "IDEMPOTENCY_CONFLICT",
    ),
    "RS-SD-013": (
        ("COMMAND_RESULT", (("correlation", "LATE_OR_UNKNOWN_MISMATCH"),)),
        "ARCHIVED_EVIDENCE",
        ("UNCHANGED", "UNCHANGED", "UNCHANGED", "UNCHANGED"),
        (),
        "partial",
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
    "material": {"IN_TRANSIT", "NG", "NOT_CREATED", "MANUAL_HOLD", "UNCHANGED"},
    "context_phase": {"PICK_TO_PIPELINE", "NG_MOVING", "MOVING_FORWARD", "UNCHANGED"},
    "command": {"NOT_CREATED", "UNCHANGED", "MANUAL_HOLD"},
    "session": {"WAITING_COMMAND_RESULT", "MANUAL_HOLD", "UNCHANGED"},
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
    "RS-SD-003": "gap",
    "RS-SD-004": "gap",
    "RS-SD-008": "covered",
    "RS-SD-009": "partial",
    "RS-SD-013": "partial",
}


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


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
    inbox_kind = "DEVICE_EVENT" if event_type == "SCAN_COMPLETED" else event_type
    result = await OrchestratorService(lock_provider=lambda _lock_key: _noop_lock()).process_inbox(
        session=SimpleNamespace(id=1, contract_version="rough_sorter.v2"),
        workline=SimpleNamespace(
            contract_version="rough_sorter.v2",
            plugin_key="rough_sorter",
            config={"pipeline_input_location": "PIPELINE-IN-01", "ng_location": "NG-01"},
            runtime_config_json={},
        ),
        inbox=SimpleNamespace(
            kind=inbox_kind,
            event_type=event_type,
            payload_json=deepcopy(payload if payload is not None else case["trigger"]["payload"]),
            trace_id=f"trace-{case_id.lower()}",
        ),
        devices_by_role={},
        services=SimpleNamespace(),
        trace_id=f"trace-{case_id.lower()}",
    )

    assert result.success is True, result.error
    return result.intents or []


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
async def test_missing_pkg_id_current_behavior_remains_ng_flow_and_target_hold_is_gap() -> None:
    case = _case("RS-SD-003")
    intents = await _process_case("RS-SD-003")

    assert case["implementation_status"] == CURRENT_IMPLEMENTATION_STATUS["RS-SD-003"] == "gap"
    assert _intent_signature(intents) == (
        ("UPDATE_CONTEXT", None, None),
        ("MARK_NG", None, None),
        ("COMMAND", "MOVE_TO_NG", None),
    )
    assert all(intent.kind != RuntimeIntentKind.CREATE_MATERIAL_UNIT for intent in intents)
    mark_ng = next(intent for intent in intents if intent.kind == RuntimeIntentKind.MARK_NG)
    assert mark_ng.reason_code == "BARCODE_INCOMPLETE"
    assert _intent_signature(intents) != _expected_intent_signature(case)
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


@pytest.mark.asyncio
async def test_success_command_result_only_continues_and_does_not_cover_measurement_wms_target() -> None:
    case = _case("RS-SD-004")
    intents = await _process_case("RS-SD-004")

    assert case["implementation_status"] == CURRENT_IMPLEMENTATION_STATUS["RS-SD-004"] == "gap"
    assert _intent_signature(intents) == (("CONTINUE_NEXT", None, None),)
    assert _intent_signature(intents) != _expected_intent_signature(case)
    assert _expected_intent_signature(case) == (
        ("UPDATE_CONTEXT", None, None),
        ("COMMAND", "MOVE_FORWARD", None),
    )


@pytest.mark.asyncio
async def test_timer_timeout_facade_holds_session_with_current_partial_reason(
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
    runtime_hold_creation = AsyncMock(return_value=SimpleNamespace(id=9009))
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

    assert case["implementation_status"] == CURRENT_IMPLEMENTATION_STATUS["RS-SD-009"] == "partial"
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
    assert timeout_timeline.payload_json["reason"] == "CALLBACK_DEADLINE_EXPIRED"
    assert timeout_timeline.payload_json["wait_token"] == command_code
    assert timeout_timeline.payload_json["deadline_at"] == deadline_at.isoformat()
    assert timeout_timeline.payload_json["ack_received_at"] == command.ack_received_at.isoformat()
    assert session.reconciliation_reason.value != case["expected_outcome"]["reason_code"]
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
    followup = RuntimeIntent.command(device_role="ROUGH_SORTER_CONVEYOR", action="MOVE_FORWARD")

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
async def test_unknown_command_correlation_is_rejected_before_target_archive_and_remains_partial() -> None:
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

    assert case["implementation_status"] == CURRENT_IMPLEMENTATION_STATUS["RS-SD-013"] == "partial"
    repository.add_received.assert_not_awaited()
    current_outcome = "REJECTED_BEFORE_ARCHIVE"
    assert case["expected_outcome"]["result"] != current_outcome
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
        assert set(case["recorded_evidence"]) == {"first_attempt", "replay"}, case_id
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
        assert case["expected_outcome"].keys() >= {"result", "reason_code"}, case_id
        assert case["expected_outcome"]["result"] in ALLOWED_OUTCOMES, case_id
        assert isinstance(case["replay_expectation"], str) and case["replay_expectation"].strip(), case_id
        assert set(case["replay_policy"]) == {"query", "effect", "session_progress"}, case_id
        assert case["replay_policy"]["query"] in {"NOT_APPLICABLE", "REUSE_RECORDED"}, case_id
        assert case["replay_policy"]["effect"] == "NO_NEW_EFFECT", case_id
        assert case["replay_policy"]["session_progress"] == "NO_PROGRESS", case_id

    cases = {case["case_id"]: case for case in _load_fixture()["cases"]}
    ng_pkg_id = cases["RS-SD-002"]["trigger"]["payload"]["data"]["PkgID"].upper()
    assert cases["RS-SD-002"]["trigger"]["decision_discriminator"]["pkg_ng_rule"] in ng_pkg_id
    assert "PkgID" not in cases["RS-SD-003"]["trigger"]["payload"]["data"]


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
            assert intent_kinds == ["BLOCK"], case_id
        else:
            assert outcome in {"REPLAY_ACCEPTED_NOOP", "ARCHIVED_EVIDENCE"} and not intents, case_id

        expected_query_policy = "REUSE_RECORDED" if case_id in QUERY_REPLAY_CASES else "NOT_APPLICABLE"
        assert case["replay_policy"]["query"] == expected_query_policy, case_id

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

    for case_id in ("RS-SD-001", "RS-SD-002", "RS-SD-003"):
        evidence = cases[case_id]["recorded_evidence"]
        assert {"normalized_input_snapshot", "payload_digest"} <= set(evidence["first_attempt"]), case_id
        assert "payload_digest" in evidence["replay"], case_id

    for case_id in ("RS-SD-004", "RS-SD-005", "RS-SD-006", "RS-SD-007", "RS-SD-010"):
        assert "command_result_snapshot" in cases[case_id]["recorded_evidence"]["first_attempt"], case_id
    assert "measurement_snapshot" in cases["RS-SD-005"]["recorded_evidence"]["first_attempt"]
    assert "measurement_validation_errors" in cases["RS-SD-007"]["recorded_evidence"]["first_attempt"]
    for case_id in ("RS-SD-004", "RS-SD-006"):
        evidence = cases[case_id]["recorded_evidence"]
        assert "wms_response_summary" in evidence["first_attempt"], case_id
        assert "original_wms_response_summary" in evidence["replay"], case_id

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
    assert {"command_identity", "deadline_snapshot", "timeout_event", "hold_reason", "payload_digest"} <= set(
        cases["RS-SD-009"]["recorded_evidence"]["first_attempt"]
    )
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
    assert metadata == {
        "contract_version": "rough-sorter-scan-decision.v1",
        "status": "Review",
        "owner": "业务 Owner（待明确）",
        "approved_by": "",
        "approved_at": "",
    }
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
        "本切片有四类合法终点：下一设备命令已持久化、稳定原因码 Hold、late/unknown callback 的 "
        "evidence-only 归档、replay no-op；后两类均不得推进当前 Session。"
    ) in content
