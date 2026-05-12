from dataclasses import dataclass

import pytest

from src.workline_runtime.material_flow_engine import MaterialFlowEngine
from src.workline_runtime.material_run import LifecycleState, MaterialRun
from src.workline_runtime.runtime_event import RuntimeEventType
from src.workline_runtime.runtime_intent import BlockScope, Destination, RuntimeIntent, RuntimeIntentKind


@dataclass
class Device:
    id: int
    device_role: str
    upstream_device_id: int | None = None
    device_status: str = "IDLE"
    current_command_id: int | None = None


def test_command_intent_moves_run_to_waiting_and_emits_events():
    source = Device(id=1, device_role="ENTRY_SCANNER")
    weigh = Device(id=2, device_role="WEIGH_SCALE", upstream_device_id=1)
    run = MaterialRun(
        run_code="MR-001",
        material_identity_key="pkg:PKG-001",
        workline_id=10,
        current_device_id=1,
        current_device_role="ENTRY_SCANNER",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    engine = MaterialFlowEngine(command_id_factory=lambda: 501)

    result = engine.apply(
        run=run,
        source_device=source,
        devices=[source, weigh],
        plugin_key="inbound_tote_qc",
        trace_id="trace-1",
        intent=RuntimeIntent.command(
            device_role="WEIGH_SCALE",
            action="WEIGH_TOTE",
            payload={"tote_id": "T-001"},
            destination=Destination.role("WEIGH_SCALE"),
            timeout_seconds=120,
        ),
    )

    assert result.command_id == 501
    assert result.blocker_id is None
    assert result.run is not run
    assert result.run.lifecycle_state == LifecycleState.WAITING
    assert result.run.current_device_id == 2
    assert result.run.current_device_role == "WEIGH_SCALE"
    assert result.run.current_action == "WEIGH_TOTE"
    assert result.run.awaiting_command_id == 501
    assert result.run.wait_reason == "COMMAND_RESULT"
    assert run.lifecycle_state == LifecycleState.ACTIVE
    assert run.current_device_id == 1
    assert run.awaiting_command_id is None
    assert [event.event_type for event in result.events] == [
        RuntimeEventType.PLUGIN_DECISION_MADE,
        RuntimeEventType.COMMAND_CREATED,
        RuntimeEventType.MATERIAL_ENTERED_DEVICE,
    ]
    assert result.events[0].payload_json["kind"] == RuntimeIntentKind.COMMAND.value
    assert result.events[0].payload_json["payload_json"] == {"tote_id": "T-001"}


def test_block_intent_moves_run_to_blocked_and_emits_block_event():
    source = Device(id=1, device_role="ENTRY_SCANNER")
    run = MaterialRun(
        run_code="MR-002",
        material_identity_key="pkg:PKG-002",
        workline_id=10,
        current_device_id=1,
        current_device_role="ENTRY_SCANNER",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    engine = MaterialFlowEngine(blocker_id_factory=lambda: 901)

    result = engine.apply(
        run=run,
        source_device=source,
        devices=[source],
        plugin_key="smt_classifier",
        trace_id="trace-2",
        intent=RuntimeIntent.block(
            scope=BlockScope.MATERIAL,
            reason_code="BARCODE_INVALID",
            message="条码无法识别",
            suggested_action="人工复核条码",
        ),
    )

    assert result.blocker_id == 901
    assert result.command_id is None
    assert result.run is not run
    assert result.run.lifecycle_state == LifecycleState.BLOCKED
    assert result.run.blocker_id == 901
    assert run.lifecycle_state == LifecycleState.ACTIVE
    assert run.blocker_id is None
    assert [event.event_type for event in result.events] == [
        RuntimeEventType.PLUGIN_DECISION_MADE,
        RuntimeEventType.PROCESS_BLOCKED,
    ]
    assert result.events[-1].event_type == RuntimeEventType.PROCESS_BLOCKED
    assert result.events[-1].reason_code == "BARCODE_INVALID"
    assert result.events[-1].payload_json == {
        "message": "条码无法识别",
        "suggested_action": "人工复核条码",
    }


def test_unsupported_intent_kind_raises_value_error():
    source = Device(id=1, device_role="ENTRY_SCANNER")
    run = MaterialRun(
        run_code="MR-003",
        material_identity_key="pkg:PKG-003",
        workline_id=10,
        current_device_id=1,
        current_device_role="ENTRY_SCANNER",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    engine = MaterialFlowEngine()

    with pytest.raises(ValueError, match="Unsupported intent kind: COMPLETE"):
        engine.apply(
            run=run,
            source_device=source,
            devices=[source],
            plugin_key="classifier",
            trace_id="trace-3",
            intent=RuntimeIntent(kind=RuntimeIntentKind.COMPLETE),
        )
