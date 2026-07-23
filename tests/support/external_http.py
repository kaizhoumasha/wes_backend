"""EXTERNAL_HTTP 冻结派发测试工厂。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from src.app.sys.canonical_dispatch import CanonicalPayload, ExternalHttpDispatchRequest
from src.app.sys.external_http_binding import (
    ExternalHttpBindingDefinition,
    ExternalHttpProviderProfileDefinition,
    FrozenExternalHttpBinding,
    freeze_external_http_binding,
)
from src.app.sys.services.endpoint_registry import EndpointRegistry

if TYPE_CHECKING:
    from collections.abc import Mapping

TEST_CREDENTIAL_REFERENCE = "secret://tests/external-http-hmac@v1"
TEST_SECRET = b"test-only-external-http-secret"


class StaticTestCredentialProvider:
    """仅解析测试 reference 的无状态 provider。"""

    def resolve(self, credential_reference: str) -> bytes:
        if credential_reference != TEST_CREDENTIAL_REFERENCE:
            raise LookupError("test credential reference mismatch")
        return TEST_SECRET


def frozen_external_http_binding(
    *,
    target_code: str = "WMS_TEST",
    target_url: str = "https://wms.example/effects",
    provider_profile_identity: str = "tests.external-http.v1",
    operation_identity: str = "tests.external-http.effect@v1",
    timeout_seconds: float = 30,
) -> FrozenExternalHttpBinding:
    profile = ExternalHttpProviderProfileDefinition(
        identity=provider_profile_identity,
        bindings=(
            ExternalHttpBindingDefinition(
                operation_identity=operation_identity,
                allowed_target_codes=(target_code,),
                http_method="POST",
                timeout_seconds=timeout_seconds,
                auth_scheme="HMAC_SHA256",
                credential_reference=TEST_CREDENTIAL_REFERENCE,
            ),
        ),
    )
    return freeze_external_http_binding(
        profile=profile,
        operation_identity=operation_identity,
        target_code=target_code,
        endpoint_registry=EndpointRegistry({target_code: target_url}),
    )


def frozen_outbox_namespace(
    projection: Mapping[str, Any],
    *,
    target_code: str = "WMS_TEST",
    target_url: str = "https://wms.example/effects",
    provider_profile_identity: str = "tests.external-http.v1",
    operation_identity: str = "tests.external-http.effect@v1",
    **values: Any,
) -> SimpleNamespace:
    canonical = CanonicalPayload.from_projection(projection)
    binding = frozen_external_http_binding(
        target_code=target_code,
        target_url=target_url,
        provider_profile_identity=provider_profile_identity,
        operation_identity=operation_identity,
    )
    return SimpleNamespace(
        **binding.as_persisted_fields(),
        payload_json=dict(projection),
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
        **values,
    )


def signed_external_http_request(
    projection: Mapping[str, Any],
    *,
    target_code: str = "WMS_TEST",
    target_url: str = "https://wms.example/effects",
    timeout_seconds: float = 30,
) -> ExternalHttpDispatchRequest:
    canonical = CanonicalPayload.from_projection(projection)
    binding = frozen_external_http_binding(
        target_code=target_code,
        target_url=target_url,
        timeout_seconds=timeout_seconds,
    )
    return ExternalHttpDispatchRequest.from_persisted(
        binding=binding,
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
        secret=TEST_SECRET,
        timestamp="2026-07-23T00:00:00+00:00",
        nonce="test-nonce",
    )


__all__ = [
    "TEST_CREDENTIAL_REFERENCE",
    "TEST_SECRET",
    "StaticTestCredentialProvider",
    "frozen_external_http_binding",
    "frozen_outbox_namespace",
    "signed_external_http_request",
]
