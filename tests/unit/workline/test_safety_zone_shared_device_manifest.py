"""CEO-012: WorkLine SafetyZone / shared-device manifest schema validator。

主计划 §9.6 + §3.2：
- DeviceRequirement 加 required 标记（required=true 不可用时 WorkLine 不启动；
  optional=true 可启动但 capability 从候选剔除）
- SafetyZone 声明物理安全边界，区域内设备异常时按 isolation_policy 隔离
- SharedDevice 声明跨线/跨 capability 共享设备的影响范围

本测试验证 manifest schema 字段完整性 + 默认值 + validator 行为。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.app.workline.models.workline import (
    DeviceRequirement,
    SafetyZone,
    SharedDevice,
    WorkLinePluginManifestSummary,
)


def test_device_requirement_defaults_required_true():
    """DeviceRequirement 默认 required=True（主计划 §9.6：未声明视为必需）。"""
    req = DeviceRequirement(role="SCAN", min_count=1)
    assert req.required is True


def test_device_requirement_optional_marked():
    """optional 设备显式标记 required=False。"""
    req = DeviceRequirement(role="PRINTER", min_count=0, required=False)
    assert req.required is False


def test_safety_zone_defaults_hold_zone():
    """SafetyZone 默认 isolation_policy=HOLD_ZONE（仅 hold 区域内，不污染整线）。"""
    zone = SafetyZone(zone_code="ZONE-A")
    assert zone.isolation_policy == "HOLD_ZONE"
    assert zone.device_codes == []
    assert zone.roles == []


def test_safety_zone_hold_workline_policy():
    """SafetyZone 可声明 HOLD_WORKLINE（异常时 hold 整线）。"""
    zone = SafetyZone(
        zone_code="ZONE-CRITICAL",
        device_codes=["ESTOP-01", "LIGHT_CURTAIN-01"],
        isolation_policy="HOLD_WORKLINE",
    )
    assert zone.isolation_policy == "HOLD_WORKLINE"
    assert "ESTOP-01" in zone.device_codes


def test_shared_device_impact_scope_required():
    """SharedDevice 必须声明 impact_scope（WORKLINE/SESSION/WORK_ITEM）。"""
    dev = SharedDevice(
        device_code="CONVEYOR-01",
        role="CONVEYOR",
        impact_scope="WORKLINE",
    )
    assert dev.impact_scope == "WORKLINE"
    assert dev.shared_by == []


def test_shared_device_cross_workline_shared_by():
    """SharedDevice 声明跨 WorkLine 共享。"""
    dev = SharedDevice(
        device_code="ARM-01",
        role="PICK_ARM",
        shared_by=["WL-ROUGH-SORTER", "WL-SMT-SORTING"],
        impact_scope="SESSION",
    )
    assert len(dev.shared_by) == 2


def test_manifest_summary_supports_safety_zones_and_shared_devices():
    """WorkLinePluginManifestSummary 含 safety_zones + shared_devices 字段。"""
    summary = WorkLinePluginManifestSummary(
        plugin_key="rough_sorter",
        contract_version="2026-06-26",
        topology=__import__(
            "src.app.workline.models.workline",
            fromlist=["TopologySpec"],
        ).TopologySpec(nodes=[], edges=[]),
        safety_zones=[
            SafetyZone(zone_code="ZONE-A", roles=["SCAN"]),
        ],
        shared_devices=[
            SharedDevice(device_code="CONVEYOR-01", role="CONVEYOR", impact_scope="WORKLINE"),
        ],
    )
    assert len(summary.safety_zones) == 1
    assert summary.safety_zones[0].zone_code == "ZONE-A"
    assert len(summary.shared_devices) == 1
    assert summary.shared_devices[0].device_code == "CONVEYOR-01"


def test_manifest_summary_safety_zones_default_empty():
    """未声明 safety_zones/shared_devices 时默认空列表（向后兼容）。"""
    summary = WorkLinePluginManifestSummary(
        plugin_key="rough_sorter",
        contract_version="2026-06-26",
        topology=__import__(
            "src.app.workline.models.workline",
            fromlist=["TopologySpec"],
        ).TopologySpec(nodes=[], edges=[]),
    )
    assert summary.safety_zones == []
    assert summary.shared_devices == []


def test_safety_zone_missing_zone_code_rejected():
    """SafetyZone zone_code 必填。"""
    with pytest.raises(ValidationError):
        SafetyZone()  # type: ignore[call-arg]


def test_shared_device_missing_impact_scope_rejected():
    """SharedDevice impact_scope 必填。"""
    with pytest.raises(ValidationError):
        SharedDevice(device_code="X", role="Y")  # type: ignore[call-arg]
