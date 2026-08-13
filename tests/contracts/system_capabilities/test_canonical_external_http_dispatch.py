"""EXTERNAL_HTTP canonical dispatch 冻结字节合同。"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from hashlib import sha256
from hmac import new as new_hmac
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from src.app.sys import canonical_dispatch
from src.app.sys.canonical_dispatch import CanonicalPayload, ExternalHttpDispatchRequest
from src.app.sys.external_http_binding import (
    ExternalHttpBindingDefinition,
    ExternalHttpProviderProfileDefinition,
    FrozenExternalHttpBinding,
    freeze_external_http_binding,
)
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
from src.app.sys.services.endpoint_registry import EndpointDefinition, EndpointRegistry
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
        "idempotency_key": "intent-idempotency-001",
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


def _frozen_get_binding(*, auth_scheme: str) -> FrozenExternalHttpBinding:
    credential_reference = None if auth_scheme == "NONE" else "secret://provider/query@v1"
    profile = ExternalHttpProviderProfileDefinition(
        identity="provider.query.production",
        environment="production",
        network_trust_mode="isolated_lan",
        bindings=(
            ExternalHttpBindingDefinition(
                operation_identity="provider.inventory.query@v1",
                allowed_target_codes=("PROVIDER_QUERY",),
                http_method="GET",
                timeout_seconds=10,
                auth_scheme=auth_scheme,  # type: ignore[arg-type]
                credential_reference=credential_reference,
            ),
        ),
    )
    return freeze_external_http_binding(
        profile=profile,
        operation_identity="provider.inventory.query@v1",
        target_code="PROVIDER_QUERY",
        endpoint_registry=EndpointRegistry({"PROVIDER_QUERY": "http://factory-provider.example/inventory"}),
    )


def test_canonical_payload_uses_one_frozen_byte_sequence_for_hash_signature_and_body() -> None:
    first = CanonicalPayload.from_projection({"z": "中文", "a": [1, {"enabled": True}]})
    second = CanonicalPayload.from_projection({"a": [1, {"enabled": True}], "z": "中文"})

    assert first.body == second.body == b'{"a":[1,{"enabled":true}],"z":"\xe4\xb8\xad\xe6\x96\x87"}'
    assert first.sha256 == sha256(first.body).hexdigest()
    assert first.sign_hmac_sha256(b"test-secret") == new_hmac(b"test-secret", first.body, sha256).hexdigest()

    binding = frozen_external_http_binding(
        target_code="WMS_INBOUND",
        target_url="https://wms.example/inbound",
        operation_identity="wms.inventory.confirm_inbound@v1",
    )
    request = ExternalHttpDispatchRequest.from_persisted(
        binding=binding,
        canonical_payload_bytes=first.body,
        payload_hash=first.sha256,
        idempotency_key="intent-idempotency-001",
        secret=TEST_SECRET,
        timestamp="2026-07-23T00:00:00+00:00",
        nonce="canonical-test",
    )
    assert request.body is first.body
    assert request.payload_hash == first.sha256
    assert request.idempotency_key == "intent-idempotency-001"
    assert request.operation_identity == binding.operation_identity
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
        dispatch_key="internal-signal-001",
        dispatch_type=SystemOutboxDispatchType.INTERNAL_SIGNAL,
        target_type=SystemOutboxTargetType.INTERNAL_SERVICE,
        target_code="RUNTIME",
        provider_profile_identity="runtime.internal-signal.v1",
        operation_identity="runtime.signal",
        payload_json={"signal": "WAKE"},
        operation_domain="RUNTIME",
    )

    assert envelope.canonical_payload_bytes is None
    assert envelope.payload_hash is None
    assert envelope.payload_json == {"signal": "WAKE"}


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
        dispatch_key="internal-signal-001",
        dispatch_type=SystemOutboxDispatchType.INTERNAL_SIGNAL,
        target_type=SystemOutboxTargetType.INTERNAL_SERVICE,
        target_code="RUNTIME",
        provider_profile_identity="runtime.internal-signal.v1",
        operation_identity="runtime.signal",
        payload_json={"signal": "WAKE"},
        operation_domain="RUNTIME",
    )

    assert schema.canonical_payload_bytes is None
    assert schema.payload_hash is None


@pytest.mark.asyncio
async def test_repeated_external_http_attempts_use_frozen_bytes_without_reading_payload_json() -> None:
    canonical = CanonicalPayload.from_projection({"request_id": "REQ-001", "quantity": "1.2300"})

    class ProjectionMustNotBeRead:
        canonical_payload_bytes = canonical.body
        payload_hash = canonical.sha256
        idempotency_key = "intent-idempotency-001"

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
        operation_identity="wms.inventory.confirm_inbound@v1",
    )
    for field_name, value in frozen_binding.as_persisted_fields().items():
        setattr(outbox, field_name, value)

    first = await dispatch_external_http(outbox, StaticTestCredentialProvider(), sender)
    second = await dispatch_external_http(outbox, StaticTestCredentialProvider(), sender)

    assert first.outcome is ExternalHttpTransportOutcome.NOT_SENT
    assert second.outcome is ExternalHttpTransportOutcome.NOT_SENT
    assert [request.body for request in requests] == [canonical.body, canonical.body]
    assert [request.payload_hash for request in requests] == [canonical.sha256, canonical.sha256]
    assert [request.idempotency_key for request in requests] == [
        "intent-idempotency-001",
        "intent-idempotency-001",
    ]


@pytest.mark.asyncio
async def test_external_http_dispatch_hashes_canonical_payload_once(monkeypatch: pytest.MonkeyPatch) -> None:
    canonical = CanonicalPayload.from_projection({"request_id": "REQ-001"})
    outbox = SimpleNamespace(
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
        idempotency_key="intent-idempotency-001",
    )
    frozen_binding = frozen_external_http_binding(
        target_code="WMS_INBOUND",
        target_url="https://wms.example/inbound",
        operation_identity="wms.inventory.confirm_inbound@v1",
    )
    for field_name, value in frozen_binding.as_persisted_fields().items():
        setattr(outbox, field_name, value)
    original_payload_sha256 = canonical_dispatch.payload_sha256
    hash_count = 0

    def tracked_payload_sha256(payload: bytes) -> str:
        nonlocal hash_count
        hash_count += 1
        return original_payload_sha256(payload)

    monkeypatch.setattr(canonical_dispatch, "payload_sha256", tracked_payload_sha256)
    requests: list[ExternalHttpDispatchRequest] = []

    async def sender(request: ExternalHttpDispatchRequest) -> ExternalHttpTransportResult:
        requests.append(request)
        return ExternalHttpTransportResult.not_sent(
            phase=ExternalHttpTransportPhase.CONNECTING,
            safe_to_retry=True,
            error_code="CONNECT_ERROR",
        )

    result = await dispatch_external_http(outbox, StaticTestCredentialProvider(), sender)

    assert result.outcome is ExternalHttpTransportOutcome.NOT_SENT
    assert len(requests) == 1
    assert hash_count == 1


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
            idempotency_key="intent-idempotency-001",
            secret=TEST_SECRET,
            timestamp="2026-07-23T00:00:00+00:00",
            nonce="mismatch-test",
        )


def test_effect_request_headers_are_closed_and_metadata_is_signed() -> None:
    canonical = CanonicalPayload.from_projection({"request_id": "REQ-001"})
    binding = frozen_external_http_binding(
        target_code="WMS_INBOUND",
        target_url="https://wms.example/inbound",
        operation_identity="wms.inventory.confirm_inbound@v1",
    )
    request = ExternalHttpDispatchRequest.from_persisted(
        binding=binding,
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
        idempotency_key="intent-idempotency-001",
        secret=TEST_SECRET,
        timestamp="2026-07-24T00:00:00+00:00",
        nonce="metadata-test",
    )

    assert request.headers == {
        "Content-Type": "application/json",
        "Idempotency-Key": "intent-idempotency-001",
        "X-WES-Content-SHA256": canonical.sha256,
        "X-WES-Credential-Reference": request.credential_reference,
        "X-WES-Nonce": "metadata-test",
        "X-WES-Operation-Identity": "wms.inventory.confirm_inbound@v1",
        "X-WES-Signature": request.headers["X-WES-Signature"],
        "X-WES-Signature-Algorithm": "HMAC_SHA256",
        "X-WES-Timestamp": "2026-07-24T00:00:00+00:00",
    }
    expected_canonical = (
        "POST\n/inbound\n2026-07-24T00:00:00+00:00\nmetadata-test\n"
        f"{canonical.sha256}\nwms.inventory.confirm_inbound@v1\nintent-idempotency-001"
    ).encode()
    expected_signature = new_hmac(TEST_SECRET, expected_canonical, sha256).hexdigest()
    assert request.headers["X-WES-Signature"] == expected_signature

    for original, tampered in (
        (b"wms.inventory.confirm_inbound@v1", b"wms.inventory.other@v1"),
        (b"intent-idempotency-001", b"other-idempotency-key"),
    ):
        tampered_canonical = expected_canonical.replace(original, tampered)
        assert new_hmac(TEST_SECRET, tampered_canonical, sha256).hexdigest() != expected_signature


def test_none_effect_request_keeps_identity_and_hash_without_authentication_headers() -> None:
    canonical = CanonicalPayload.from_projection({"request_id": "REQ-NONE-001"})
    binding = frozen_external_http_binding(
        target_code="WMS_INBOUND",
        target_url="http://factory-wms/inbound",
        operation_identity="wms.inventory.confirm_inbound@v1",
        auth_scheme="NONE",
        network_trust_mode="isolated_lan",
        credential_reference=None,
    )

    request = ExternalHttpDispatchRequest.from_persisted(
        binding=binding,
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
        idempotency_key="intent-none-001",
        secret=None,
        timestamp=None,
        nonce=None,
    )

    assert request.headers == {
        "Content-Type": "application/json",
        "Idempotency-Key": "intent-none-001",
        "X-WES-Content-SHA256": canonical.sha256,
        "X-WES-Operation-Identity": "wms.inventory.confirm_inbound@v1",
    }
    assert not any(
        fragment in header_name.lower()
        for header_name in request.headers
        for fragment in ("credential", "nonce", "signature", "timestamp")
    )


@pytest.mark.parametrize(
    ("secret", "timestamp", "nonce"),
    [
        (TEST_SECRET, None, None),
        (None, "2026-07-29T00:00:00+00:00", None),
        (None, None, "unexpected-nonce"),
    ],
)
def test_none_effect_request_rejects_authentication_material(
    secret: bytes | None,
    timestamp: str | None,
    nonce: str | None,
) -> None:
    canonical = CanonicalPayload.from_projection({"request_id": "REQ-NONE-INVALID"})
    binding = frozen_external_http_binding(
        target_url="http://factory-wms/inbound",
        operation_identity="wms.inventory.confirm_inbound@v1",
        auth_scheme="NONE",
        network_trust_mode="isolated_lan",
        credential_reference=None,
    )

    with pytest.raises(ValueError, match="NONE request must not carry authentication material"):
        ExternalHttpDispatchRequest.from_persisted(
            binding=binding,
            canonical_payload_bytes=canonical.body,
            payload_hash=canonical.sha256,
            idempotency_key="intent-none-invalid",
            secret=secret,
            timestamp=timestamp,
            nonce=nonce,
        )


@pytest.mark.parametrize("auth_scheme", ["NONE", "HMAC_SHA256"])
def test_get_request_projects_frozen_payload_to_query_params_for_shared_auth_schemes(auth_scheme: str) -> None:
    binding = _frozen_get_binding(auth_scheme=auth_scheme)
    projection = {"cursor": "CURSOR-1", "limit": 25}
    canonical = CanonicalPayload.from_projection(projection)

    request = ExternalHttpDispatchRequest.from_persisted(
        binding=binding,
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
        secret=None if auth_scheme == "NONE" else TEST_SECRET,
        timestamp=None if auth_scheme == "NONE" else "2026-07-24T00:00:00+00:00",
        nonce=None if auth_scheme == "NONE" else "query-nonce",
    )

    assert request.method == "GET"
    assert request.body is None
    assert request.query_params == projection
    assert request.headers["X-WES-Content-SHA256"] == canonical.sha256
    assert "Content-Type" not in request.headers
    if auth_scheme == "NONE":
        assert set(request.headers) == {"X-WES-Content-SHA256"}
    else:
        assert request.headers["X-WES-Signature-Algorithm"] == "HMAC_SHA256"
        assert request.headers["X-WES-Credential-Reference"] == binding.credential_reference


@pytest.mark.asyncio
async def test_none_dispatch_uses_canonical_sender_without_resolving_credentials() -> None:
    canonical = CanonicalPayload.from_projection({"request_id": "REQ-NONE-DISPATCH"})
    outbox = SimpleNamespace(
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
        idempotency_key="intent-none-dispatch",
    )
    frozen_binding = frozen_external_http_binding(
        target_url="http://factory-wms/inbound",
        operation_identity="wms.inventory.confirm_inbound@v1",
        auth_scheme="NONE",
        network_trust_mode="isolated_lan",
        credential_reference=None,
    )
    for field_name, value in frozen_binding.as_persisted_fields().items():
        setattr(outbox, field_name, value)

    class CredentialProviderMustNotBeRead:
        def resolve(self, _credential_reference: str) -> bytes:
            raise AssertionError("NONE dispatch must not resolve credentials")

    requests: list[ExternalHttpDispatchRequest] = []

    async def sender(request: ExternalHttpDispatchRequest) -> ExternalHttpTransportResult:
        requests.append(request)
        return ExternalHttpTransportResult.accepted(
            http_status_code=202,
            protocol_result=ExternalHttpProtocolResult.ACCEPTED,
        )

    result = await dispatch_external_http(outbox, CredentialProviderMustNotBeRead(), sender)

    assert result.outcome is ExternalHttpTransportOutcome.ACCEPTED
    assert len(requests) == 1
    assert requests[0].headers["X-WES-Content-SHA256"] == canonical.sha256


def test_authored_wms_effect_request_rejects_missing_idempotency_key() -> None:
    canonical = CanonicalPayload.from_projection({"request_id": "REQ-001"})
    binding = frozen_external_http_binding(
        target_code="WMS_INBOUND",
        target_url="https://wms.example/inbound",
        operation_identity="wms.inventory.confirm_inbound@v1",
    )

    with pytest.raises(ValueError, match="idempotency_key"):
        ExternalHttpDispatchRequest.from_persisted(
            binding=binding,
            canonical_payload_bytes=canonical.body,
            payload_hash=canonical.sha256,
            secret=TEST_SECRET,
            timestamp="2026-07-24T00:00:00+00:00",
            nonce="metadata-test",
        )


def test_generic_outbox_keeps_idempotency_metadata_nullable() -> None:
    envelope = DispatchEnvelope(
        dispatch_key="internal-signal-001",
        dispatch_type=SystemOutboxDispatchType.INTERNAL_SIGNAL,
        target_type=SystemOutboxTargetType.INTERNAL_SERVICE,
        target_code="RUNTIME",
        provider_profile_identity="runtime.internal-signal.v1",
        operation_identity="runtime.signal",
        payload_json={"signal": "WAKE"},
        operation_domain="RUNTIME",
    )

    assert envelope.idempotency_key is None
    assert (
        SystemOutboxCreate(
            dispatch_key="internal-signal-002",
            dispatch_type=SystemOutboxDispatchType.INTERNAL_SIGNAL,
            target_type=SystemOutboxTargetType.INTERNAL_SERVICE,
            target_code="RUNTIME",
            provider_profile_identity="runtime.internal-signal.v1",
            operation_identity="runtime.signal",
            payload_json={"signal": "WAKE"},
            operation_domain="RUNTIME",
        ).idempotency_key
        is None
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
    client_options: list[dict[str, Any]] = []

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        def build_request(self, method: str, url: str, **kwargs: Any) -> httpx.Request:
            calls.append({"method": method, "url": url, **kwargs})
            return httpx.Request(method, url, **kwargs)

        async def send(self, request: httpx.Request, *, stream: bool) -> httpx.Response:
            assert stream is True
            return httpx.Response(status_code=202, content=b"", request=request)

    def create_client(**kwargs: Any) -> FakeClient:
        client_options.append(kwargs)
        return FakeClient()

    monkeypatch.setattr(httpx, "AsyncClient", create_client)

    result = await _send_external_http(request)

    assert result.outcome is ExternalHttpTransportOutcome.ACCEPTED
    assert result.protocol_result is ExternalHttpProtocolResult.ACCEPTED
    assert client_options == [{"timeout": request.timeout_seconds, "trust_env": False}]
    assert calls == [
        {
            "method": "POST",
            "url": "https://wms.example/inbound",
            "content": canonical.body,
            "headers": request.headers,
        }
    ]


@pytest.mark.asyncio
async def test_default_http_sender_sends_get_projection_as_query_params_without_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _frozen_get_binding(auth_scheme="NONE")
    projection = {"cursor": "CURSOR-1", "limit": 25}
    canonical = CanonicalPayload.from_projection(projection)
    request = ExternalHttpDispatchRequest.from_persisted(
        binding=binding,
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
        secret=None,
        timestamp=None,
        nonce=None,
    )
    calls: list[dict[str, Any]] = []

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        def build_request(self, method: str, url: str, **kwargs: Any) -> httpx.Request:
            calls.append({"method": method, "url": url, **kwargs})
            return httpx.Request(method, url, **kwargs)

        async def send(self, request: httpx.Request, *, stream: bool) -> httpx.Response:
            assert stream is True
            return httpx.Response(status_code=200, content=b"", request=request)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FakeClient())

    result = await _send_external_http(request)

    assert result.outcome is ExternalHttpTransportOutcome.ACCEPTED
    assert calls == [
        {
            "method": "GET",
            "url": "http://factory-provider.example/inventory",
            "params": projection,
            "headers": request.headers,
        }
    ]
