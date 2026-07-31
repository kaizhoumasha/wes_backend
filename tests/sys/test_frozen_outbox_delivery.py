"""SystemOutbox 只从冻结 target/binding 与版本化 secret ref 发送。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.sys.external_http_binding import (
    ExternalHttpBindingDefinition,
    ExternalHttpProviderProfileDefinition,
    freeze_external_http_binding,
)
from src.app.sys.external_http_credentials import CredentialRevokedError
from src.app.sys.external_http_transport import (
    ExternalHttpProtocolResult,
    ExternalHttpTransportOutcome,
    ExternalHttpTransportResult,
)
from src.app.sys.services.endpoint_registry import EndpointRegistry
from src.app.sys.services.outbox_delivery import dispatch_external_http


def _outbox() -> SimpleNamespace:
    frozen = freeze_external_http_binding(
        profile=ExternalHttpProviderProfileDefinition(
            identity="wms.effect.production",
            environment="production",
            network_trust_mode="authenticated_network",
            bindings=(
                ExternalHttpBindingDefinition(
                    operation_identity="wms.inventory.confirm_inbound@v1",
                    allowed_target_codes=("WMS_CONFIRM_INBOUND",),
                    http_method="POST",
                    timeout_seconds=15,
                    auth_scheme="HMAC_SHA256",
                    credential_reference="secret://wms/effect-hmac@v1",
                ),
            ),
        ),
        operation_identity="wms.inventory.confirm_inbound@v1",
        target_code="WMS_CONFIRM_INBOUND",
        endpoint_registry=EndpointRegistry({"WMS_CONFIRM_INBOUND": "https://wms-v1.example/effects/inbound"}),
    )
    canonical = CanonicalPayload.from_projection({"inbound_key": "IN-001"})
    return SimpleNamespace(
        **frozen.as_persisted_fields(),
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
        idempotency_key="intent-frozen-delivery-001",
    )


class _RecordingCredentialProvider:
    def __init__(self, secret: bytes = b"never-persist-this-secret") -> None:
        self.secret = secret
        self.references: list[str] = []

    def resolve(self, credential_reference: str) -> bytes:
        self.references.append(credential_reference)
        return self.secret


@pytest.mark.asyncio
async def test_delivery_uses_frozen_url_and_exact_credential_version() -> None:
    outbox = _outbox()
    credential_provider = _RecordingCredentialProvider()
    captured_requests = []

    async def sender(request):
        captured_requests.append(request)
        return ExternalHttpTransportResult.accepted(
            http_status_code=202,
            protocol_result=ExternalHttpProtocolResult.ACCEPTED,
        )

    result = await dispatch_external_http(outbox, credential_provider, sender)

    assert result.outcome is ExternalHttpTransportOutcome.ACCEPTED
    assert credential_provider.references == ["secret://wms/effect-hmac@v1"]
    assert len(captured_requests) == 1
    request = captured_requests[0]
    assert request.endpoint.url == "https://wms-v1.example/effects/inbound"
    assert request.method == "POST"
    assert request.timeout_seconds == 15
    assert request.credential_reference == "secret://wms/effect-hmac@v1"
    assert request.headers["X-WES-Signature-Algorithm"] == "HMAC_SHA256"
    assert request.headers["X-WES-Credential-Reference"] == "secret://wms/effect-hmac@v1"
    assert "never-persist-this-secret" not in repr(request)
    assert request.headers["X-WES-Signature"] not in repr(request)


@pytest.mark.asyncio
async def test_revoked_frozen_credential_stops_before_sender_without_leaking_reference() -> None:
    outbox = _outbox()

    class _RevokedProvider:
        def resolve(self, _credential_reference: str) -> bytes:
            raise CredentialRevokedError

    sender = AsyncMock()
    warning = MagicMock()
    with patch("src.app.sys.services.outbox_delivery.logger.warning", warning):
        result = await dispatch_external_http(outbox, _RevokedProvider(), sender)

    assert result.outcome is ExternalHttpTransportOutcome.NOT_SENT
    assert result.safe_to_retry is False
    assert result.error_code == "CREDENTIAL_REVOKED"
    sender.assert_not_awaited()
    diagnostic = f"{warning.call_args} {result.evidence_json()}"
    assert "secret://" not in diagnostic
    assert "never-persist-this-secret" not in diagnostic
    assert "X-WES-Signature" not in diagnostic


@pytest.mark.asyncio
async def test_sender_exception_cannot_copy_auth_headers_into_log_or_evidence() -> None:
    outbox = _outbox()
    credential_provider = _RecordingCredentialProvider()

    async def sender(request):
        raise RuntimeError(f"unsafe sender detail: {request.headers}")

    error_log = MagicMock()
    with patch("src.app.sys.services.outbox_delivery.logger.error", error_log):
        result = await dispatch_external_http(outbox, credential_provider, sender)

    assert result.outcome is ExternalHttpTransportOutcome.AMBIGUOUS
    assert result.error_message == "external HTTP sender raised RuntimeError"
    diagnostic = f"{error_log.call_args} {result.evidence_json()}"
    assert "X-WES-Signature" not in diagnostic
    assert "secret://" not in diagnostic
    assert "never-persist-this-secret" not in diagnostic


@pytest.mark.asyncio
async def test_unexpected_credential_provider_error_fails_closed_without_leaking_detail() -> None:
    outbox = _outbox()

    class _ExplodingProvider:
        def resolve(self, _credential_reference: str) -> bytes:
            raise RuntimeError("unsafe provider detail: never-persist-this-secret")

    sender = AsyncMock()
    error_log = MagicMock()
    with patch("src.app.sys.services.outbox_delivery.logger.error", error_log):
        result = await dispatch_external_http(outbox, _ExplodingProvider(), sender)

    assert result.outcome is ExternalHttpTransportOutcome.NOT_SENT
    assert result.safe_to_retry is False
    assert result.error_code == "CREDENTIAL_RESOLUTION_FAILED"
    sender.assert_not_awaited()
    diagnostic = f"{error_log.call_args} {result.evidence_json()}"
    assert "never-persist-this-secret" not in diagnostic
    assert "secret://" not in diagnostic
