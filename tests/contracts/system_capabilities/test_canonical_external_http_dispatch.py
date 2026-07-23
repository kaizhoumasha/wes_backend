"""EXTERNAL_HTTP canonical dispatch 冻结字节合同。"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from hashlib import sha256
from hmac import new as new_hmac
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from src.app.sys.canonical_dispatch import CanonicalPayload, ExternalHttpDispatchRequest
from src.app.sys.external_http_transport import (
    ExternalHttpProtocolResult,
    ExternalHttpTransportOutcome,
    ExternalHttpTransportPhase,
    ExternalHttpTransportResult,
)
from src.app.sys.models import (
    DispatchEnvelope,
    SystemOutboxCreate,
    SystemOutboxDispatchType,
    SystemOutboxTargetType,
)
from src.app.sys.services.endpoint_registry import EndpointDefinition
from src.app.sys.services.outbox_delivery import dispatch_external_http
from src.app.sys.services.outbox_engine import _send_external_http
from tests.support.external_http import (
    TEST_SECRET,
    StaticTestCredentialProvider,
    frozen_external_http_binding,
    signed_external_http_request,
)


def _external_envelope(**overrides: Any) -> DispatchEnvelope:
    projection = {"quantity": "1.2300", "request_id": "REQ-入库-001"}
    canonical = CanonicalPayload.from_projection(projection)
    frozen_binding = frozen_external_http_binding(
        target_code="WMS_INBOUND",
        target_url="https://wms.example/inbound",
        provider_profile_identity="wms.profile-test",
        operation_identity="wms.inventory.confirm@v1",
    )
    values = {
        "dispatch_key": "dispatch-001",
        "dispatch_type": SystemOutboxDispatchType.EXTERNAL_HTTP,
        "target_type": SystemOutboxTargetType.HTTP_ENDPOINT,
        "target_code": "WMS_INBOUND",
        "provider_profile_identity": "wms.profile-test",
        "operation_identity": "wms.inventory.confirm@v1",
        "payload_json": projection,
        "canonical_payload_bytes": canonical.body,
        "payload_hash": canonical.sha256,
        "frozen_binding": frozen_binding,
        "operation_domain": "WMS_INVENTORY",
    }
    values.update(overrides)
    return DispatchEnvelope(**values)


def test_canonical_payload_uses_one_frozen_byte_sequence_for_hash_signature_and_body() -> None:
    first = CanonicalPayload.from_projection({"z": "中文", "a": [1, {"enabled": True}]})
    second = CanonicalPayload.from_projection({"a": [1, {"enabled": True}], "z": "中文"})

    assert first.body == second.body == b'{"a":[1,{"enabled":true}],"z":"\xe4\xb8\xad\xe6\x96\x87"}'
    assert first.sha256 == sha256(first.body).hexdigest()
    assert first.sign_hmac_sha256(b"test-secret") == new_hmac(b"test-secret", first.body, sha256).hexdigest()

    binding = frozen_external_http_binding(target_code="WMS_INBOUND", target_url="https://wms.example/inbound")
    request = ExternalHttpDispatchRequest.from_persisted(
        binding=binding,
        canonical_payload_bytes=first.body,
        payload_hash=first.sha256,
        secret=TEST_SECRET,
        timestamp="2026-07-23T00:00:00+00:00",
        nonce="canonical-test",
    )
    assert request.body is first.body
    assert request.payload_hash == first.sha256
    assert request.sign_hmac_sha256(b"test-secret") == first.sign_hmac_sha256(b"test-secret")
    with pytest.raises(FrozenInstanceError):
        request.endpoint = EndpointDefinition(code="OTHER", url="https://other.example")  # type: ignore[misc]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"canonical_payload_bytes": None}, "canonical_payload_bytes"),
        ({"payload_hash": None}, "payload_hash"),
        (
            {
                "canonical_payload_bytes": b'{"request_id":"different"}',
                "payload_hash": sha256(b'{"request_id":"different"}').hexdigest(),
            },
            "projection",
        ),
        ({"payload_hash": "0" * 64}, "payload_hash"),
    ],
)
def test_external_http_envelope_fails_closed_when_canonical_contract_is_missing_or_drifted(
    overrides: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _external_envelope(**overrides)


def test_non_http_envelope_does_not_require_canonical_payload() -> None:
    envelope = DispatchEnvelope(
        dispatch_key="device-command-001",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ECS-001",
        provider_profile_identity="ecs.device-command.v1",
        operation_identity="device.command",
        payload_json={"command": "MOVE"},
        operation_domain="DEVICE",
    )

    assert envelope.canonical_payload_bytes is None
    assert envelope.payload_hash is None
    assert envelope.payload_json == {"command": "MOVE"}


def test_external_http_create_schema_fails_closed_without_canonical_payload() -> None:
    with pytest.raises(ValueError, match="canonical_payload_bytes"):
        SystemOutboxCreate(
            dispatch_key="dispatch-001",
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code="WMS_INBOUND",
            provider_profile_identity="wms.profile-test",
            operation_identity="wms.inventory.confirm@v1",
            payload_json={"request_id": "REQ-001"},
            operation_domain="WMS_INVENTORY",
        )


def test_non_http_create_schema_does_not_require_canonical_payload() -> None:
    schema = SystemOutboxCreate(
        dispatch_key="device-command-001",
        dispatch_type=SystemOutboxDispatchType.DEVICE_COMMAND,
        target_type=SystemOutboxTargetType.DEVICE,
        target_code="ECS-001",
        provider_profile_identity="ecs.device-command.v1",
        operation_identity="device.command",
        payload_json={"command": "MOVE"},
        operation_domain="DEVICE",
    )

    assert schema.canonical_payload_bytes is None
    assert schema.payload_hash is None


@pytest.mark.asyncio
async def test_repeated_external_http_attempts_use_frozen_bytes_without_reading_payload_json() -> None:
    canonical = CanonicalPayload.from_projection({"request_id": "REQ-001", "quantity": "1.2300"})

    class ProjectionMustNotBeRead:
        canonical_payload_bytes = canonical.body
        payload_hash = canonical.sha256

        @property
        def payload_json(self) -> dict[str, Any]:
            raise AssertionError("dispatch/retry must not read payload_json")

    requests: list[ExternalHttpDispatchRequest] = []

    async def sender(request: ExternalHttpDispatchRequest) -> ExternalHttpTransportResult:
        requests.append(request)
        return ExternalHttpTransportResult.not_sent(
            phase=ExternalHttpTransportPhase.CONNECTING,
            safe_to_retry=True,
            error_code="CONNECT_ERROR",
        )

    outbox = ProjectionMustNotBeRead()
    frozen_binding = frozen_external_http_binding(
        target_code="WMS_INBOUND",
        target_url="https://wms.example/inbound",
    )
    for field_name, value in frozen_binding.as_persisted_fields().items():
        setattr(outbox, field_name, value)

    first = await dispatch_external_http(outbox, StaticTestCredentialProvider(), sender)
    second = await dispatch_external_http(outbox, StaticTestCredentialProvider(), sender)

    assert first.outcome is ExternalHttpTransportOutcome.NOT_SENT
    assert second.outcome is ExternalHttpTransportOutcome.NOT_SENT
    assert [request.body for request in requests] == [canonical.body, canonical.body]
    assert [request.payload_hash for request in requests] == [canonical.sha256, canonical.sha256]


def test_external_http_request_rejects_persisted_bytes_hash_mismatch() -> None:
    frozen_binding = frozen_external_http_binding(
        target_code="WMS_INBOUND",
        target_url="https://wms.example/inbound",
    )
    with pytest.raises(ValueError, match="payload_hash"):
        ExternalHttpDispatchRequest.from_persisted(
            binding=frozen_binding,
            canonical_payload_bytes=b'{"request_id":"REQ-001"}',
            payload_hash="f" * 64,
            secret=TEST_SECRET,
            timestamp="2026-07-23T00:00:00+00:00",
            nonce="mismatch-test",
        )


def test_replay_parses_only_the_frozen_canonical_bytes() -> None:
    canonical = CanonicalPayload.from_projection({"request_id": "REQ-001", "quantity": "1.2300"})

    assert canonical.parse_projection() == {"request_id": "REQ-001", "quantity": "1.2300"}
    noncanonical_body = b'{"request_id": "REQ-001"}'
    with pytest.raises(ValueError, match="canonical JSON form"):
        CanonicalPayload.from_persisted(
            canonical_payload_bytes=noncanonical_body,
            payload_hash=sha256(noncanonical_body).hexdigest(),
        ).parse_projection()


@pytest.mark.asyncio
async def test_default_http_sender_posts_the_exact_frozen_body(monkeypatch: pytest.MonkeyPatch) -> None:
    canonical = CanonicalPayload.from_projection({"quantity": "1.2300", "request_id": "REQ-001"})
    request = signed_external_http_request(
        {"quantity": "1.2300", "request_id": "REQ-001"},
        target_code="WMS_INBOUND",
        target_url="https://wms.example/inbound",
    )
    calls: list[dict[str, Any]] = []

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs: Any) -> SimpleNamespace:
            calls.append({"method": method, "url": url, **kwargs})
            return SimpleNamespace(status_code=202)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())

    result = await _send_external_http(request)

    assert result.outcome is ExternalHttpTransportOutcome.ACCEPTED
    assert result.protocol_result is ExternalHttpProtocolResult.ACCEPTED
    assert calls == [
        {
            "method": "POST",
            "url": "https://wms.example/inbound",
            "content": canonical.body,
            "headers": request.headers,
        }
    ]
