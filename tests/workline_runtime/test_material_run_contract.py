from src.workline_runtime.material_run import LifecycleState, MaterialRun


def test_material_run_tracks_current_device_and_action():
    run = MaterialRun(
        run_code="MR-001",
        material_identity_key="pkg:PKG-001",
        workline_id=10,
        current_device_id=21,
        current_device_role="ENTRY_SCANNER",
        current_action="SCAN_COMPLETED",
        lifecycle_state=LifecycleState.ACTIVE,
    )

    assert run.material_identity_key == "pkg:PKG-001"
    assert run.current_device_id == 21
    assert run.current_device_role == "ENTRY_SCANNER"
    assert run.current_action == "SCAN_COMPLETED"
    assert run.lifecycle_state == LifecycleState.ACTIVE


def test_material_run_can_record_wait_anchor():
    run = MaterialRun(
        run_code="MR-002",
        material_identity_key="pkg:PKG-002",
        workline_id=10,
        lifecycle_state=LifecycleState.WAITING,
        awaiting_command_id=300,
        wait_reason="COMMAND_RESULT",
    )

    assert run.awaiting_command_id == 300
    assert run.wait_reason == "COMMAND_RESULT"
