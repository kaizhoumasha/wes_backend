"""RuntimeInbox 非设备通用处理分支回归。"""

from types import SimpleNamespace

import pytest

from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_orchestrator_bridge import (
    _project_replay_request,
)
from src.app.runtime.orchestration.services.runtime_inbox.runtime_inbox_validation_service import (
    RuntimeInboxValidationService,
)


def test_replay_uses_validated_external_envelope() -> None:
    inbox = SimpleNamespace(id=91)
    envelope = {
        "original_kind": "EXTERNAL_HTTP",
        "original_payload": {"data": {"barcode": "BOX-1"}},
        "original_provider_code": "WMS",
        "original_event_type": "WMS_EFFECT_CALLBACK",
        "original_source_event_id": "source-1",
        "original_payload_hash": "sha256:source",
        "original_workline_id": 3,
        "original_workline_session_id": 5,
        "original_execution_session_id": None,
        "original_correlation_id": "corr-1",
        "original_trace_id": "trace-1",
        "original_event_id": "event-1",
        "original_causation_id": None,
    }

    replay = _project_replay_request(inbox, validated_source=SimpleNamespace(envelope=envelope))

    assert replay.is_manual_replay is True
    assert replay.kind == "EXTERNAL_HTTP"
    assert replay.payload_json == {"data": {"barcode": "BOX-1"}}
    assert replay.workline_session_id == 5


@pytest.mark.asyncio
async def test_scan_entry_requires_generic_barcode_evidence() -> None:
    inbox = SimpleNamespace(id=7, payload_json={"data": {}})

    outcome = await RuntimeInboxValidationService().pre_gate(
        object(), inbox=inbox, resolved_event_type="SCAN_COMPLETED", workline=None
    )

    assert outcome.proceed_to_orchestrator is False
    assert outcome.error_code is not None
