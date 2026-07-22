"""EXTERNAL_HTTP typed transport result 合同。"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from src.app.sys.canonical_dispatch import CanonicalPayload, EndpointDefinition, ExternalHttpDispatchRequest
from src.app.sys.external_http_transport import (
    ExternalHttpProtocolResult,
    ExternalHttpTransportOutcome,
    ExternalHttpTransportPhase,
    ExternalHttpTransportResult,
)
from src.app.sys.services.outbox_delivery import dispatch_external_http
from src.app.sys.services.outbox_engine import _send_external_http


def _request() -> ExternalHttpDispatchRequest:
    canonical = CanonicalPayload.from_projection({"request_id": "REQ-001"})
    return ExternalHttpDispatchRequest(
        endpoint=EndpointDefinition(code="WMS_TEST", url="https://wms.example/effects"),
        payload=canonical,
    )


def test_transport_result_is_frozen_and_rejects_retryable_non_not_sent_outcome() -> None:
    result = ExternalHttpTransportResult.not_sent(
        phase=ExternalHttpTransportPhase.CONNECTING,
        safe_to_retry=True,
        error_code="CONNECT_FAILED",
        error_message="connection refused",
    )

    assert result.outcome is ExternalHttpTransportOutcome.NOT_SENT
    assert result.protocol_result is ExternalHttpProtocolResult.NOT_AVAILABLE
    assert result.safe_to_retry is True
    with pytest.raises(FrozenInstanceError):
        result.safe_to_retry = False  # type: ignore[misc]
    with pytest.raises(ValueError, match="safe_to_retry"):
        ExternalHttpTransportResult(
            outcome=ExternalHttpTransportOutcome.AMBIGUOUS,
            phase=ExternalHttpTransportPhase.AWAITING_RESPONSE,
            protocol_result=ExternalHttpProtocolResult.NOT_AVAILABLE,
            safe_to_retry=True,
            error_code="READ_TIMEOUT",
        )


@pytest.mark.parametrize(
    ("phase", "protocol_result", "http_status_code"),
    [
        (
            ExternalHttpTransportPhase.CONNECTING,
            ExternalHttpProtocolResult.REJECTED,
            409,
        ),
        (
            ExternalHttpTransportPhase.AWAITING_RESPONSE,
            ExternalHttpProtocolResult.UNKNOWN,
            None,
        ),
        (
            ExternalHttpTransportPhase.RESPONSE_RECEIVED,
            ExternalHttpProtocolResult.NOT_AVAILABLE,
            503,
        ),
    ],
)
def test_ambiguous_result_rejects_contradictory_phase_protocol_and_http_evidence(
    phase: ExternalHttpTransportPhase,
    protocol_result: ExternalHttpProtocolResult,
    http_status_code: int | None,
) -> None:
    with pytest.raises(ValueError, match="AMBIGUOUS"):
        ExternalHttpTransportResult.ambiguous(
            phase=phase,
            protocol_result=protocol_result,
            http_status_code=http_status_code,
            error_code="CONTRADICTORY_EVIDENCE",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "outcome", "protocol_result"),
    [
        (202, ExternalHttpTransportOutcome.ACCEPTED, ExternalHttpProtocolResult.ACCEPTED),
        (409, ExternalHttpTransportOutcome.ACCEPTED, ExternalHttpProtocolResult.REJECTED),
        (503, ExternalHttpTransportOutcome.AMBIGUOUS, ExternalHttpProtocolResult.UNKNOWN),
    ],
)
async def test_http_response_classification_is_delivery_certain_and_protocol_aware(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    outcome: ExternalHttpTransportOutcome,
    protocol_result: ExternalHttpProtocolResult,
) -> None:
    request = _request()
    calls: list[dict[str, Any]] = []

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: Any) -> SimpleNamespace:
            calls.append({"url": url, **kwargs})
            return SimpleNamespace(status_code=status_code)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())

    result = await _send_external_http(request)

    assert result.outcome is outcome
    assert result.phase is ExternalHttpTransportPhase.RESPONSE_RECEIVED
    assert result.protocol_result is protocol_result
    assert result.safe_to_retry is False
    assert result.http_status_code == status_code
    assert calls[0]["content"] is request.body


@pytest.mark.asyncio
async def test_connect_error_is_confirmed_not_sent_and_retry_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request()

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, _url: str, **_kwargs: Any) -> SimpleNamespace:
            raise httpx.ConnectError("connection refused", request=httpx.Request("POST", request.endpoint.url))

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())

    result = await _send_external_http(request)

    assert result.outcome is ExternalHttpTransportOutcome.NOT_SENT
    assert result.phase is ExternalHttpTransportPhase.CONNECTING
    assert result.safe_to_retry is True
    assert result.error_code == "CONNECT_ERROR"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exception_type", "phase"),
    [
        (httpx.ConnectTimeout, ExternalHttpTransportPhase.CONNECTING),
        (httpx.WriteError, ExternalHttpTransportPhase.SENDING),
        (httpx.ReadError, ExternalHttpTransportPhase.AWAITING_RESPONSE),
    ],
)
async def test_timeout_and_reset_are_ambiguous_and_never_retry_safe(
    monkeypatch: pytest.MonkeyPatch,
    exception_type: type[httpx.RequestError],
    phase: ExternalHttpTransportPhase,
) -> None:
    request = _request()

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(self, _url: str, **_kwargs: Any) -> SimpleNamespace:
            raise exception_type("transport interrupted", request=httpx.Request("POST", request.endpoint.url))

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())

    result = await _send_external_http(request)

    assert result.outcome is ExternalHttpTransportOutcome.AMBIGUOUS
    assert result.phase is phase
    assert result.protocol_result is ExternalHttpProtocolResult.NOT_AVAILABLE
    assert result.safe_to_retry is False


@pytest.mark.asyncio
async def test_preflight_failure_is_not_sent_but_not_retry_safe() -> None:
    registry = SimpleNamespace(resolve=lambda _code: (_ for _ in ()).throw(ValueError("endpoint missing")))
    sender_calls = 0

    async def sender(_request: ExternalHttpDispatchRequest) -> ExternalHttpTransportResult:
        nonlocal sender_calls
        sender_calls += 1
        return ExternalHttpTransportResult.accepted(
            http_status_code=202,
            protocol_result=ExternalHttpProtocolResult.ACCEPTED,
        )

    result = await dispatch_external_http(SimpleNamespace(target_code="MISSING"), registry, sender)

    assert result.outcome is ExternalHttpTransportOutcome.NOT_SENT
    assert result.phase is ExternalHttpTransportPhase.PREPARING
    assert result.safe_to_retry is False
    assert result.error_code == "DISPATCH_PREPARATION_FAILED"
    assert sender_calls == 0


@pytest.mark.asyncio
async def test_non_typed_sender_result_fails_closed_as_ambiguous_contract_violation() -> None:
    canonical = CanonicalPayload.from_projection({"request_id": "REQ-001"})
    outbox = SimpleNamespace(
        target_code="WMS_TEST",
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
    )
    registry = SimpleNamespace(
        resolve=lambda _code: EndpointDefinition(code="WMS_TEST", url="https://wms.example/effects")
    )

    async def invalid_sender(_request: ExternalHttpDispatchRequest) -> object:
        return object()

    result = await dispatch_external_http(outbox, registry, invalid_sender)  # type: ignore[arg-type]

    assert result.outcome is ExternalHttpTransportOutcome.AMBIGUOUS
    assert result.safe_to_retry is False
    assert result.error_code == "SENDER_CONTRACT_VIOLATION"
