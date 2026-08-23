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


def _status_entry(
    device_code: str = "ARM-01", *, state_device_code: str | None = None, **state_overrides: object
) -> dict[str, object]:
    state: dict[str, object] = {
        "device_code": state_device_code or device_code,
        "mode": "AUTO",
        "status": "IDLE",
        "is_online": True,
        "current_command_code": None,
        "scenario": "success",
        "updated_at": 1_786_032_000_000,
    }
    state.update(state_overrides)
    return {
        "device": {
            "device_code": device_code,
            "device_name": "机械臂 1",
            "device_type": "ROBOTIC_ARM",
            "role": "PLACEMENT_DEVICE",
            "supported_commands": ["PICK"],
            "supported_events": [],
        },
        "state": state,
    }


@pytest.mark.asyncio
async def test_submit_uses_fixed_path_closed_envelope_and_no_auth_header() -> None:
    transport = FakeOutboundHttpTransport([_response(200, {"code": 200, "message": "Accepted", "trace_id": "T-1"})])
    adapter = EcsAdapter(transport)

    result = await adapter.submit_command(
        device_code="ARM-01",
        command_code="CMD-001",
        task_type="PICK",
        priority=1,
        timeout_ms=30_000,
        timestamp=1_786_032_000_000,
        params={"source_location": "STATION-A", "target_location": "STATION-B"},
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
        "task_type": "PICK",
        "priority": 1,
        "timeout": 30_000,
        "timestamp": 1_786_032_000_000,
        "params": {"source_location": "STATION-A", "target_location": "STATION-B"},
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
        task_type="PICK",
        priority=1,
        timeout_ms=30_000,
        timestamp=1_786_032_000_000,
        params={},
        deadline_at=datetime(2026, 8, 13, 0, 0, 1),
    )

    assert result.disposition is EcsSubmitDisposition.RETRYABLE_NOT_ACCEPTED
    assert transport.requests == []


@pytest.mark.asyncio
async def test_status_uses_fixed_get_path_and_parses_closed_state() -> None:
    transport = FakeOutboundHttpTransport([_response(200, {"devices": [_status_entry()]})])

    status = await EcsAdapter(transport).fetch_status("ARM-01")

    request = transport.requests[0]
    assert request.method is OutboundHttpMethod.GET
    assert request.path == "/api/v1/device/status"
    assert request.query == (("device_code", "ARM-01"),)
    assert request.headers == ()
    assert status.device.device_code == "ARM-01"
    assert status.device.supported_commands == ("PICK",)
    assert status.state.mode.value == "AUTO"
    assert status.state.status.value == "IDLE"
    assert status.state.is_online is True
    assert status.state.current_command_code is None


@pytest.mark.asyncio
async def test_statuses_without_device_filter_preserve_wire_order() -> None:
    transport = FakeOutboundHttpTransport(
        [_response(200, {"devices": [_status_entry("ARM-02"), _status_entry("ARM-01")]})]
    )

    statuses = await EcsAdapter(transport).fetch_statuses()

    request = transport.requests[0]
    assert request.method is OutboundHttpMethod.GET
    assert request.path == "/api/v1/device/status"
    assert request.query == ()
    assert request.response_limits.max_wire_bytes == 256 * 1024
    assert request.response_limits.max_decoded_bytes == 256 * 1024
    assert tuple(status.device.device_code for status in statuses) == ("ARM-02", "ARM-01")


@pytest.mark.asyncio
async def test_statuses_reject_duplicate_device_identity() -> None:
    transport = FakeOutboundHttpTransport(
        [_response(200, {"devices": [_status_entry("ARM-01"), _status_entry("ARM-01")]})]
    )

    with pytest.raises(EcsStatusUnavailableError):
        await EcsAdapter(transport).fetch_statuses()


