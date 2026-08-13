"""统一 ECS command/status wire 的可观察合同。"""

from __future__ import annotations

import json

import pytest

from src.app.device.contracts import EcsSubmitDisposition
from src.app.device.ecs_adapter import EcsAdapter, EcsStatusUnavailableError
from src.core.outbound_http import (
    OutboundHttpDeliveryState,
    OutboundHttpFailureKind,
    OutboundHttpMethod,
    OutboundHttpRequest,
    OutboundHttpResult,
)


class FakeOutboundHttpTransport:
    def __init__(self, results: list[OutboundHttpResult]) -> None:
        self.results = list(results)
        self.requests: list[OutboundHttpRequest] = []
        self.closed = False

    async def send(self, request: OutboundHttpRequest) -> OutboundHttpResult:
        self.requests.append(request)
        return self.results.pop(0)

    async def aclose(self) -> None:
        self.closed = True


def _response(status: int, payload: dict[str, object]) -> OutboundHttpResult:
    return OutboundHttpResult(
        delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
        status_code=status,
        response_headers=(("content-type", "application/json"),),
        decoded_body=json.dumps(payload, separators=(",", ":")).encode(),
    )


@pytest.mark.asyncio
async def test_submit_uses_fixed_path_closed_envelope_and_no_auth_header() -> None:
    transport = FakeOutboundHttpTransport([_response(200, {"code": 200, "message": "ACCEPTED", "trace_id": "T-1"})])
    adapter = EcsAdapter(transport)

    result = await adapter.submit_command(
        device_code="ARM-01",
        command_code="CMD-001",
        contract_key="arm.pick",
        contract_version="2.0",
        task_type="PICK",
        timestamp_ms=1_786_032_000_000,
        params={"source_location": "STATION-A", "target_location": "STATION-B"},
        trace_id="T-1",
    )

    assert result.disposition is EcsSubmitDisposition.ACKNOWLEDGED
    request = transport.requests[0]
    assert request.method is OutboundHttpMethod.POST
    assert request.path == "/api/v1/device/command"
    assert request.query == ()
    assert request.headers == (("content-type", "application/json"),)
    assert json.loads(request.body) == {
        "device_code": "ARM-01",
        "command_code": "CMD-001",
        "contract_key": "arm.pick",
        "contract_version": "2.0",
        "task_type": "PICK",
        "timestamp": 1_786_032_000_000,
        "params": {"source_location": "STATION-A", "target_location": "STATION-B"},
        "trace_id": "T-1",
    }
    assert request.response_limits.max_wire_bytes == 256 * 1024
    assert request.response_limits.max_decoded_bytes == 256 * 1024


@pytest.mark.asyncio
async def test_status_uses_fixed_get_path_and_parses_closed_state() -> None:
    transport = FakeOutboundHttpTransport(
        [
            _response(
                200,
                {
                    "device_code": "ARM-01",
                    "contract_key": "arm.pick",
                    "contract_version": "2.0",
                    "mode": "AUTO",
                    "status": "IDLE",
                    "current_command_code": None,
                    "error_detail": None,
                    "timestamp": 1_786_032_000_000,
                },
            )
        ]
    )

    status = await EcsAdapter(transport).fetch_status("ARM-01")

    request = transport.requests[0]
    assert request.method is OutboundHttpMethod.GET
    assert request.path == "/api/v1/device/status"
    assert request.query == (("device_code", "ARM-01"),)
    assert request.headers == ()
    assert status.mode.value == "AUTO"
    assert status.status.value == "IDLE"
    assert status.current_command_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [429, 503])
async def test_explicit_not_accepted_status_can_retry_same_identity(status_code: int) -> None:
    message = "CAPACITY_EXCEEDED" if status_code == 429 else "TEMPORARILY_UNAVAILABLE"
    transport = FakeOutboundHttpTransport([_response(status_code, {"code": status_code, "message": message})])

    result = await EcsAdapter(transport).submit_command(
        device_code="ARM-01",
        command_code="CMD-001",
        contract_key="arm.pick",
        contract_version="2.0",
        task_type="PICK",
        timestamp_ms=1_786_032_000_000,
        params={},
        trace_id=None,
    )

    assert result.disposition is EcsSubmitDisposition.RETRYABLE_NOT_ACCEPTED


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [500, 502, 504])
async def test_ambiguous_server_failure_enters_reconciliation(status_code: int) -> None:
    transport = FakeOutboundHttpTransport(
        [_response(status_code, {"code": status_code, "message": "TEMPORARILY_UNAVAILABLE"})]
    )

    result = await EcsAdapter(transport).submit_command(
        device_code="ARM-01",
        command_code="CMD-001",
        contract_key="arm.pick",
        contract_version="2.0",
        task_type="PICK",
        timestamp_ms=1_786_032_000_000,
        params={},
        trace_id=None,
    )

    assert result.disposition is EcsSubmitDisposition.RECONCILING


@pytest.mark.asyncio
async def test_delivery_unknown_never_auto_retries() -> None:
    transport = FakeOutboundHttpTransport(
        [
            OutboundHttpResult(
                delivery_state=OutboundHttpDeliveryState.DELIVERY_UNKNOWN,
                failure_kind=OutboundHttpFailureKind.READ_TIMEOUT,
            )
        ]
    )

    result = await EcsAdapter(transport).submit_command(
        device_code="ARM-01",
        command_code="CMD-001",
        contract_key="arm.pick",
        contract_version="2.0",
        task_type="PICK",
        timestamp_ms=1_786_032_000_000,
        params={},
        trace_id=None,
    )

    assert result.disposition is EcsSubmitDisposition.RECONCILING


@pytest.mark.asyncio
async def test_malformed_or_unknown_status_fails_closed() -> None:
    valid = {
        "device_code": "ARM-01",
        "contract_key": "arm.pick",
        "contract_version": "2.0",
        "mode": "AUTO",
        "status": "IDLE",
        "current_command_code": None,
        "error_detail": None,
        "timestamp": 1_786_032_000_000,
    }
    transport = FakeOutboundHttpTransport([_response(200, {**valid, "status": "PAUSED"})])

    with pytest.raises(EcsStatusUnavailableError):
        await EcsAdapter(transport).fetch_status("ARM-01")
