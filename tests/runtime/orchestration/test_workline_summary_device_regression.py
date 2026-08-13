"""静态 Device 主数据不得被运行态汇总当作旧投影读取。"""

from types import SimpleNamespace

from src.app.device.models.device import Device
from src.app.runtime.orchestration.services.query.runtime_query_service import RuntimeQueryService


def test_workline_summary_does_not_read_retired_device_runtime_fields() -> None:
    service = object.__new__(RuntimeQueryService)
    workline = SimpleNamespace(
        id=1,
        line_code="LINE-01",
        line_name="Line 01",
        line_type="AUTO",
        zone_name=None,
        is_active=True,
        run_mode="AUTO",
    )
    snapshot = SimpleNamespace(
        runtime_status="READY",
        active_safety_incident_id=None,
        stopped_at=None,
        stopped_reason=None,
        resumed_at=None,
    )
    device = Device(device_code="ARM-01", device_name="Arm", device_role="ROBOT_ARM")

    summary = service._build_workline_summary(workline, [device], [], snapshot)

    assert summary.device_count == 1
    assert summary.error_device_count == 0
    assert summary.offline_device_count == 0
    assert summary.maintenance_device_count == 0
