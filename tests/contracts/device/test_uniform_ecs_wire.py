"""统一 ECS command/status wire 的可观察合同。"""

from __future__ import annotations

import json
from datetime import datetime

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


def _response(
    status: int, payload: dict[str, object], *, headers: tuple[tuple[str, str], ...] = ()
) -> OutboundHttpResult:
    return OutboundHttpResult(
        delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
        status_code=status,
        response_headers=(("content-type", "application/json"), *headers),
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
async def test_submit_fails_closed_at_transport_boundary_after_deadline() -> None:
    transport = FakeOutboundHttpTransport([])
    adapter = EcsAdapter(transport, clock=lambda: datetime(2026, 8, 13, 0, 0, 1))

    result = await adapter.submit_command(
        device_code="ARM-01",
        command_code="CMD-001",
        contract_key="arm.pick",
        contract_version="2.0",
        task_type="PICK",
        timestamp_ms=1_786_032_000_000,
        params={},
        trace_id=None,
        deadline_at=datetime(2026, 8, 13, 0, 0, 1),
    )

    assert result.disposition is EcsSubmitDisposition.RETRYABLE_NOT_ACCEPTED
    assert transport.requests == []


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
                headers=(("Cache-Control", "no-store"),),
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
async def test_status_without_no_store_fails_closed() -> None:
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
    with pytest.raises(EcsStatusUnavailableError):
        await EcsAdapter(transport).fetch_status("ARM-01")


@pytest.mark.asyncio
@pytest.mark.parametrize("trace_id", ["@", "T" * 121])
async def test_invalid_response_trace_id_enters_reconciliation(trace_id: str) -> None:
    transport = FakeOutboundHttpTransport([_response(200, {"code": 200, "message": "ACCEPTED", "trace_id": trace_id})])
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
async def test_ack_without_json_media_type_enters_reconciliation() -> None:
    result = await EcsAdapter(
        FakeOutboundHttpTransport(
            [
                OutboundHttpResult(
                    delivery_state=OutboundHttpDeliveryState.RESPONSE_RECEIVED,
                    status_code=200,
                    decoded_body=b'{"code":200,"message":"ACCEPTED"}',
                )
            ]
        )
    ).submit_command(
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
@pytest.mark.parametrize("status_code", [429, 503])
async def test_explicit_not_accepted_status_can_retry_same_identity(status_code: int) -> None:
    message = "CAPACITY_EXCEEDED" if status_code == 429 else "TEMPORARILY_UNAVAILABLE"
    headers = (("Retry-After", "5"),) if status_code == 429 else ()
    transport = FakeOutboundHttpTransport(
        [_response(status_code, {"code": status_code, "message": message}, headers=headers)]
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

    assert result.disposition is EcsSubmitDisposition.RETRYABLE_NOT_ACCEPTED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "wrong_message", "headers"),
    [
        (429, "TEMPORARILY_UNAVAILABLE", (("Retry-After", "5"),)),
        (503, "CAPACITY_EXCEEDED", ()),
    ],
)
async def test_unknown_status_message_combination_enters_reconciliation(status_code, wrong_message, headers) -> None:
    transport = FakeOutboundHttpTransport(
        [_response(status_code, {"code": status_code, "message": wrong_message}, headers=headers)]
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
async def test_capacity_response_requires_and_parses_retry_after() -> None:
    accepted = FakeOutboundHttpTransport(
        [_response(429, {"code": 429, "message": "CAPACITY_EXCEEDED"}, headers=(("Retry-After", "60"),))]
    )
    missing = FakeOutboundHttpTransport([_response(429, {"code": 429, "message": "CAPACITY_EXCEEDED"})])

    values = {
        "device_code": "ARM-01",
        "command_code": "CMD-001",
        "contract_key": "arm.pick",
        "contract_version": "2.0",
        "task_type": "PICK",
        "timestamp_ms": 1_786_032_000_000,
        "params": {},
        "trace_id": None,
    }
    result = await EcsAdapter(accepted).submit_command(**values)
    malformed = await EcsAdapter(missing).submit_command(**values)

    assert result.disposition is EcsSubmitDisposition.RETRYABLE_NOT_ACCEPTED
    assert result.retry_after_seconds == 60
    assert malformed.disposition is EcsSubmitDisposition.RECONCILING


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
    transport = FakeOutboundHttpTransport(
        [_response(200, {**valid, "status": "PAUSED"}, headers=(("Cache-Control", "no-store"),))]
    )

    with pytest.raises(EcsStatusUnavailableError):
        await EcsAdapter(transport).fetch_status("ARM-01")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        {"current_command_code": "__MISSING__"},
        {"timestamp": 2**63},
        {"timestamp": "1786032000000"},
    ],
)
async def test_status_rejects_incomplete_or_non_int64_wire(invalid: dict) -> None:
    payload = {
        "device_code": "ARM-01",
        "contract_key": "arm.pick",
        "contract_version": "2.0",
        "mode": "AUTO",
        "status": "IDLE",
        "current_command_code": None,
        "error_detail": None,
        "timestamp": 1_786_032_000_000,
    }
    if invalid.get("current_command_code") == "__MISSING__":
        payload.pop("current_command_code")
    else:
        payload.update(invalid)
    transport = FakeOutboundHttpTransport([_response(200, payload, headers=(("Cache-Control", "no-store"),))])
    with pytest.raises(EcsStatusUnavailableError):
        await EcsAdapter(transport).fetch_status("ARM-01")
