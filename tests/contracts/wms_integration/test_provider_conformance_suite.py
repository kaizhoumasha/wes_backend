"""真实 adapter、最小 simulator 与 replay factory 的同卷 conformance。"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.app.runtime.system_capabilities.wms.provider_catalog import WMS_PROVIDER_PROFILES
from src.app.runtime.system_capabilities.wms.provider_conformance import (
    QUERY_INVENTORY_CONFORMANCE_CASES,
    ConformanceObservation,
    build_wms_conformance_report,
)
from tests.support import wms_provider_conformance as conformance_support
from tests.support.wms_provider_conformance import (
    QUERY_INVENTORY_SCRIPT_FIXTURE,
    QueryInventoryReplayFactory,
    build_query_inventory_conformance_targets,
)

SANDBOX_PROFILE = WMS_PROVIDER_PROFILES["wms.2026-07-06.material-flow.sandbox"]
GENERATED_AT = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)


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
        profile=SANDBOX_PROFILE,
        fixture_digest=QUERY_INVENTORY_SCRIPT_FIXTURE.digest,
        endpoint_revision=None,
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
    replay_fixture = getattr(conformance_support, "QUERY_INVENTORY_REPLAY_FIXTURE", None)

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
    wrapper_type = getattr(conformance_support, "RecordingConformanceTarget", None)
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
