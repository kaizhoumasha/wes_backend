"""WMS inventory QUERY 在 runtime composition root 使用的 Port factory。"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from src.app.runtime.system_capabilities.wms.inventory.query_inventory.contract import CONTRACT
from src.app.wms_integration.adapters import InventoryQueryOperationAdapter
from src.app.wms_integration.ports.query_inventory_operation import (
    OPERATION_IDENTITY,
    InventoryAuthorityItem,
    InventoryQueryOperationPort,
    InventoryQueryOperationResult,
)
from src.app.wms_integration.ports.query_outcome import QuerySuccess, QueryTechnicalFailure
from src.app.wms_integration.services.circuit_breaker_service import wms_circuit_breaker_service
from src.app.wms_integration.services.endpoint_config import wms_endpoint_config
from src.app.wms_integration.services.evidence_service import wms_call_evidence_service
from src.app.wms_integration.services.http_client import wms_http_client
from src.app.wms_integration.services.query_transport import (
    WmsCallEvidenceQueryWriter,
    WmsQueryEvidenceWriter,
    WmsQueryTransportExecutor,
)
from src.database.db import get_db_context

if TYPE_CHECKING:
    from collections.abc import Callable

    SandboxInventoryRowsProvider = Callable[..., list[dict[str, Any]]]


class SandboxInventoryQueryOperationPort:
    """SIMULATION 运行模式下的确定性 typed inventory QUERY Port。"""

    def __init__(
        self,
        *,
        evidence_writer: WmsQueryEvidenceWriter,
        rows_provider: SandboxInventoryRowsProvider,
    ) -> None:
        self._evidence_writer = evidence_writer
        self._rows_provider = rows_provider

    async def execute(self, request):
        target_code = CONTRACT.target_code
        permit = await self._evidence_writer.before_call(
            operation_identity=OPERATION_IDENTITY,
            target_code=target_code,
        )
        if not permit.allowed:
            outcome = QueryTechnicalFailure(
                reason_code="WMS_CIRCUIT_OPEN",
                message="WMS QUERY circuit breaker rejected the call",
                retryable=True,
                retry_after_seconds=permit.retry_after_seconds,
            )
        else:
            rows = self._rows_provider(
                sku=request.material_code,
                lot_no=request.lot_no,
                warehouse_code=request.warehouse_code,
                owner_code=request.owner_code,
            )
            outcome = QuerySuccess(
                InventoryQueryOperationResult(
                    items=tuple(
                        InventoryAuthorityItem(
                            material_code=row["sku"],
                            available_quantity=row["available_qty"],
                            warehouse_code=row.get("warehouse_code"),
                            owner_code=row.get("owner_code"),
                            lot_no=row.get("lot_no"),
                            total_quantity=row.get("total_qty"),
                            reserved_quantity=row.get("reserved_qty"),
                        )
                        for row in rows
                    ),
                    source_version="SANDBOX_WMS_INVENTORY_V1",
                )
            )
        evidence_key = await self._evidence_writer.record(
            operation_identity=OPERATION_IDENTITY,
            target_code=target_code,
            request_snapshot=request.model_dump(mode="json", exclude_none=True),
            outcome=outcome,
            permit=permit,
        )
        return replace(outcome, evidence_key=evidence_key)


def build_inventory_query_port_factory(
    *,
    simulation: bool,
    sandbox_rows_provider: SandboxInventoryRowsProvider,
) -> Callable[[], InventoryQueryOperationPort]:
    """构建每次 capability attempt 都返回新 Port 实例的 factory。"""

    evidence_writer = WmsCallEvidenceQueryWriter(
        session_factory=get_db_context,
        evidence_service=wms_call_evidence_service,
        breaker_service=wms_circuit_breaker_service,
    )
    if simulation:

        def sandbox_factory() -> InventoryQueryOperationPort:
            return SandboxInventoryQueryOperationPort(
                evidence_writer=evidence_writer,
                rows_provider=sandbox_rows_provider,
            )

        return sandbox_factory

    def production_factory() -> InventoryQueryOperationPort:
        return InventoryQueryOperationAdapter(
            executor=WmsQueryTransportExecutor(
                base_url=wms_endpoint_config.base_url,
                transport=wms_http_client.transport,
                evidence_writer=evidence_writer,
            ),
            contract=CONTRACT,
        )

    return production_factory


__all__ = ["SandboxInventoryQueryOperationPort", "build_inventory_query_port_factory"]
