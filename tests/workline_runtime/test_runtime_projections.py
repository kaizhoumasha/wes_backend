from src.workline_runtime.projections import ProjectionState
from src.workline_runtime.runtime_event import RuntimeEvent, RuntimeEventType


def test_projection_tracks_material_current_device():
    state = ProjectionState()

    state.apply(
        RuntimeEvent(
            event_type=RuntimeEventType.MATERIAL_ENTERED_DEVICE,
            trace_id="trace-1",
            material_identity_key="pkg:PKG-001",
            workline_id=10,
            device_id=21,
            device_role="WEIGH_SCALE",
            action="WEIGH_TOTE",
        )
    )

    material = state.materials["pkg:PKG-001"]
    assert material.current_device_id == 21
    assert material.current_device_role == "WEIGH_SCALE"
    assert material.current_action == "WEIGH_TOTE"


def test_projection_tracks_blocker_count_by_line():
    state = ProjectionState()

    state.apply(
        RuntimeEvent(
            event_type=RuntimeEventType.PROCESS_BLOCKED,
            trace_id="trace-2",
            material_identity_key="pkg:PKG-002",
            workline_id=10,
            device_id=22,
            device_role="DIVERT_CONVEYOR",
            reason_code="DEVICE_TIMEOUT",
        )
    )

    assert state.lines[10].blocked_count == 1


def test_projection_marks_blocked_material():
    state = ProjectionState()

    state.apply(
        RuntimeEvent(
            event_type=RuntimeEventType.PROCESS_BLOCKED,
            trace_id="trace-3",
            material_identity_key="pkg:PKG-003",
            workline_id=11,
            reason_code="QUALITY_HOLD",
        )
    )

    material = state.materials["pkg:PKG-003"]
    assert material.blocked is True
    assert material.block_reason == "QUALITY_HOLD"


def test_projection_does_not_create_material_for_line_only_event():
    state = ProjectionState()

    state.apply(
        RuntimeEvent(
            event_type=RuntimeEventType.PROCESS_BLOCKED,
            trace_id="trace-4",
            workline_id=12,
            reason_code="LINE_STOPPED",
        )
    )

    assert state.lines[12].blocked_count == 1
    assert state.materials == {}
