"""真实 adapter、最小 simulator 与 replay factory 的同卷 conformance。"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from src.app.runtime.system_capabilities.wms.conformance_manifest import WMS_CONFORMANCE_MANIFEST
from src.app.runtime.system_capabilities.wms.provider_conformance import (
    QUERY_INVENTORY_CONFORMANCE_CASES,
    ConformanceObservation,
    build_wms_conformance_report,
)
from src.app.wms_integration.adapters.effect_status_query_adapter import WmsEffectStatusQueryAdapter
from src.app.wms_integration.operation_contract import WmsCompletionMode
from src.app.wms_integration.ports.effect_status import (
    WmsEffectStatusRequest,
    build_wms_effect_status_binding,
)
from src.app.wms_integration.ports.fulfillment_operations import RequestRackSupplyResult
from src.app.wms_integration.services.query_transport import WmsQueryCallPermit
from tests.support.wms_provider_conformance import (
    QUERY_INVENTORY_SCRIPT_FIXTURE,
    RecordingConformanceTarget,
    build_query_inventory_conformance_targets,
)
from tests.support.wms_provider_replay import (
    QUERY_INVENTORY_REPLAY_FIXTURE,
    QueryInventoryReplayFactory,
)

GENERATED_AT = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)
EFFECT_STATUS_REPLAY_FIXTURE = {
    "state": "COMPLETED",
    "provider_reference": "wms-conformance-effect-001",
    "reason_code": None,
    "updated_at": "2026-07-24T00:00:00+00:00",
    "source_version": 4,
    "result_payload": {
        "dispatch_key": "dispatch-conformance-001",
        "provider_reference": "wms-conformance-effect-001",
        "source_version": "4",
        "station_code": "STATION-CONFORMANCE-001",
        "rack_type": "FLOW_RACK",
        "demand_generation": 1,
        "rack_id": "RACK-CONFORMANCE-001",
        "final_station_code": "STATION-CONFORMANCE-001",
        "arrival_relation": "AT_STATION",
        "task_outcome": "SUCCESS",
    },
}


class _EffectStatusConformanceCredentialProvider:
    def resolve(self, _credential_reference: str) -> bytes:
        return b"test-only-effect-status-conformance"


class _EffectStatusConformanceEvidenceWriter:
    async def before_call(self, *, operation_identity: str, target_code: str) -> WmsQueryCallPermit:
        return WmsQueryCallPermit(allowed=True)

    async def record(self, **_kwargs) -> str:
        return "status:conformance:evidence"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target_factory",
    build_query_inventory_conformance_targets(),
    ids=lambda factory: factory.name,
)
async def test_every_target_runs_the_same_core_question_bank_without_override(target_factory) -> None:
    observations = tuple([await target_factory.execute(case) for case in QUERY_INVENTORY_SCRIPT_FIXTURE.cases])

    report = build_wms_conformance_report(
        cases=QUERY_INVENTORY_CONFORMANCE_CASES,
        observations=observations,
        target=target_factory.target,
        fixture_digest=target_factory.asset_digest,
        generated_at=GENERATED_AT,
    )

    expected_ids = tuple(case.case_id for case in QUERY_INVENTORY_CONFORMANCE_CASES)
    assert tuple(case.case_id for case in QUERY_INVENTORY_SCRIPT_FIXTURE.cases) == expected_ids
    assert target_factory.executed_case_ids == expected_ids
    assert report.passed is True
    assert all(case.passed for case in report.cases)


@pytest.mark.asyncio
async def test_replay_factory_is_pure_and_deterministic() -> None:
    first = QueryInventoryReplayFactory()
    second = QueryInventoryReplayFactory()

    first_run = tuple([await first.execute(case) for case in QUERY_INVENTORY_SCRIPT_FIXTURE.cases])
    second_run = tuple([await second.execute(case) for case in reversed(QUERY_INVENTORY_SCRIPT_FIXTURE.cases)])

    assert first_run == tuple(reversed(second_run))
    source = inspect.getsource(QueryInventoryReplayFactory).lower()
    assert "endpoint" not in source
    assert "credential" not in source
    assert "httpx" not in source
    assert "runtime_factory" not in source


@pytest.mark.asyncio
async def test_replay_uses_an_independent_fixed_asset_instead_of_current_question_bank_expectations() -> None:
    replay_fixture = QUERY_INVENTORY_REPLAY_FIXTURE

    assert replay_fixture is not None
    assert replay_fixture.digest != QUERY_INVENTORY_SCRIPT_FIXTURE.digest
    assert replay_fixture.verify() is True

    success = next(case for case in QUERY_INVENTORY_SCRIPT_FIXTURE.cases if case.case_id == "success")
    contradictory_case = success.model_copy(
        update={
            "recorded_observation": ConformanceObservation(
                case_id="success",
                outcome_kind="SUCCESS",
                evidence_recorded=True,
                semantic_marker="EMPTY",
            )
        }
    )
    observation = await QueryInventoryReplayFactory().execute(contradictory_case)

    assert observation.semantic_marker == "ONE_ITEM"
    assert "recorded_observation" not in inspect.getsource(QueryInventoryReplayFactory)


@pytest.mark.asyncio
async def test_execution_recording_belongs_to_the_test_wrapper_not_the_replay_factory() -> None:
    wrapper_type = RecordingConformanceTarget
    factory = QueryInventoryReplayFactory()

    assert wrapper_type is not None
    assert not hasattr(factory, "executed_case_ids")
    assert not hasattr(factory, "__dict__")

    wrapper = wrapper_type(factory)
    case = QUERY_INVENTORY_SCRIPT_FIXTURE.cases[0]
    await wrapper.execute(case)

    assert wrapper.executed_case_ids == (case.case_id,)


def test_script_fixture_is_frozen_verifiable_and_has_no_secret_or_header_schema() -> None:
    assert len(QUERY_INVENTORY_SCRIPT_FIXTURE.digest) == 64
    assert QUERY_INVENTORY_SCRIPT_FIXTURE.verify() is True
    serialized = QUERY_INVENTORY_SCRIPT_FIXTURE.model_dump_json().lower()
    assert "secret" not in serialized
    assert "credential" not in serialized
    assert "header" not in serialized
    with pytest.raises(ValidationError):
        QUERY_INVENTORY_SCRIPT_FIXTURE.digest = "b" * 64


def test_common_conformance_test_has_no_skip_or_xfail_escape_hatch() -> None:
    source = inspect.getsource(test_every_target_runs_the_same_core_question_bank_without_override)
    assert "pytest.mark.skip" not in source
    assert "pytest.mark.xfail" not in source
    assert "pytest.skip(" not in source


def test_every_effect_conformance_question_bank_contains_unsigned_status_query_case() -> None:
    async_requirements = tuple(
        requirement
        for requirement in WMS_CONFORMANCE_MANIFEST.operations
        if requirement.operation.completion_mode is WmsCompletionMode.ASYNC_TASK
    )
    sync_requirements = tuple(
        requirement
        for requirement in WMS_CONFORMANCE_MANIFEST.operations
        if requirement.operation.completion_mode is WmsCompletionMode.SYNC_RESULT
    )

    assert len(async_requirements) == 7
    assert len(sync_requirements) == 9
    assert all("status_query" in requirement.required_cases for requirement in async_requirements)
    assert all("status_query" not in requirement.required_cases for requirement in sync_requirements)
    serialized = repr(tuple(requirement.required_cases for requirement in async_requirements)).lower()
    assert "signature" not in serialized
    assert "credential" not in serialized


@pytest.mark.asyncio
async def test_unsigned_effect_status_case_replays_the_same_interaction_contract_deterministically() -> None:
    observed_query_params: list[dict[str, str]] = []

    async def replay(request: httpx.Request) -> httpx.Response:
        observed_query_params.append(dict(request.url.params))
        return httpx.Response(200, json=EFFECT_STATUS_REPLAY_FIXTURE)

    binding = build_wms_effect_status_binding(
        settings_source=SimpleNamespace(
            APP_ENV="test",
            WMS_MATERIAL_FLOW_ACTIVE_HMAC_VERSION="v2",
            WMS_EFFECT_STATUS_URL="https://conformance.invalid/northbound/operations/status",
            WMS_EFFECT_STATUS_TIMEOUT_SECONDS=2.0,
            WMS_EFFECT_STATUS_MAX_RESPONSE_BYTES=4096,
        )
    )
    adapter = WmsEffectStatusQueryAdapter(
        binding=binding,
        credential_provider=_EffectStatusConformanceCredentialProvider(),
        evidence_writer=_EffectStatusConformanceEvidenceWriter(),
        transport=httpx.MockTransport(replay),
        jitter=lambda _upper: 0.0,
        initial_backoff_seconds=1.0,
        max_backoff_seconds=8.0,
    )
    request = WmsEffectStatusRequest(
        operation_identity="wms.fulfillment.request_rack_supply@v1",
        idempotency_key="intent-conformance-001",
        request_payload={
            "dispatch_key": "dispatch-conformance-001",
            "station_code": "STATION-CONFORMANCE-001",
            "rack_type": "FLOW_RACK",
            "demand_generation": 1,
        },
    )

    first = await adapter.query_status(request)
    second = await adapter.query_status(request)

    assert first == second
    assert isinstance(first.result, RequestRackSupplyResult)
    assert observed_query_params == [
        {
            "operation_identity": "wms.fulfillment.request_rack_supply@v1",
            "idempotency_key": "intent-conformance-001",
        },
        {
            "operation_identity": "wms.fulfillment.request_rack_supply@v1",
            "idempotency_key": "intent-conformance-001",
        },
    ]
