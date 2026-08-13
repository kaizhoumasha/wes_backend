"""ECS 非对象 JSON 必须归类为不可信响应，而不是逃逸异常。"""

import pytest

from src.app.device.ecs_adapter import EcsAdapter, EcsStatusUnavailableError
from src.core.outbound_http import OutboundHttpDeliveryState, OutboundHttpResult


class StubTransport:
    async def send(self, *_args, **_kwargs):
        return OutboundHttpResult(
            delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
            status_code=200,
            decoded_body=b"[]",
            response_headers=(("content-type", "application/json"), ("cache-control", "no-store")),
        )


@pytest.mark.asyncio
async def test_submit_non_object_json_enters_reconciling() -> None:
    adapter = EcsAdapter(StubTransport())

    result = await adapter.submit_command(
        device_code="ARM-01",
        command_code="CMD-1",
        contract_key="ecs.uniform",
        contract_version="1.0",
        task_type="MOVE",
        timestamp_ms=1,
        params={},
        trace_id=None,
    )

    assert result.disposition.value == "RECONCILING"


@pytest.mark.asyncio
async def test_status_non_object_json_is_wrapped_as_unavailable() -> None:
    adapter = EcsAdapter(StubTransport())

    with pytest.raises(EcsStatusUnavailableError):
        await adapter.fetch_status("ARM-01")
