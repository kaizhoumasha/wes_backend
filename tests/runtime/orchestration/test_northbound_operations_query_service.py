"""北向只读运维入口的租户作用域与审计合同。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.exceptions import PermissionException


@pytest.mark.asyncio
async def test_tenant_principal_reads_only_owner_scoped_operational_snapshot() -> None:
    from src.app.runtime.orchestration.operational_models import NorthboundOperationalPrincipal
    from src.app.runtime.orchestration.services.query.northbound_operations_query_service import (
        NorthboundOperationsQueryService,
    )

    bucket = SimpleNamespace(
        provider_profile_identity="wms.2026-07-28.full-factory",
        operation_identity="wms.inventory.query_inventory@v1",
        mode="QUERY",
        backlog_count=1,
        active_lease_count=0,
        unknown_count=0,
        oldest_queue_age_seconds=3,
        rate_limited_count=0,
        lease_loss_count=0,
        reconciliation_open_count=0,
    )
    repository = SimpleNamespace(
        workline_is_owned_by=AsyncMock(return_value=True),
        load_snapshot=AsyncMock(return_value=(bucket,)),
    )
    audit_service = SimpleNamespace(create_audit_log=AsyncMock())
    db = SimpleNamespace(commit=AsyncMock())
    service = NorthboundOperationsQueryService(repository=repository, audit_service=audit_service)

    snapshot = await service.get_snapshot(
        db,
        principal=NorthboundOperationalPrincipal(tenant_id=42, user_id=42),
        workline_id=7,
    )

    repository.workline_is_owned_by.assert_awaited_once_with(db, workline_id=7, tenant_id=42)
    repository.load_snapshot.assert_awaited_once_with(db, tenant_id=42, workline_id=7)
    assert snapshot.tenant_scope == "WORKLINE_OWNER"
    assert snapshot.workline_id == 7
    assert snapshot.operations[0].operation_identity == "wms.inventory.query_inventory@v1"
    assert snapshot.operations[0].mode == "QUERY"
    assert "readiness" not in snapshot.operations[0].model_dump()
    audit_args = audit_service.create_audit_log.await_args.kwargs["args"]
    assert audit_args["decision"] == "ALLOWED"
    assert audit_args["tenant_id"] == "42"
    assert not {"payload", "trace_id", "credential_reference", "headers"} & set(audit_args)


@pytest.mark.asyncio
async def test_cross_tenant_operational_read_is_denied_and_audited() -> None:
    from src.app.runtime.orchestration.operational_models import NorthboundOperationalPrincipal
    from src.app.runtime.orchestration.services.query.northbound_operations_query_service import (
        NorthboundOperationsQueryService,
    )

    repository = SimpleNamespace(
        workline_is_owned_by=AsyncMock(return_value=False),
        load_snapshot=AsyncMock(),
    )
    audit_service = SimpleNamespace(create_audit_log=AsyncMock())
    db = SimpleNamespace(commit=AsyncMock())
    service = NorthboundOperationsQueryService(repository=repository, audit_service=audit_service)

    with pytest.raises(PermissionException):
        await service.get_snapshot(
            db,
            principal=NorthboundOperationalPrincipal(tenant_id=42, user_id=42),
            workline_id=99,
        )

    repository.load_snapshot.assert_not_awaited()
    audit_args = audit_service.create_audit_log.await_args.kwargs
    assert audit_args["status"].value == "FAIL"
    assert audit_args["args"]["decision"] == "DENIED"
    assert audit_args["args"]["workline_id"] == "99"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_superuser_operational_read_uses_unscoped_repository_query() -> None:
    from src.app.runtime.orchestration.operational_models import NorthboundOperationalPrincipal
    from src.app.runtime.orchestration.services.query.northbound_operations_query_service import (
        NorthboundOperationsQueryService,
    )

    repository = SimpleNamespace(
        workline_is_owned_by=AsyncMock(),
        load_snapshot=AsyncMock(return_value=()),
    )
    audit_service = SimpleNamespace(create_audit_log=AsyncMock())
    db = SimpleNamespace(commit=AsyncMock())
    service = NorthboundOperationsQueryService(repository=repository, audit_service=audit_service)

    snapshot = await service.get_snapshot(
        db,
        principal=NorthboundOperationalPrincipal(tenant_id=42, user_id=42, is_superuser=True),
        workline_id=None,
    )

    repository.workline_is_owned_by.assert_not_awaited()
    repository.load_snapshot.assert_awaited_once_with(db, tenant_id=None, workline_id=None)
    assert snapshot.tenant_scope == "PLATFORM"
