"""RuntimeInbox 通用处理分支回归。"""

from types import SimpleNamespace

import pytest

from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    _project_replay_request,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_validation_service import (
    RuntimeInboxValidationService,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_writeback_service import (
    _is_late_or_duplicate_command_result_for_session,
)


def test_replay_uses_validated_original_envelope() -> None:
    inbox = SimpleNamespace(id=91)
    envelope = {
        "original_kind": "DEVICE_EVENT",
        "original_payload": {"data": {"barcode": "BOX-1"}},
        "original_provider_code": "ecs",
        "original_event_type": "SCAN_COMPLETED",
        "original_source_event_id": "source-1",
        "original_payload_hash": "sha256:source",
        "original_workline_id": 3,
        "original_device_id": 4,
        "original_command_id": None,
        "original_workline_session_id": 5,
        "original_execution_session_id": None,
        "original_correlation_id": "corr-1",
        "original_trace_id": "trace-1",
        "original_event_id": "event-1",
        "original_causation_id": None,
    }

    replay = _project_replay_request(inbox, validated_source=SimpleNamespace(envelope=envelope))

    assert replay.is_manual_replay is True
    assert replay.kind == "DEVICE_EVENT"
    assert replay.payload_json == {"data": {"barcode": "BOX-1"}}
    assert replay.workline_session_id == 5


def test_timer_timeout_is_a_generic_control_route() -> None:
    outcome = RuntimeInboxValidationService().classify_estop_or_timer(
        resolved_event_type="anything", inbox_kind="TIMER_TIMEOUT"
    )

    assert outcome.timer_timeout_event is True
    assert outcome.proceed_to_orchestrator is False


@pytest.mark.asyncio
async def test_scan_entry_requires_generic_barcode_evidence() -> None:
    inbox = SimpleNamespace(id=7, payload_json={"data": {}})

    outcome = await RuntimeInboxValidationService().pre_gate(
        object(), inbox=inbox, resolved_event_type="SCAN_COMPLETED", workline=None
    )

    assert outcome.proceed_to_orchestrator is False
    assert outcome.error_code is not None


def test_late_command_result_is_archived_without_business_handler() -> None:
    inbox = SimpleNamespace(kind="COMMAND_RESULT")
    session = SimpleNamespace(status="COMPLETED", awaiting_device_command_code="command-1")
    command = SimpleNamespace(command_code="command-1", status="COMPLETED")

    assert _is_late_or_duplicate_command_result_for_session(
        inbox=inbox,
        payload={"command_code": "command-1"},
        session=session,
        command=command,
    )
