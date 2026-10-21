"""已确认机械臂包络映射；完整参数仅为显式测试输入，不能当设备合同默认值。"""

from datetime import datetime, timedelta
from types import SimpleNamespace as N
from unittest.mock import AsyncMock

import pytest
from automatic_picking.application.picking_device_ports import PickingArmCommandMapper
from automatic_picking.handlers.source_authorization import WorkSourceFact
from wes_plugin_sdk import PickingCellSource, PickingTargetRackSlot

NOW = datetime(2026, 10, 22)
SOURCE = PickingCellSource("CELL-7", 7, 2, 1, 20.0, 15.0)
TARGET = PickingTargetRackSlot("TARGET", "A", "S1", 1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["source", "put"])
async def test_mapping_uses_current_role_binding_identity_and_configured_deadline(role):
    line = N(
        id=1,
        is_active=True,
        config={
            "device_bindings": {
                "SCAN1": "S1",
                "SCAN2": "S2",
                "SCAN3": "S3",
                "SCAN4": "S4",
                "SOURCE_ARM": "ARM-X",
                "TARGET_ARM": "ARM-Y",
            }
        },
    )
    db = N(get=AsyncMock(return_value=line))
    binding = N(contract_key="third_party_integration", contract_version="1.1", command_timeout_ms=1234)
    worklines = N(
        get_for_authority_update=AsyncMock(return_value=line),
        get_binding_for_command_creation=AsyncMock(return_value=binding),
    )
    parameters = {"source": {"explicit_test_location": "source"}, "target": {"explicit_test_location": "target"}}
    provider = N(
        source_in_session=AsyncMock(return_value=parameters), put_in_session=AsyncMock(return_value=parameters)
    )
    mapper = PickingArmCommandMapper(provider, worklines=worklines, clock=lambda: NOW)
    if role == "source":
        work = WorkSourceFact(11, 1, 31, SOURCE, None, True, None, True, True, True)
        request = await mapper.source_request(db, work=work, pickup_id="P1")
        assert request.execution_ref_id == "P1" and request.material_execution_id is None
    else:
        request = await mapper.put_request(
            db, workline_id=1, pickup_id="P1", material_execution_id=22, target=TARGET, now=NOW
        )
        assert request.execution_ref_id == "put:P1" and request.material_execution_id == 22
    assert request.device_code == ("ARM-X" if role == "source" else "ARM-Y")
    assert request.task_type == "PICK_AND_PUT"
    assert request.params == parameters
    assert request.contract_key == binding.contract_key and request.contract_version == binding.contract_version
    assert request.deadline_at == NOW + timedelta(milliseconds=1234)
    assert request.execution_ref_type == "WORKLINE_BUSINESS"


@pytest.mark.asyncio
async def test_missing_complete_parameters_never_manufactures_command_defaults():
    mapper = PickingArmCommandMapper(N(source_in_session=AsyncMock(return_value=None)))
    work = WorkSourceFact(11, 1, 31, SOURCE, None, True, None, True, True, True)
    assert await mapper.source_request(N(), work=work, pickup_id="P1") is None
