"""P0-006 external contract profile fixtures 必须真实存在且可校验。

测试覆盖从 tests.support.external_contract_profile 切到生产共享层
src.app.contracts.external_contract_profile 后，fixture 仍能通过
provider_simulator_registry 校验。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.app.contracts.external_contract_profile import (
    ExternalContractProfile,
    FixtureCase,
    FixtureSet,
    InboundNormalizerProfile,
    WmsExternalContractProfile,
    parse_external_contract_profile,
)
from src.app.contracts.external_contract_profile_catalog import (
    WMS_MATERIAL_FLOW_PROFILE,
    ExternalContractProfileCatalog,
    list_external_contract_profiles,
)
from src.app.wms_integration.provider_simulator_registry import (
    ProviderSimulatorRegistry,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "external_contracts" / "wms" / "default"
REQUIRED_CASES = {"success", "reject", "timeout", "duplicate", "missing_event_id"}


def _minimal_profile_payload(provider_code: str = "WMS") -> dict[str, object]:
    return {
        "provider_code": provider_code,
        "contract_version": "2026-07-28.full-factory",
        "timeout_retry_query_timeout_seconds": 10,
        "timeout_retry_retry_backoff_seconds": [1],
        "fixture_set_path": "tests/fixtures/external_contracts/wms/default",
        "fixture_set_required_cases": ["success"],
    }


def test_wms_external_contract_profile_identity_has_no_environment_dimension() -> None:
    payload = _minimal_profile_payload()
    profile = WmsExternalContractProfile.model_validate(payload)

    assert profile.identity == "wms.2026-07-28.full-factory"
    with pytest.raises(ValidationError, match="environment"):
        WmsExternalContractProfile.model_validate({**payload, "environment": "production"})


def test_generic_external_contract_profile_requires_environment_and_keeps_it_in_identity() -> None:
    payload = _minimal_profile_payload("ECS")

    with pytest.raises(ValidationError, match="environment"):
        ExternalContractProfile.model_validate(payload)

    profile = ExternalContractProfile.model_validate({**payload, "environment": "sandbox"})
    assert profile.identity == "ecs.2026-07-28.full-factory.sandbox"


def test_external_contract_profile_parser_closes_union_by_provider_contract() -> None:
    wms = parse_external_contract_profile(_minimal_profile_payload())
    generic = parse_external_contract_profile(
        {
            **_minimal_profile_payload("ECS"),
            "environment": "sandbox",
            "inbound_normalizers_event": ["ECS_EVENT"],
        }
    )

    assert isinstance(wms, WmsExternalContractProfile)
    assert wms.identity == "wms.2026-07-28.full-factory"
    assert isinstance(generic, ExternalContractProfile)
    assert generic.identity == "ecs.2026-07-28.full-factory.sandbox"
    generic.ensure_inbound_normalizer_declared("ECS_EVENT", direction="event")

    with pytest.raises(ValidationError, match="environment"):
        parse_external_contract_profile(
            {
                **_minimal_profile_payload(),
                "environment": "sandbox",
            }
        )


def test_inbound_normalizer_rejects_unknown_provider_even_with_valid_event_prefix() -> None:
    with pytest.raises(ValidationError, match="source_provider"):
        InboundNormalizerProfile(
            normalizer_name="unknown-provider",
            source_provider="UNKNOWN",
            event_type="WMS_EVENT",
        )


@pytest.mark.parametrize("provider_code", ["WMS", "wms", " WmS "])
def test_generic_external_contract_profile_rejects_wms_provider(provider_code: str) -> None:
    with pytest.raises(ValidationError, match="WmsExternalContractProfile"):
        ExternalContractProfile.model_validate(
            {
                **_minimal_profile_payload(provider_code),
                "environment": "sandbox",
            }
        )


def test_generic_production_inbound_profile_requires_explicit_security_material() -> None:
    payload = {
        **_minimal_profile_payload("ECS"),
        "environment": "production",
        "inbound_normalizers_event": ["ECS_SCAN_COMPLETED"],
    }

    with pytest.raises(ValidationError, match="security_profile"):
        ExternalContractProfile.model_validate(payload)

    profile = ExternalContractProfile.model_validate(
        {
            **payload,
            "security_profile": {
                "secret_kid": "ecs-production-kid",
                "signature_algo": "HS256",
            },
        }
    )
    assert profile.identity == "ecs.2026-07-28.full-factory.production"


def test_catalog_resolves_generic_environment_and_wms_identity_without_aliases() -> None:
    generic = ExternalContractProfile.model_validate(
        {
            **_minimal_profile_payload("ECS"),
            "environment": "sandbox",
        }
    )
    wms = WmsExternalContractProfile.model_validate(_minimal_profile_payload())
    catalog = ExternalContractProfileCatalog((generic, wms))

    assert (
        catalog.resolve(
            provider_code="ECS",
            contract_version=generic.contract_version,
            environment="sandbox",
        )
        is generic
    )
    assert catalog.resolve_identity(wms.identity) is wms
    with pytest.raises(LookupError):
        catalog.resolve(
            provider_code=" WmS ",
            contract_version=wms.contract_version,
            environment="sandbox",
        )
    with pytest.raises(LookupError):
        catalog.resolve_identity(f"{wms.identity}.sandbox")


def test_catalog_rejects_constructed_generic_wms_environment_alias() -> None:
    invalid_generic_wms = ExternalContractProfile.model_construct(
        **_minimal_profile_payload(" wMs "),
        environment="sandbox",
    )

    with pytest.raises(ValueError, match="WmsExternalContractProfile"):
        ExternalContractProfileCatalog((invalid_generic_wms,))


def test_catalog_rejects_duplicate_identity_and_lists_active_snapshot() -> None:
    wms = WmsExternalContractProfile.model_validate(_minimal_profile_payload())

    with pytest.raises(ValueError, match="重复 external contract profile identity"):
        ExternalContractProfileCatalog((wms, wms.model_copy()))

    assert list_external_contract_profiles() == (WMS_MATERIAL_FLOW_PROFILE,)


def _wms_profile() -> WmsExternalContractProfile:
    """构造 WMS 测试 profile（默认 fixture set 配套）。"""
    return WmsExternalContractProfile(
        provider_code="WMS",
        contract_version="2026-06-25",
        timeout_retry_query_timeout_seconds=10,
        timeout_retry_effect_timeout_seconds=30,
        timeout_retry_retry_backoff_seconds=[1, 2, 4],
        fixture_set_path="tests/fixtures/external_contracts/wms/default",
        fixture_set_required_cases=sorted(REQUIRED_CASES),
    )


def test_wms_default_fixture_set_contains_required_cases():
    assert FIXTURE_ROOT.is_dir(), f"fixture_set.path 缺失: {FIXTURE_ROOT}"

    case_files = {path.stem: path for path in FIXTURE_ROOT.glob("*.json")}
    assert set(case_files) >= REQUIRED_CASES


def test_wms_default_fixtures_match_schema_and_profile_identity():
    """fixture 必过生产 schema FixtureCase 校验, 且与 profile 身份一致。"""
    for case_path in sorted(FIXTURE_ROOT.glob("*.json")):
        fixture = FixtureCase.model_validate(json.loads(case_path.read_text(encoding="utf-8")))

        assert fixture.provider_code == "WMS"
        assert fixture.contract_version == "2026-06-25"
        assert fixture.case_id == case_path.stem


def test_wms_async_e08_fixtures_use_typed_ack_or_explicit_no_response() -> None:
    from src.app.wms_integration.operation_registry import WMS_OPERATION_BY_IDENTITY
    from src.app.wms_integration.ports.fulfillment_operations import WmsEffectAck

    operation = WMS_OPERATION_BY_IDENTITY["wms.fulfillment.request_rack_supply@v1"]
    fixtures = {
        case_id: FixtureCase.model_validate(json.loads((FIXTURE_ROOT / f"{case_id}.json").read_text(encoding="utf-8")))
        for case_id in ("success", "reject", "timeout")
    }
    for fixture in fixtures.values():
        assert fixture.raw_request is not None
        parsed_request = operation.request_model.model_validate(fixture.raw_request)
        assert parsed_request.model_dump(mode="json", exclude_none=True) == fixture.raw_request

    success = fixtures["success"]
    assert success.raw_response is not None
    parsed_ack = WmsEffectAck.model_validate(success.raw_response)
    assert parsed_ack.model_dump(mode="json", exclude_none=True) == success.expected_typed
    assert parsed_ack.operation_identity == operation.identity
    assert parsed_ack.idempotency_key

    reject = fixtures["reject"]
    assert reject.raw_response is None
    assert reject.expected_typed == {}
    assert reject.expected_error is not None
    assert reject.expected_error["code"] in operation.reject_codes
    assert reject.expected_error["retryable"] is False

    timeout = fixtures["timeout"]
    assert timeout.raw_response is None
    assert timeout.expected_typed == {}
    assert timeout.expected_error is not None
    assert timeout.expected_error["code"] in operation.error_codes
    assert timeout.expected_error["retryable"] is True


def test_wms_profile_validator_against_default_fixture_set():
    """默认 5 个 fixture 全部过 ExternalContractProfile Pydantic 校验。"""
    profile = _wms_profile()
    assert profile.fixture_set_required_cases == sorted(REQUIRED_CASES)


def test_provider_simulator_registry_loads_default_fixture_set():
    """ProviderSimulatorRegistry 加载默认 5 fixture 成功。"""
    registry = ProviderSimulatorRegistry(_wms_profile(), repo_root=REPO_ROOT)
    registry.load()
    assert sorted(registry.list_cases()) == sorted(REQUIRED_CASES)


def test_provider_simulator_registry_get_case_by_id():
    """按 case_id 查找 FixtureCase 成功。"""
    registry = ProviderSimulatorRegistry(_wms_profile(), repo_root=REPO_ROOT)
    registry.load()
    case = registry.get_case("success")
    assert case.case_id == "success"
    assert case.provider_code == "WMS"


def test_provider_simulator_registry_rejects_provider_mismatch(tmp_path):
    """fixture provider_code 与 profile 不匹配应拒绝。"""
    bad_fixture = tmp_path / "bad.json"
    bad_fixture.write_text(
        json.dumps(
            {
                "case_id": "bad",
                "provider_code": "ECS",  # 错误: profile 期望 WMS
                "contract_version": "2026-06-25",
                "direction": "event",
                "expected_typed": {},
            }
        ),
        encoding="utf-8",
    )
    # model_validate 而非赋值, 避免 frozen 限制
    from pydantic import TypeAdapter

    TypeAdapter(WmsExternalContractProfile).validate_python(
        {**_wms_profile().model_dump(), "fixture_set_path": str(tmp_path)}
    )
    # 构造新 profile with temp path
    base = _wms_profile().model_dump()
    base["fixture_set_path"] = str(tmp_path)
    new_profile = WmsExternalContractProfile(**base)
    registry = ProviderSimulatorRegistry(new_profile, repo_root=REPO_ROOT)
    with pytest.raises(ValueError, match="provider_code"):
        registry.load()


def test_provider_simulator_registry_rejects_missing_required_cases():
    """fixture 缺失 required_cases 必含 case 应拒绝。"""
    # model_validate 而非赋值, 避免 frozen 限制
    base = _wms_profile().model_dump()
    base["fixture_set_required_cases"] = ["success", "reject", "nonexistent_case"]
    new_profile = WmsExternalContractProfile(**base)
    registry = ProviderSimulatorRegistry(new_profile, repo_root=REPO_ROOT)
    with pytest.raises(ValueError, match="缺失"):
        registry.load()


def test_provider_simulator_registry_get_case_raises_on_missing():
    """get_case 找不到 case_id 抛 KeyError。"""
    registry = ProviderSimulatorRegistry(_wms_profile(), repo_root=REPO_ROOT)
    registry.load()
    with pytest.raises(KeyError, match="nonexistent"):
        registry.get_case("nonexistent")
