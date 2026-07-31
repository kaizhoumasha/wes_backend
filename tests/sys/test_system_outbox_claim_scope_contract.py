"""G3 SystemOutbox 三 scope 的静态 claim 合同。"""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.app.sys.dispatch_concurrency import (
    DispatchBucketKey,
    DispatchBucketPolicy,
    DispatchBucketState,
    DispatchPolicyRegistry,
    FairDispatchScheduler,
)
from src.app.sys.models.outbox import SystemOutbox
from src.app.sys.repositories.outbox_repository import SystemOutboxRepository
from src.app.wms_integration.operation_contract import WmsExecutionLane
from src.app.wms_integration.operation_registry import EFFECT_OPERATIONS
from src.utils.timezone import timezone


def test_task_composition_derives_three_exhaustive_disjoint_claim_scopes_from_registry() -> None:
    try:
        module = import_module("src.celery_app.outbox_dispatch_composition")
    except ModuleNotFoundError:
        pytest.fail("G3 task composition root is missing", pytrace=False)

    scopes = module.build_outbox_claim_scopes()
    all_effects = frozenset(operation.identity for operation in EFFECT_OPERATIONS)
    data_effects = frozenset(
        operation.identity for operation in EFFECT_OPERATIONS if operation.execution_lane is WmsExecutionLane.WMS_DATA
    )
    fulfillment_effects = all_effects - data_effects

    assert tuple(scopes) == (
        module.OutboxClaimScopeName.SYSTEM,
        module.OutboxClaimScopeName.WMS_DATA,
        module.OutboxClaimScopeName.WMS_DISPATCH,
    )
    assert scopes[module.OutboxClaimScopeName.SYSTEM].included_operation_identities is None
    assert scopes[module.OutboxClaimScopeName.SYSTEM].excluded_operation_identities == all_effects
    assert scopes[module.OutboxClaimScopeName.WMS_DATA].included_operation_identities == data_effects
    assert scopes[module.OutboxClaimScopeName.WMS_DISPATCH].included_operation_identities == fulfillment_effects
    assert data_effects.isdisjoint(fulfillment_effects)
    assert data_effects | fulfillment_effects == all_effects
    assert "WMS_FULFILLMENT" not in module.OutboxClaimScopeName.__members__


def test_repository_identity_scope_builds_include_and_exclude_predicates_without_lane_column() -> None:
    repository = SystemOutboxRepository()
    columns = SystemOutbox.__table__.c

    assert not hasattr(columns, "execution_lane")
    assert hasattr(repository, "_operation_identity_predicates"), (
        "G3 repository must filter the existing operation_identity column"
    )
    predicates = repository._operation_identity_predicates(  # type: ignore[attr-defined]
        columns,
        operation_identities=("wms.inventory.confirm_inbound@v1",),
        exclude_operation_identities=("wms.fulfillment.request_rack_supply@v1",),
    )
    compiled = " AND ".join(str(predicate.compile(compile_kwargs={"literal_binds": True})) for predicate in predicates)

    assert "operation_identity IN ('wms.inventory.confirm_inbound@v1')" in compiled
    assert "operation_identity NOT IN ('wms.fulfillment.request_rack_supply@v1')" in compiled


class _ScopeCapturingRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.bucket = DispatchBucketKey("wms.profile", "wms.inventory.confirm_inbound@v1")
        self.state = DispatchBucketState(
            key=self.bucket,
            backlog_count=1,
            active_lease_count=0,
            recent_attempt_count=0,
            oldest_created_at=timezone.now_for_db(),
            unknown_count=0,
        )

    async def list_dispatch_bucket_keys(self, _db: object, **kwargs: Any) -> tuple[DispatchBucketKey, ...]:
        self.calls.append(("list", kwargs))
        return (self.bucket,)

    async def get_dispatch_bucket_state(
        self,
        _db: object,
        *,
        bucket: DispatchBucketKey,
        **kwargs: Any,
    ) -> DispatchBucketState:
        self.calls.append(("state", kwargs))
        return self.state

    async def try_lock_dispatch_bucket(self, _db: object, *, bucket: DispatchBucketKey) -> bool:
        return bucket is self.bucket

    async def claim_next_in_bucket(self, _db: object, **kwargs: Any) -> None:
        self.calls.append(("claim", kwargs))


@pytest.mark.asyncio
async def test_scheduler_propagates_one_identity_scope_to_recovery_bucket_metrics_and_claim() -> None:
    repository = _ScopeCapturingRepository()
    expired_http = SimpleNamespace(fence_expired_leases=AsyncMock(return_value=0))
    exhausted_non_http = SimpleNamespace(fence_exhausted_leases=AsyncMock(return_value=0))
    scheduler = FairDispatchScheduler(
        repository=repository,
        policy_registry=DispatchPolicyRegistry(
            default_policy=DispatchBucketPolicy(max_concurrency=1, rate_limit=10, batch_size=1)
        ),
        worker_identity="wms-data-test",
        expired_http_lease_loss_service=expired_http,
        exhausted_non_http_lease_service=exhausted_non_http,
    )
    identities = ("wms.inventory.confirm_inbound@v1",)

    await scheduler.claim(
        object(),
        limit=1,
        operation_identities=identities,
        exclude_operation_identities=(),
    )

    expected_scope = {
        "operation_identities": identities,
        "exclude_operation_identities": (),
    }
    expired_http.fence_expired_leases.assert_awaited_once()
    assert {key: expired_http.fence_expired_leases.await_args.kwargs[key] for key in expected_scope} == expected_scope
    exhausted_non_http.fence_exhausted_leases.assert_awaited_once()
    assert {
        key: exhausted_non_http.fence_exhausted_leases.await_args.kwargs[key] for key in expected_scope
    } == expected_scope
    assert repository.calls
    for _name, kwargs in repository.calls:
        assert {key: kwargs[key] for key in expected_scope} == expected_scope
