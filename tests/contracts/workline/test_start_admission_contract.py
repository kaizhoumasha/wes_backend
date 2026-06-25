"""BC-01 Start Admission 行为契约。

验收: manifest 有效/设备角色满足/外部 port 可用/active projection 无阻塞冲突才接受;
任一不满足即拒绝且不创建 session/intent。必须通过; 不可 skip。
"""

from __future__ import annotations

from tests.support.workline_contracts import evaluate_start_admission


def test_admission_rejects_invalid_manifest():
    result = evaluate_start_admission(
        manifest_valid=False,
        device_roles_satisfied=True,
        external_ports_available=True,
        projection_blocked=False,
    )
    assert not result.accepted
    assert result.reason_code == "INVALID_MANIFEST"
    assert not result.session_created
    assert not result.intent_created


def test_admission_rejects_unsatisfied_device_roles():
    result = evaluate_start_admission(
        manifest_valid=True,
        device_roles_satisfied=False,
        external_ports_available=True,
        projection_blocked=False,
    )
    assert not result.accepted
    assert result.reason_code == "DEVICE_ROLE_UNSATISFIED"


def test_admission_rejects_unavailable_external_ports():
    result = evaluate_start_admission(
        manifest_valid=True,
        device_roles_satisfied=True,
        external_ports_available=False,
        projection_blocked=False,
    )
    assert not result.accepted
    assert result.reason_code == "EXTERNAL_PORT_UNAVAILABLE"


def test_admission_rejects_blocked_projection():
    result = evaluate_start_admission(
        manifest_valid=True,
        device_roles_satisfied=True,
        external_ports_available=True,
        projection_blocked=True,
    )
    assert not result.accepted
    assert result.reason_code == "PROJECTION_BLOCKED"


def test_admission_accepts_when_all_preconditions_met():
    result = evaluate_start_admission(
        manifest_valid=True,
        device_roles_satisfied=True,
        external_ports_available=True,
        projection_blocked=False,
    )
    assert result.accepted
    assert result.reason_code is None
    assert result.session_created
    assert result.intent_created
