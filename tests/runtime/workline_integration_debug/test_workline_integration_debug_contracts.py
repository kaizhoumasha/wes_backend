from __future__ import annotations

import pytest

from src.app.workline_integration_debug.contracts import (
    IntegrationDebugPhase,
    IntegrationDebugProfile,
    IntegrationDebugRunStatus,
    IntegrationDebugScenario,
    profile_uses_real_ecs,
    profile_uses_real_transport,
    require_transition,
    wms_team_guidance,
)


def test_manual_outbound_debug_contract_is_a_closed_vocabulary() -> None:
    assert tuple(IntegrationDebugScenario) == (IntegrationDebugScenario.MANUAL_OUTBOUND_PICKING_V1,)
    assert tuple(IntegrationDebugProfile) == (
        IntegrationDebugProfile.CONTRACT_SIMULATION,
        IntegrationDebugProfile.DEVICE_INTEGRATION,
        IntegrationDebugProfile.FULL_SITE_INTEGRATION,
    )
    assert tuple(IntegrationDebugRunStatus) == (
        IntegrationDebugRunStatus.CREATED,
        IntegrationDebugRunStatus.WAITING_TASK,
        IntegrationDebugRunStatus.ACTIVE,
        IntegrationDebugRunStatus.WAITING_EXTERNAL,
        IntegrationDebugRunStatus.COMPLETED,
        IntegrationDebugRunStatus.NEEDS_ATTENTION,
        IntegrationDebugRunStatus.CLOSED_BY_OPERATOR,
    )
    assert tuple(IntegrationDebugPhase) == (
        IntegrationDebugPhase.BIND_TASK,
        IntegrationDebugPhase.TASK_PREPARE,
        IntegrationDebugPhase.PLAN_RECEIPT,
        IntegrationDebugPhase.RACK_TRANSPORT,
        IntegrationDebugPhase.RACK_ARRIVAL,
        IntegrationDebugPhase.BIN_INBOUND_BATCH,
        IntegrationDebugPhase.BIN_TRANSPORT,
        IntegrationDebugPhase.POINT1_ARRIVAL,
        IntegrationDebugPhase.POINT2_SCAN,
        IntegrationDebugPhase.WORK_ADMISSION,
        IntegrationDebugPhase.WORK_COMPLETION,
        IntegrationDebugPhase.COMPLETION_REPORT,
        IntegrationDebugPhase.POINT2_RELEASE,
        IntegrationDebugPhase.POINT3_ROUTE,
        IntegrationDebugPhase.RETURN_BUFFER,
        IntegrationDebugPhase.BIN_RETURN_BATCH,
        IntegrationDebugPhase.BIN_RETURN_TRANSPORT,
        IntegrationDebugPhase.RACK_DEPARTURE,
        IntegrationDebugPhase.TASK_COMPLETION,
        IntegrationDebugPhase.CLEANUP,
    )


@pytest.mark.parametrize(
    ("profile", "real_transport", "real_ecs"),
    [
        (IntegrationDebugProfile.CONTRACT_SIMULATION, False, False),
        (IntegrationDebugProfile.DEVICE_INTEGRATION, False, True),
        (IntegrationDebugProfile.FULL_SITE_INTEGRATION, True, True),
    ],
)
def test_profile_capabilities_are_fixed(
    profile: IntegrationDebugProfile,
    real_transport: bool,
    real_ecs: bool,
) -> None:
    assert profile_uses_real_transport(profile) is real_transport
    assert profile_uses_real_ecs(profile) is real_ecs


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (IntegrationDebugRunStatus.CREATED, IntegrationDebugRunStatus.WAITING_TASK),
        (IntegrationDebugRunStatus.WAITING_TASK, IntegrationDebugRunStatus.ACTIVE),
        (IntegrationDebugRunStatus.ACTIVE, IntegrationDebugRunStatus.WAITING_EXTERNAL),
        (IntegrationDebugRunStatus.WAITING_EXTERNAL, IntegrationDebugRunStatus.ACTIVE),
        (IntegrationDebugRunStatus.ACTIVE, IntegrationDebugRunStatus.COMPLETED),
        (IntegrationDebugRunStatus.COMPLETED, IntegrationDebugRunStatus.CLOSED_BY_OPERATOR),
        (IntegrationDebugRunStatus.NEEDS_ATTENTION, IntegrationDebugRunStatus.CLOSED_BY_OPERATOR),
    ],
)
def test_run_state_allows_only_reviewed_transitions(
    current: IntegrationDebugRunStatus,
    target: IntegrationDebugRunStatus,
) -> None:
    require_transition(current, target)


def test_run_state_rejects_automatic_recovery_from_attention() -> None:
    with pytest.raises(ValueError, match="NEEDS_ATTENTION"):
        require_transition(IntegrationDebugRunStatus.NEEDS_ATTENTION, IntegrationDebugRunStatus.ACTIVE)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("WAIT", "使用新的 operation_id 重新求值"),
        ("UNAVAILABLE", "保留原 operation_id"),
        ("CONFLICT", "核对同一 operation_id 的完整请求内容"),
        ("RECEIVED", "只证明 WES 已可靠接收 Evidence"),
    ],
)
def test_wms_guidance_is_concrete_for_csharp_team(code: str, expected: str) -> None:
    guidance = wms_team_guidance(code)

    assert expected in guidance
    assert "C#" in guidance
    assert "HTTP" in guidance
