from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from wes_plugin_sdk import TransportRackPosition, wms_operations

from src.app.wms_adapter.outbound_picking.departure_typed import encode_request
from src.app.wms_integration.outbound_picking.services.rack_departure_owner import RackDepartureOwnerService
from src.utils.timezone import timezone


@pytest.mark.asyncio
async def test_real_rack_departure_does_not_require_integration_run():
    worklines = SimpleNamespace(get_for_update=AsyncMock(return_value=SimpleNamespace(is_active=True)))
    tasks = SimpleNamespace(get_by_task_id_for_update=AsyncMock())
    payload = encode_request(
        wms_operations.outbound_rack_departure_decide(
            operation_id="0199e9f0-0000-7000-8000-000000000001",
            task_id=None,
            rack_id="RACK-1",
            current_location=TransportRackPosition("OUTLET"),
            current_face="90",
        ),
        timestamp=int(timezone.now_utc().timestamp() * 1000),
    )

    assert await RackDepartureOwnerService(worklines=worklines, tasks=tasks).validate_owner(
        object(), workline_id=7, request_payload=payload
    )
    tasks.get_by_task_id_for_update.assert_not_called()
