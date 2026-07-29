"""P0-006 external contract profile fixtures 必须真实存在且可校验。

测试覆盖从 tests.support.external_contract_profile 切到生产共享层
src.app.contracts.external_contract_profile 后，fixture 仍能通过
provider_simulator_registry 校验。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.app.contracts.external_contract_profile import (
    ExternalContractProfile,
    FixtureCase,
    FixtureSet,
)
from src.app.wms_integration.provider_simulator_registry import (
    ProviderSimulatorRegistry,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "external_contracts" / "wms" / "default"
REQUIRED_CASES = {"success", "reject", "timeout", "duplicate", "missing_event_id"}


def _wms_profile() -> ExternalContractProfile:
    """构造 WMS sandbox 测试 profile (默认 fixture set 配套)。"""
    return ExternalContractProfile(
        provider_code="WMS",
        contract_version="2026-06-25",
        environment="sandbox",
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

    TypeAdapter(ExternalContractProfile).validate_python(
        {**_wms_profile().model_dump(), "fixture_set_path": str(tmp_path)}
    )
    # 构造新 profile with temp path
    base = _wms_profile().model_dump()
    base["fixture_set_path"] = str(tmp_path)
    new_profile = ExternalContractProfile(**base)
    registry = ProviderSimulatorRegistry(new_profile, repo_root=REPO_ROOT)
    with pytest.raises(ValueError, match="provider_code"):
        registry.load()


def test_provider_simulator_registry_rejects_missing_required_cases():
    """fixture 缺失 required_cases 必含 case 应拒绝。"""
    # model_validate 而非赋值, 避免 frozen 限制
    base = _wms_profile().model_dump()
    base["fixture_set_required_cases"] = ["success", "reject", "nonexistent_case"]
    new_profile = ExternalContractProfile(**base)
    registry = ProviderSimulatorRegistry(new_profile, repo_root=REPO_ROOT)
    with pytest.raises(ValueError, match="缺失"):
        registry.load()


def test_provider_simulator_registry_get_case_raises_on_missing():
    """get_case 找不到 case_id 抛 KeyError。"""
    registry = ProviderSimulatorRegistry(_wms_profile(), repo_root=REPO_ROOT)
    registry.load()
    with pytest.raises(KeyError, match="nonexistent"):
        registry.get_case("nonexistent")
