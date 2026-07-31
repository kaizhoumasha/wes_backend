"""北向运维 Repository 的同步 QUERY operation 可见性合同。"""

from __future__ import annotations

from src.app.runtime.orchestration.repositories.northbound_operations_repository import (
    NorthboundOperationHealthRow,
    NorthboundOperationsRepository,
)
from tests.contracts.wms_integration.provider_profile_support import build_provider_catalog

_CATALOG = build_provider_catalog()
_PRODUCTION_PROFILE = _CATALOG.profile_identity
_QUERY_OPERATION = "wms.inventory.query_inventory@v1"


class _Rows:
    def __init__(self, rows: tuple[tuple[object, ...], ...]) -> None:
        self._rows = rows

    def all(self) -> tuple[tuple[object, ...], ...]:
        return self._rows


class _EvidenceOnlyDatabase:
    async def execute(self, statement) -> _Rows:
        sql = str(statement)
        if "wms_call_evidence" in sql:
            return _Rows(((_PRODUCTION_PROFILE, _QUERY_OPERATION),))
        return _Rows(())


async def test_platform_snapshot_includes_query_operation_with_evidence_and_no_outbox() -> None:
    rows = await NorthboundOperationsRepository(provider_catalog=_CATALOG).load_snapshot(
        _EvidenceOnlyDatabase(),
        tenant_id=None,
        workline_id=None,
    )

    assert len(rows) == 1
    assert rows[0].provider_profile_identity == _PRODUCTION_PROFILE
    assert rows[0].operation_identity == _QUERY_OPERATION
    assert rows[0].mode == "QUERY"
    assert not hasattr(rows[0], "readiness")


async def test_platform_snapshot_has_no_shadow_readiness_query() -> None:
    database = _EvidenceOnlyDatabase()

    await NorthboundOperationsRepository(provider_catalog=_CATALOG).load_snapshot(
        database,
        tenant_id=None,
        workline_id=None,
    )

    # 仓储层只读取基础 operation 观测，不再依赖 shadow readiness 平台。
    assert "readiness" not in NorthboundOperationHealthRow.__dataclass_fields__
