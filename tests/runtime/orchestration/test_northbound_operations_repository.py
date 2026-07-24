"""北向运维 Repository 的同步 QUERY operation 可见性合同。"""

from __future__ import annotations

from src.app.runtime.orchestration.repositories.northbound_operations_repository import (
    NorthboundOperationHealthRow,
    NorthboundOperationsRepository,
)

_PRODUCTION_PROFILE = "wms.2026-07-06.material-flow.production"
_QUERY_OPERATION = "wms.inventory.query_inventory@v1"


class _Rows:
    def __init__(self, rows: tuple[tuple[object, ...], ...]) -> None:
        self._rows = rows

    def all(self) -> tuple[tuple[object, ...], ...]:
        return self._rows


class _ReadinessOnlyDatabase:
    async def execute(self, statement) -> _Rows:
        sql = str(statement)
        if "query_shadow_readiness_reports" in sql:
            return _Rows(((_PRODUCTION_PROFILE, _QUERY_OPERATION, "READY", None, "report-1"),))
        return _Rows(())


class _EvidenceOnlyDatabase:
    async def execute(self, statement) -> _Rows:
        sql = str(statement)
        if "wms_call_evidence" in sql:
            return _Rows(((_PRODUCTION_PROFILE, _QUERY_OPERATION),))
        return _Rows(())


async def test_platform_snapshot_includes_query_operation_with_readiness_and_no_outbox() -> None:
    rows = await NorthboundOperationsRepository().load_snapshot(
        _ReadinessOnlyDatabase(),
        tenant_id=None,
        workline_id=None,
    )

    assert rows == (
        NorthboundOperationHealthRow(
            provider_profile_identity=_PRODUCTION_PROFILE,
            operation_identity=_QUERY_OPERATION,
            backlog_count=0,
            active_lease_count=0,
            unknown_count=0,
            oldest_queue_age_seconds=0,
            rate_limited_count=0,
            lease_loss_count=0,
            reconciliation_open_count=0,
            readiness="READY",
        ),
    )


async def test_platform_snapshot_includes_query_operation_with_evidence_and_no_outbox() -> None:
    rows = await NorthboundOperationsRepository().load_snapshot(
        _EvidenceOnlyDatabase(),
        tenant_id=None,
        workline_id=None,
    )

    assert len(rows) == 1
    assert rows[0].provider_profile_identity == _PRODUCTION_PROFILE
    assert rows[0].operation_identity == _QUERY_OPERATION
    assert rows[0].readiness == "UNKNOWN"
