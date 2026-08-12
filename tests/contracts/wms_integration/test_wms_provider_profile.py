"""WMS Provider profile typed Settings 与 fail-closed parser 合同。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.app.wms_integration.operation_registry import WMS_OPERATIONS
from tests.contracts.wms_integration.provider_profile_support import (
    build_provider_profile_payload,
    changed_profile_payload,
    write_provider_profile,
)


def test_profile_accepts_exact_static_registry_and_rejects_unknown_fields() -> None:
    from src.app.wms_integration.provider_profile import WmsProviderProfileSettings

    profile = WmsProviderProfileSettings.model_validate(build_provider_profile_payload())
    assert tuple(profile.operations) == tuple(operation.identity for operation in WMS_OPERATIONS)
    assert len(profile.operations) == 29
    assert profile.profile.identity == "wms.2026-07-28.full-factory"

    invalid = changed_profile_payload(legacy_endpoint="/forbidden")
    with pytest.raises(ValidationError, match="legacy_endpoint"):
        WmsProviderProfileSettings.model_validate(invalid)


def test_profile_rejects_external_environment_dimension() -> None:
    from src.app.wms_integration.provider_profile import WmsProviderProfileSettings

    payload = build_provider_profile_payload()
    payload["profile"]["environment"] = "production"

    with pytest.raises(ValidationError, match="environment"):
        WmsProviderProfileSettings.model_validate(payload)


def test_profile_coverage_does_not_depend_on_yaml_mapping_order() -> None:
    from src.app.wms_integration.provider_profile import WmsProviderProfileSettings

    payload = build_provider_profile_payload()
    payload["operations"] = dict(reversed(tuple(payload["operations"].items())))

    profile = WmsProviderProfileSettings.model_validate(payload)

    assert set(profile.operations) == {operation.identity for operation in WMS_OPERATIONS}


def test_profile_operation_bindings_are_deeply_read_only() -> None:
    from src.app.wms_integration.provider_profile import WmsProviderOperationPathSettings, WmsProviderProfileSettings

    profile = WmsProviderProfileSettings.model_validate(build_provider_profile_payload())
    identity = next(iter(profile.operations))

    with pytest.raises(TypeError):
        profile.operations[identity] = WmsProviderOperationPathSettings(path="/mutated")


@pytest.mark.parametrize("mutation", ["missing", "unknown"])
def test_profile_rejects_missing_or_unknown_operation_identity(mutation: str) -> None:
    from src.app.wms_integration.provider_profile import WmsProviderProfileSettings

    payload = build_provider_profile_payload()
    if mutation == "missing":
        payload["operations"].pop(next(iter(payload["operations"])))
    else:
        payload["operations"]["wms.unknown.operation@v1"] = {"path": "/api/wms/unknown"}

    with pytest.raises(ValidationError, match="exactly cover the static WMS operation registry"):
        WmsProviderProfileSettings.model_validate(payload)


def test_profile_parser_rejects_duplicate_operation_identity(tmp_path) -> None:
    from src.app.wms_integration.provider_profile import load_wms_provider_profile

    payload = build_provider_profile_payload()
    profile_file = write_provider_profile(tmp_path / "provider.yaml", payload)
    duplicate_identity = next(iter(payload["operations"]))
    with profile_file.open("a", encoding="utf-8") as stream:
        stream.write(f"  {duplicate_identity}:\n    path: /duplicate\n")

    with pytest.raises(ValueError, match="duplicate key"):
        load_wms_provider_profile(profile_file)


def test_profile_loader_returns_validated_provider_profile(tmp_path) -> None:
    from src.app.wms_integration.provider_profile import load_wms_provider_profile

    profile_file = write_provider_profile(
        tmp_path / "provider.yaml",
        build_provider_profile_payload(),
    )

    profile = load_wms_provider_profile(profile_file)

    assert profile.profile.identity == "wms.2026-07-28.full-factory"
    assert tuple(profile.operations) == tuple(operation.identity for operation in WMS_OPERATIONS)


def test_profile_rejects_contract_drift_and_mode_specific_path_fields() -> None:
    from src.app.wms_integration.provider_profile import WmsProviderProfileSettings

    wrong_version = deepcopy(build_provider_profile_payload())
    wrong_version["profile"]["contract_version"] = "2026-07-29.drift"
    with pytest.raises(ValidationError, match="contract_version"):
        WmsProviderProfileSettings.model_validate(wrong_version)

    query_with_submit = build_provider_profile_payload()
    query_identity = WMS_OPERATIONS[0].identity
    query_with_submit["operations"][query_identity] = {"submit_path": "/api/wms/not-a-query"}
    with pytest.raises(ValidationError, match="QUERY operation requires path"):
        WmsProviderProfileSettings.model_validate(query_with_submit)

    effect_with_path = build_provider_profile_payload()
    effect_identity = WMS_OPERATIONS[-1].identity
    effect_with_path["operations"][effect_identity] = {"path": "/api/wms/not-an-effect"}
    with pytest.raises(ValidationError, match="EFFECT operation requires submit_path"):
        WmsProviderProfileSettings.model_validate(effect_with_path)


def test_profile_authentication_combinations_are_closed() -> None:
    from src.app.wms_integration.provider_profile import WmsProviderProfileSettings

    assert WmsProviderProfileSettings.model_validate(build_provider_profile_payload()).outbound_auth.scheme == "NONE"

    none_with_credential = build_provider_profile_payload()
    none_with_credential["outbound_auth"]["credential_reference"] = "secret://wms/factory@v1"
    with pytest.raises(ValidationError, match="NONE auth must not carry credential"):
        WmsProviderProfileSettings.model_validate(none_with_credential)

    none_outside_lan = changed_profile_payload(network_trust_mode="authenticated_network")
    with pytest.raises(ValidationError, match="isolated_lan"):
        WmsProviderProfileSettings.model_validate(none_outside_lan)

    inbound_none_outside_lan = changed_profile_payload(network_trust_mode="authenticated_network")
    inbound_none_outside_lan["outbound_auth"] = {
        "scheme": "HMAC_SHA256",
        "credential_reference": "secret://wms/outbound@v1",
    }
    with pytest.raises(ValidationError, match="isolated_lan"):
        WmsProviderProfileSettings.model_validate(inbound_none_outside_lan)

    hmac_without_reference = build_provider_profile_payload()
    hmac_without_reference["outbound_auth"] = {"scheme": "HMAC_SHA256"}
    with pytest.raises(ValidationError, match="versioned credential_reference"):
        WmsProviderProfileSettings.model_validate(hmac_without_reference)

    valid_hmac = build_provider_profile_payload()
    valid_hmac["outbound_auth"] = {
        "scheme": "HMAC_SHA256",
        "credential_reference": "secret://wms/factory-hmac@v2",
    }
    valid_hmac["inbound_auth"] = {
        "scheme": "HMAC_SHA256",
        "credential_reference": "secret://wms/inbound-hmac@v2",
    }
    valid_hmac["network_trust_mode"] = "authenticated_network"
    assert WmsProviderProfileSettings.model_validate(valid_hmac).outbound_auth.scheme == "HMAC_SHA256"


def test_profile_parser_rejects_non_mapping_yaml(tmp_path) -> None:
    from src.app.wms_integration.provider_profile import load_wms_provider_profile

    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(["not", "a", "profile"]), encoding="utf-8")

    with pytest.raises(TypeError, match="mapping"):
        load_wms_provider_profile(path)


def test_profile_parser_rejects_relative_or_unreadable_files(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.app.wms_integration.provider_profile import load_wms_provider_profile

    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="absolute path"):
        load_wms_provider_profile(Path("provider.yaml"))

    with pytest.raises(ValueError, match="not readable"):
        load_wms_provider_profile(tmp_path / "missing.yaml")