@pytest.mark.asyncio
async def test_status_allows_supplier_nullable_diagnostic_fields() -> None:
    entry = _status_entry()
    device = entry["device"]
    state = entry["state"]
    assert isinstance(device, dict)
    assert isinstance(state, dict)
    device.update(
        {
            "device_name": None,
            "device_type": None,
            "role": None,
            "supported_commands": None,
            "supported_events": None,
        }
    )
    state["scenario"] = None
    transport = FakeOutboundHttpTransport([_response(200, {"devices": [entry]})])

    status = await EcsAdapter(transport).fetch_status("ARM-01")

    assert status.device.device_name is None
    assert status.device.supported_commands is None
    assert status.state.scenario is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "devices",
    [
        [],
        [_status_entry("ARM-02")],
        [_status_entry(state_device_code="ARM-02")],
        [_status_entry(), _status_entry("ARM-02")],
    ],
)
async def test_status_requires_exactly_one_matching_device(devices: list[dict[str, object]]) -> None:
    transport = FakeOutboundHttpTransport([_response(200, {"devices": devices})])
    with pytest.raises(EcsStatusUnavailableError):
        await EcsAdapter(transport).fetch_status("ARM-01")


@pytest.mark.asyncio
@pytest.mark.parametrize("trace_id", ["@", "T" * 121])
async def test_invalid_response_trace_id_enters_reconciliation(trace_id: str) -> None:
    transport = FakeOutboundHttpTransport([_response(200, {"code": 200, "message": "Accepted", "trace_id": trace_id})])
    result = await EcsAdapter(transport).submit_command(
        device_code="ARM-01",
        command_code="CMD-001",
        task_type="PICK",
        priority=1,
        timeout_ms=30_000,
        timestamp=1_786_032_000_000,
        params={},
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
                    decoded_body=b'{"code":200,"message":"Accepted"}',
                )
            ]
        )
    ).submit_command(
        device_code="ARM-01",
        command_code="CMD-001",
        task_type="PICK",
        priority=1,
        timeout_ms=30_000,
        timestamp=1_786_032_000_000,
        params={},
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
        task_type="PICK",
        priority=1,
        timeout_ms=30_000,
        timestamp=1_786_032_000_000,
        params={},
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
        task_type="PICK",
        priority=1,
        timeout_ms=30_000,
        timestamp=1_786_032_000_000,
        params={},
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
        "task_type": "PICK",
        "priority": 1,
        "timeout_ms": 30_000,
        "timestamp": 1_786_032_000_000,
        "params": {},
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
        task_type="PICK",
        priority=1,
        timeout_ms=30_000,
        timestamp=1_786_032_000_000,
        params={},
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
        task_type="PICK",
        priority=1,
        timeout_ms=30_000,
        timestamp=1_786_032_000_000,
        params={},
    )

    assert result.disposition is EcsSubmitDisposition.RECONCILING


@pytest.mark.asyncio
async def test_malformed_or_unknown_status_fails_closed() -> None:
    transport = FakeOutboundHttpTransport([_response(200, {"devices": [_status_entry(status="BROKEN")]})])

    with pytest.raises(EcsStatusUnavailableError):
        await EcsAdapter(transport).fetch_status("ARM-01")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid",
    [
        {"current_command_code": "__MISSING__"},
        {"updated_at": 2**63},
        {"updated_at": "1786032000000"},
        {"is_online": 1},
        {"mode": None},
        {"status": None},
    ],
)
async def test_status_rejects_incomplete_or_non_int64_wire(invalid: dict) -> None:
    entry = _status_entry()
    state = entry["state"]
    assert isinstance(state, dict)
    if invalid.get("current_command_code") == "__MISSING__":
        state.pop("current_command_code")
    else:
        state.update(invalid)
    transport = FakeOutboundHttpTransport([_response(200, {"devices": [entry]})])
    with pytest.raises(EcsStatusUnavailableError):
        await EcsAdapter(transport).fetch_status("ARM-01")
