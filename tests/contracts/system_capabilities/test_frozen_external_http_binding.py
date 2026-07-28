"""EXTERNAL_HTTP target/profile/credential 冻结合同。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.app.sys.canonical_dispatch import CanonicalPayload
from src.app.sys.external_http_binding import (
    ExternalHttpBindingDefinition,
    ExternalHttpProviderProfileDefinition,
    FrozenExternalHttpBinding,
    freeze_external_http_binding,
)
from src.app.sys.external_http_credentials import (
    CredentialRevokedError,
    EnvironmentVersionedCredentialProvider,
    build_environment_external_http_credential_provider,
)
from src.app.sys.models import (
    DispatchEnvelope,
    SystemOutbox,
    SystemOutboxDispatchType,
    SystemOutboxTargetType,
    SystemOutboxUpdate,
)
from src.app.sys.services.endpoint_registry import EndpointRegistry

ROOT = Path(__file__).parents[3]


def _profile(*, credential_reference: str = "secret://wms/effect-hmac@v1") -> ExternalHttpProviderProfileDefinition:
    return ExternalHttpProviderProfileDefinition(
        identity="wms.effect.production",
        environment="production",
        bindings=(
            ExternalHttpBindingDefinition(
                operation_identity="wms.inventory.confirm_inbound@v1",
                allowed_target_codes=("WMS_CONFIRM_INBOUND",),
                http_method="POST",
                timeout_seconds=15,
                auth_scheme="HMAC_SHA256",
                credential_reference=credential_reference,
            ),
        ),
    )


def _frozen_binding() -> FrozenExternalHttpBinding:
    return freeze_external_http_binding(
        profile=_profile(),
        operation_identity="wms.inventory.confirm_inbound@v1",
        target_code="WMS_CONFIRM_INBOUND",
        endpoint_registry=EndpointRegistry({"WMS_CONFIRM_INBOUND": "https://wms.example/effects/inbound"}),
    )


def test_target_and_credential_rotation_only_affect_new_frozen_binding() -> None:
    original = freeze_external_http_binding(
        profile=_profile(),
        operation_identity="wms.inventory.confirm_inbound@v1",
        target_code="WMS_CONFIRM_INBOUND",
        endpoint_registry=EndpointRegistry({"WMS_CONFIRM_INBOUND": "https://wms-v1.example/effects/inbound"}),
    )
    rotated = freeze_external_http_binding(
        profile=_profile(credential_reference="secret://wms/effect-hmac@v2"),
        operation_identity="wms.inventory.confirm_inbound@v1",
        target_code="WMS_CONFIRM_INBOUND",
        endpoint_registry=EndpointRegistry({"WMS_CONFIRM_INBOUND": "https://wms-v2.example/effects/inbound"}),
    )

    persisted = FrozenExternalHttpBinding.from_persisted(**original.as_persisted_fields())

    assert persisted == original
    assert persisted.target_snapshot.url == "https://wms-v1.example/effects/inbound"
    assert persisted.credential_reference == "secret://wms/effect-hmac@v1"
    assert persisted.target_snapshot_hash != rotated.target_snapshot_hash
    assert persisted.provider_profile_hash != rotated.provider_profile_hash
    assert persisted.binding_revision != rotated.binding_revision


@pytest.mark.parametrize(
    ("target_code", "endpoints"),
    [
        ("https://attacker.invalid/effect", {"WMS_CONFIRM_INBOUND": "https://wms.example/effect"}),
        ("UNAUTHORED_TARGET", {"UNAUTHORED_TARGET": "https://wms.example/effect"}),
    ],
)
def test_freeze_rejects_raw_url_and_target_outside_typed_binding(
    target_code: str,
    endpoints: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="authored binding"):
        freeze_external_http_binding(
            profile=_profile(),
            operation_identity="wms.inventory.confirm_inbound@v1",
            target_code=target_code,
            endpoint_registry=EndpointRegistry(endpoints),
        )


def test_persisted_target_snapshot_rejects_hash_or_identity_tampering() -> None:
    frozen = freeze_external_http_binding(
        profile=_profile(),
        operation_identity="wms.inventory.confirm_inbound@v1",
        target_code="WMS_CONFIRM_INBOUND",
        endpoint_registry=EndpointRegistry({"WMS_CONFIRM_INBOUND": "https://wms.example/effects/inbound"}),
    )
    fields = frozen.as_persisted_fields()

    with pytest.raises(ValueError, match="target snapshot hash"):
        FrozenExternalHttpBinding.from_persisted(**{**fields, "target_snapshot_hash": "0" * 64})
    with pytest.raises(ValueError, match="target code"):
        FrozenExternalHttpBinding.from_persisted(**{**fields, "target_code": "OTHER_TARGET"})


def test_credential_reference_must_be_versioned() -> None:
    with pytest.raises(ValueError, match="versioned credential reference"):
        _profile(credential_reference="secret://wms/effect-hmac")


def test_production_profile_rejects_plain_http_target_before_freezing() -> None:
    with pytest.raises(ValueError, match="production EXTERNAL_HTTP endpoint requires HTTPS"):
        freeze_external_http_binding(
            profile=_profile(),
            operation_identity="wms.inventory.confirm_inbound@v1",
            target_code="WMS_CONFIRM_INBOUND",
            endpoint_registry=EndpointRegistry({"WMS_CONFIRM_INBOUND": "http://wms/effects/inbound"}),
        )


def test_secret_provider_resolves_exact_version_and_revocation_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    reference = "secret://wms/effect-hmac@v1"
    provider = EnvironmentVersionedCredentialProvider(
        reference_env_names={reference: "WMS_EFFECT_HMAC_SECRET_V1"},
        settings_source=SimpleNamespace(WMS_EFFECT_HMAC_SECRET_V1="never-persist-this-secret"),
    )

    assert provider.resolve(reference) == b"never-persist-this-secret"
    with pytest.raises(LookupError, match="not configured"):
        provider.resolve("secret://wms/effect-hmac@v2")

    revoked_provider = replace(provider, revoked_references=frozenset({reference}))
    with pytest.raises(CredentialRevokedError) as exc_info:
        revoked_provider.resolve(reference)
    assert exc_info.value.code == "CREDENTIAL_REVOKED"
    assert reference not in str(exc_info.value)


def test_default_environment_provider_reads_explicit_mapping_and_revocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.sys import external_http_credentials

    reference = "secret://wms/material-flow-production-hmac@v1"
    monkeypatch.delenv("WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V1", raising=False)
    monkeypatch.setattr(
        external_http_credentials,
        "settings",
        SimpleNamespace(
            WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V1="resolved-secret",
            WES_REVOKED_EXTERNAL_HTTP_CREDENTIAL_REFERENCES="",
        ),
        raising=False,
    )
    provider = external_http_credentials.build_environment_external_http_credential_provider()
    assert provider.resolve(reference) == b"resolved-secret"

    monkeypatch.setattr(
        external_http_credentials,
        "settings",
        SimpleNamespace(
            WMS_MATERIAL_FLOW_PRODUCTION_HMAC_SECRET_V1="resolved-secret",
            WES_REVOKED_EXTERNAL_HTTP_CREDENTIAL_REFERENCES=reference,
        ),
    )
    revoked_provider = external_http_credentials.build_environment_external_http_credential_provider()
    with pytest.raises(CredentialRevokedError):
        revoked_provider.resolve(reference)


def test_environment_provider_freezes_credential_allowlist() -> None:
    reference = "secret://wms/effect-hmac@v1"
    source = {reference: "WMS_EFFECT_HMAC_SECRET_V1"}
    provider = EnvironmentVersionedCredentialProvider(
        reference_env_names=source,
        settings_source=SimpleNamespace(WMS_EFFECT_HMAC_SECRET_V1="secret"),
    )
    source[reference] = "ATTACKER_CONTROLLED_ENV"

    assert provider.reference_env_names[reference] == "WMS_EFFECT_HMAC_SECRET_V1"
    with pytest.raises(TypeError):
        provider.reference_env_names[reference] = "OTHER"  # type: ignore[index]


def test_dispatch_envelope_requires_and_carries_frozen_binding() -> None:
    frozen = _frozen_binding()
    canonical = CanonicalPayload.from_projection({"inbound_key": "IN-001"})

    envelope = DispatchEnvelope(
        dispatch_key="effect:IN-001",
        idempotency_key="intent:IN-001",
        dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
        target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
        target_code=frozen.target_snapshot.code,
        provider_profile_identity=frozen.provider_profile_identity,
        operation_identity=frozen.operation_identity,
        payload_json={"inbound_key": "IN-001"},
        operation_domain="WMS_INVENTORY",
        canonical_payload_bytes=canonical.body,
        payload_hash=canonical.sha256,
        frozen_binding=frozen,
    )

    assert envelope.frozen_binding is frozen
    with pytest.raises(ValueError, match="frozen binding"):
        DispatchEnvelope(
            dispatch_key="effect:IN-002",
            idempotency_key="intent:IN-002",
            dispatch_type=SystemOutboxDispatchType.EXTERNAL_HTTP,
            target_type=SystemOutboxTargetType.HTTP_ENDPOINT,
            target_code=frozen.target_snapshot.code,
            provider_profile_identity=frozen.provider_profile_identity,
            operation_identity=frozen.operation_identity,
            payload_json={"inbound_key": "IN-002"},
            operation_domain="WMS_INVENTORY",
            canonical_payload_bytes=CanonicalPayload.from_projection({"inbound_key": "IN-002"}).body,
            payload_hash=CanonicalPayload.from_projection({"inbound_key": "IN-002"}).sha256,
        )


def test_external_http_outbox_requires_frozen_fields_and_update_schema_hides_them() -> None:
    frozen = _frozen_binding()
    canonical = CanonicalPayload.from_projection({"inbound_key": "IN-001"})
    common = {
        "dispatch_type": SystemOutboxDispatchType.EXTERNAL_HTTP,
        "dispatch_key": "effect:IN-001",
        "idempotency_key": "intent:IN-001",
        "target_type": SystemOutboxTargetType.HTTP_ENDPOINT,
        "target_code": frozen.target_snapshot.code,
        "provider_profile_identity": frozen.provider_profile_identity,
        "operation_identity": frozen.operation_identity,
        "payload_json": {"inbound_key": "IN-001"},
        "canonical_payload_bytes": canonical.body,
        "payload_hash": canonical.sha256,
    }

    with pytest.raises(ValueError, match="frozen target and credential binding"):
        SystemOutbox(**common)
    outbox = SystemOutbox(**{**common, **frozen.as_persisted_fields()})
    assert outbox.target_snapshot_hash == frozen.target_snapshot_hash
    assert outbox.credential_reference == "secret://wms/effect-hmac@v1"
    assert {
        "provider_profile_hash",
        "binding_revision",
        "target_snapshot_json",
        "target_snapshot_hash",
        "auth_scheme",
        "credential_reference",
        "idempotency_key",
    }.isdisjoint(SystemOutboxUpdate.model_fields)


def test_frozen_delivery_binding_migration_is_schema_only_and_reversible() -> None:
    matches = list((ROOT / "migrations" / "versions").glob("*_freeze_external_http_delivery_binding.py"))
    assert len(matches) == 1
    source = matches[0].read_text(encoding="utf-8")

    assert 'down_revision: Union[str, Sequence[str], None] = "2c1407a3606e"' in source
    for column_name in (
        "provider_profile_hash",
        "binding_revision",
        "target_snapshot_json",
        "target_snapshot_hash",
        "auth_scheme",
        "credential_reference",
    ):
        assert f'"{column_name}"' in source
        assert f'op.drop_column("system_outbox", "{column_name}", schema="wes_biz")' in source
    assert '"ck_system_outbox_external_http_frozen_binding"' in source
    assert "op.execute" not in source
    assert "UPDATE " not in source.upper()
    assert "INSERT INTO " not in source.upper()
