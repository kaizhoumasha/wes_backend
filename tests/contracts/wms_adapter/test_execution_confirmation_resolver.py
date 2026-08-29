"""E03/E07 request 只从持久化 execution、evidence 与前序 confirmation 重建。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from src.app.execution.models.wms_confirmation import WmsConfirmationStatus
from src.app.wms_adapter.execution_confirmation_resolver import ExecutionConfirmationRequestResolver


class _Executions:
    async def get_by_execution_code_for_update(self, _db: object, execution_code: str):
        assert execution_code == "EXEC-001"
        return SimpleNamespace(id=21, execution_code=execution_code, admission_evidence_id=31)


class _Evidences:
    def __init__(self, rows: dict[int, object]) -> None:
        self.rows = rows

    async def get_by_id_for_update(self, _db: object, evidence_id: int):
        return self.rows.get(evidence_id)


class _Confirmations:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    async def list_for_execution_operations_for_update(self, *_args: object, **_kwargs: object):
        return self.rows


def _decision(operation: str, evidence_id: int, operation_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        operation=operation,
        operation_id=operation_id,
        material_execution_id="EXEC-001",
        evidence_refs=(str(evidence_id),),
    )


@pytest.mark.asyncio
async def test_resolver_rebuilds_e03_then_e07_from_persisted_chain() -> None:
    admitted = SimpleNamespace(
        id=31,
        material_execution_id=21,
        received_at=datetime(2026, 8, 16),
        normalized_payload={
            "data": {
                "material_code": "MAT-001",
                "quantity": "1",
                "pkg_id": "PKG-001",
                "location_code": "CELL-001",
            }
        },
    )
    placed = SimpleNamespace(
        id=32,
        material_execution_id=21,
        received_at=datetime(2026, 8, 16, 0, 1),
        normalized_payload={
            "data": {
                "bin_id": "BIN-001",
                "slot_id": "SLOT-001",
                "rack_id": "RACK-001",
                "station_code": "STATION-001",
            }
        },
    )
    e03 = SimpleNamespace(
        operation="wms.inventory.confirm_inbound@v1",
        status=WmsConfirmationStatus.COMPLETED,
        request_payload={"pkg_id": "PKG-001"},
    )
    resolver = ExecutionConfirmationRequestResolver(
        execution_repository=_Executions(),
        evidence_repository=_Evidences({31: admitted, 32: placed}),
        confirmation_repository=_Confirmations([e03]),
    )

    e03_request = await resolver.resolve(
        object(),
        _decision("wms.inventory.confirm_inbound@v1", 31, "E03-001"),
    )
    e07_request = await resolver.resolve(
        object(),
        _decision("wms.fulfillment.notify_pkg_binding@v1", 32, "E07-001"),
    )

    assert e03_request.request_payload == {
        "dispatch_key": "E03-001",
        "inbound_key": "EXEC-001",
        "material_code": "MAT-001",
        "quantity": "1",
        "pkg_id": "PKG-001",
        "location_code": "CELL-001",
    }
    assert e07_request.request_payload == {
        "dispatch_key": "E07-001",
        "pkg_id": "PKG-001",
        "bin_id": "BIN-001",
        "slot_id": "SLOT-001",
        "rack_id": "RACK-001",
        "station_code": "STATION-001",
    }
