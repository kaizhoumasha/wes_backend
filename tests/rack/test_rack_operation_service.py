"""旧 Rack operation producer 的 fail-closed 回归。"""

from __future__ import annotations

import pytest

from src.app.rack.services import RackOperationService
from src.app.rack.services.gateway import WmsRcsRackGateway
from src.app.wms_integration.services.transport_contract import WmsTransportMigrationRequiredError


@pytest.mark.asyncio
async def test_rack_operation_rejects_before_persisting_legacy_transport() -> None:
    with pytest.raises(RuntimeError, match="T5 dispatcher is not implemented"):
        await RackOperationService().request_operation_tasks(
            None,
            operation_key="rack-removed-001",
            operation_type="RACK_TRANSPORT",
            session=None,
            trace_id="trace-rack-removed",
            task_specs=[],
        )


def test_rack_gateway_cannot_build_removed_transport_envelope() -> None:
    with pytest.raises(WmsTransportMigrationRequiredError, match="T5 dispatcher is not implemented"):
        WmsRcsRackGateway().build_task_envelope(
            operation_key="rack-removed-001",
            operation_type="RACK_TRANSPORT",
            sequence_no=1,
            task_type="MOVE_RACK",
            trace_id="trace-rack-removed",
            workline_id=None,
            workline_code=None,
            material_session_id=None,
            rack_code="RACK-001",
            rack_kind="SINGLE_LAYER",
            source_position_code="SOURCE",
            target_position_code="TARGET",
            target_position_role="WORK",
        )
