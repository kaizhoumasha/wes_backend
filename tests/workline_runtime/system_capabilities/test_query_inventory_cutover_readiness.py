"""inventory QUERY 首次切换的真实 readiness 授权合同。"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from src.app.runtime.system_capabilities.query_inventory_cutover import (
    QUERY_INVENTORY_PRODUCTION_PROFILE,
    QueryInventoryCutoverReadinessRepository,
    QueryInventoryCutoverReadinessService,
)
from src.app.runtime.system_capabilities.shadow_readiness import (
    BoundedQueryShadowEvaluator,
    QueryShadowReadinessApproval,
    QueryShadowReadinessPolicy,
    ReadinessApprovalDecision,
    ReadinessGateError,
    ShadowDecision,
    ShadowVersionSet,
    build_query_shadow_expected,
    build_query_shadow_readiness_report,
)
from src.app.wms_integration.ports.query_inventory_operation import OPERATION_IDENTITY


def _ready_authorization() -> tuple[object, QueryShadowReadinessApproval]:
    observed_at = datetime(2026, 7, 22, tzinfo=UTC)
    versions = ShadowVersionSet(
        legacy_policy_version="rough-sorter-inventory-admission.v1",
        candidate_policy_version="rough-sorter-inventory-admission.v1",
        legacy_contract_version="wms.rough-sorter-inventory-admission.v1",
        candidate_contract_version=OPERATION_IDENTITY,
        normalization_version="inventory-query-normalization.v1",
        evaluator_version="query-shadow-evaluator.v1",
    )
    expected = build_query_shadow_expected(
        attempt_id="attempt-1",
        capability_key="rough_sorter",
        provider_profile_identity=QUERY_INVENTORY_PRODUCTION_PROFILE,
        operation_identity=OPERATION_IDENTITY,
        versions=versions,
        observed_at=observed_at,
        input_hash="a" * 64,
        output_hash="b" * 64,
    )
    decision = ShadowDecision(action="ADMIT", reason="WMS_ADMITTED", error_class="NONE")
    comparison = BoundedQueryShadowEvaluator().compare(
        expected=expected,
        legacy_decision=decision,
        candidate_decision=decision,
        legacy_policy_duration_ns=1_000,
        candidate_policy_duration_ns=1_000,
        query_end_to_end_duration_ms=5,
    )
    report = build_query_shadow_readiness_report(
        provider_profile_identity=QUERY_INVENTORY_PRODUCTION_PROFILE,
        operation_identity=OPERATION_IDENTITY,
        expected_samples=[expected],
        comparisons=[comparison],
        generated_at=observed_at,
        policy=QueryShadowReadinessPolicy(min_window_days=0, min_eligible_samples=1),
    )
    approval = QueryShadowReadinessApproval(
        report_id=report.report_id,
        decision=ReadinessApprovalDecision.GO,
        approved_by="release-owner",
        approved_at=observed_at,
    )
    return report, approval


class _ScalarResult:
    def __init__(self, value: object | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object | None:
        return self._value


class _Database:
    def __init__(self, *rows: object | None) -> None:
        self._rows = iter(rows)

    async def execute(self, _statement: object) -> _ScalarResult:
        return _ScalarResult(next(self._rows))


def _persisted_rows(*, report_json: dict[str, object], generated_at: object) -> tuple[object, object]:
    report, approval = _ready_authorization()
    return (
        SimpleNamespace(
            report_id=report.report_id,
            generated_at=generated_at,
            provider_profile_identity=report.provider_profile_identity,
            operation_identity=report.operation_identity,
            verdict=report.verdict.value,
            report_json=report_json,
        ),
        SimpleNamespace(
            report_id=approval.report_id,
            decision=approval.decision.value,
            approved_by=approval.approved_by,
            approved_at=approval.approved_at.replace(tzinfo=None),
        ),
    )


@pytest.mark.asyncio
async def test_repository_accepts_only_normalized_generated_at_for_latest_db_row() -> None:
    report, _approval = _ready_authorization()
    report_json = report.model_dump(mode="json")
    report_json["generated_at"] = "2026-07-22T08:00:00+08:00"
    rows = _persisted_rows(report_json=report_json, generated_at=datetime(2026, 7, 22))

    authorization = await QueryInventoryCutoverReadinessRepository().load_latest_authorization(
        _Database(*rows),
        provider_profile_identity=QUERY_INVENTORY_PRODUCTION_PROFILE,
        operation_identity=OPERATION_IDENTITY,
    )

    assert authorization is not None
    assert authorization[0].generated_at == datetime(2026, 7, 22, tzinfo=UTC)


@pytest.mark.asyncio
async def test_repository_rejects_report_json_generated_at_that_differs_from_selected_db_row() -> None:
    report, _approval = _ready_authorization()
    report_json = report.model_dump(mode="json")
    rows = _persisted_rows(report_json=report_json, generated_at=datetime(2026, 7, 22, 0, 0, 1))

    with pytest.raises(ReadinessGateError, match="generated_at"):
        await QueryInventoryCutoverReadinessRepository().load_latest_authorization(
            _Database(*rows),
            provider_profile_identity=QUERY_INVENTORY_PRODUCTION_PROFILE,
            operation_identity=OPERATION_IDENTITY,
        )


@pytest.mark.parametrize("generated_at", [None, "not-a-timestamp"])
@pytest.mark.asyncio
async def test_repository_rejects_missing_or_malformed_report_json_generated_at(generated_at: object) -> None:
    report, _approval = _ready_authorization()
    report_json = report.model_dump(mode="json")
    if generated_at is None:
        report_json.pop("generated_at")
    else:
        report_json["generated_at"] = generated_at
    rows = _persisted_rows(report_json=report_json, generated_at=datetime(2026, 7, 22))

    with pytest.raises(ReadinessGateError, match="metadata"):
        await QueryInventoryCutoverReadinessRepository().load_latest_authorization(
            _Database(*rows),
            provider_profile_identity=QUERY_INVENTORY_PRODUCTION_PROFILE,
            operation_identity=OPERATION_IDENTITY,
        )


@pytest.mark.parametrize("generated_at", [None, "not-a-timestamp"])
@pytest.mark.asyncio
async def test_repository_rejects_missing_or_malformed_db_generated_at(generated_at: object) -> None:
    report, _approval = _ready_authorization()
    rows = _persisted_rows(report_json=report.model_dump(mode="json"), generated_at=generated_at)

    with pytest.raises(ReadinessGateError, match="metadata"):
        await QueryInventoryCutoverReadinessRepository().load_latest_authorization(
            _Database(*rows),
            provider_profile_identity=QUERY_INVENTORY_PRODUCTION_PROFILE,
            operation_identity=OPERATION_IDENTITY,
        )


class _Repository:
    def __init__(self, authorization: tuple[object, QueryShadowReadinessApproval] | None) -> None:
        self.authorization = authorization
        self.calls: list[tuple[str, str]] = []

    async def load_latest_authorization(
        self,
        _db: object,
        *,
        provider_profile_identity: str,
        operation_identity: str,
    ) -> tuple[object, QueryShadowReadinessApproval] | None:
        self.calls.append((provider_profile_identity, operation_identity))
        return self.authorization


@pytest.mark.asyncio
async def test_production_cutover_accepts_same_immutable_ready_go_report() -> None:
    repository = _Repository(_ready_authorization())

    await QueryInventoryCutoverReadinessService(repository).require_ready(object(), app_env="prod")

    assert repository.calls == [(QUERY_INVENTORY_PRODUCTION_PROFILE, OPERATION_IDENTITY)]


@pytest.mark.asyncio
async def test_production_cutover_rejects_missing_authorization() -> None:
    with pytest.raises(ReadinessGateError, match="authorization is missing"):
        await QueryInventoryCutoverReadinessService(_Repository(None)).require_ready(object(), app_env="prod")


@pytest.mark.asyncio
async def test_production_cutover_rejects_approval_for_another_report() -> None:
    report, approval = _ready_authorization()
    repository = _Repository((report, approval.model_copy(update={"report_id": "f" * 64})))

    with pytest.raises(ReadinessGateError, match="report ID does not match"):
        await QueryInventoryCutoverReadinessService(repository).require_ready(object(), app_env="prod")


@pytest.mark.asyncio
async def test_non_production_runtime_does_not_claim_production_authorization() -> None:
    repository = _Repository(None)

    await QueryInventoryCutoverReadinessService(repository).require_ready(object(), app_env="test")

    assert repository.calls == []
