"""E03/E07 持久链 request resolver。"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Protocol, cast

from src.app.execution.models.wms_confirmation import WmsConfirmationStatus
from src.app.execution.services.decision_applier import WmsConfirmationRequest
from src.app.execution.services.wms_confirmation_service import E03_CONFIRM_INBOUND, E07_NOTIFY_PKG_BINDING
from src.app.wms_integration.ports.fulfillment_operations import NotifyPkgBindingRequest
from src.app.wms_integration.ports.inventory_operations import ConfirmInboundRequest
from src.app.wms_integration.ports.operation_common import validate_json_payload

_OPERATIONS = (E03_CONFIRM_INBOUND, E07_NOTIFY_PKG_BINDING)


class _ExecutionRepository(Protocol):
    async def get_by_execution_code_for_update(self, db: object, execution_code: str) -> Any | None: ...


class _EvidenceRepository(Protocol):
    async def get_by_id_for_update(self, db: object, evidence_id: int) -> Any | None: ...


class _ConfirmationRepository(Protocol):
    async def list_for_execution_operations_for_update(
        self,
        db: object,
        *,
        material_execution_id: int,
        operations: tuple[str, ...],
    ) -> list[Any]: ...


class ExecutionConfirmationRequestResolver:
    """拒绝推测 wire；只消费当前 decision 明确引用的持久 evidence。"""

    def __init__(
        self,
        *,
        execution_repository: _ExecutionRepository,
        evidence_repository: _EvidenceRepository,
        confirmation_repository: _ConfirmationRepository,
    ) -> None:
        self._executions = execution_repository
        self._evidences = evidence_repository
        self._confirmations = confirmation_repository

    async def resolve(self, db: object, decision: Any) -> WmsConfirmationRequest:
        if decision.operation not in _OPERATIONS:
            raise ValueError("resolver 只拥有 E03/E07")
        execution = await self._executions.get_by_execution_code_for_update(db, decision.material_execution_id)
        if execution is None or execution.id is None:
            raise LookupError("MaterialExecution 不存在")
        evidence_id = _canonical_evidence_id(decision.evidence_refs)
        evidence = await self._evidences.get_by_id_for_update(db, evidence_id)
        if evidence is None or evidence.material_execution_id != execution.id:
            raise ValueError("WMS confirmation evidence/execution correlation 不匹配")
        data = evidence.normalized_payload.get("data")
        if not isinstance(data, dict):
            raise TypeError("WMS confirmation evidence.data 必须是对象")
        if decision.operation == E03_CONFIRM_INBOUND:
            payload = self._e03_payload(decision.operation_id, execution.execution_code, data)
        else:
            payload = await self._e07_payload(db, decision.operation_id, execution.id, data)
        return WmsConfirmationRequest(
            request_payload=payload,
            deadline_at=evidence.received_at + timedelta(seconds=30),
        )

    @staticmethod
    def _e03_payload(operation_id: str, execution_code: str, data: dict[str, Any]) -> dict[str, Any]:
        request = validate_json_payload(
            ConfirmInboundRequest,
            {
                "dispatch_key": operation_id,
                "inbound_key": execution_code,
                "material_code": data.get("material_code"),
                "quantity": data.get("quantity"),
                "pkg_id": data.get("pkg_id"),
                "location_code": data.get("location_code"),
            },
        )
        return cast("dict[str, Any]", request.model_dump(mode="json"))

    async def _e07_payload(
        self,
        db: object,
        operation_id: str,
        material_execution_id: int,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        confirmations = await self._confirmations.list_for_execution_operations_for_update(
            db,
            material_execution_id=material_execution_id,
            operations=_OPERATIONS,
        )
        e03 = next(
            (
                item
                for item in confirmations
                if item.operation == E03_CONFIRM_INBOUND and item.status == WmsConfirmationStatus.COMPLETED
            ),
            None,
        )
        if e03 is None:
            raise ValueError("E07 request 缺少已完成 E03")
        request = validate_json_payload(
            NotifyPkgBindingRequest,
            {
                "dispatch_key": operation_id,
                "pkg_id": e03.request_payload.get("pkg_id"),
                "bin_id": data.get("bin_id"),
                "slot_id": data.get("slot_id"),
                "rack_id": data.get("rack_id"),
                "station_code": data.get("station_code"),
            },
        )
        return cast("dict[str, Any]", request.model_dump(mode="json"))


class WmsConfirmationRequestTypedRouter:
    """明确把 E03/E07 交给 persisted-chain resolver，其余交给粗分 resolver。"""

    def __init__(self, *, execution_resolver: ExecutionConfirmationRequestResolver, rough_sorter_resolver: Any) -> None:
        self._execution_resolver = execution_resolver
        self._rough_sorter_resolver = rough_sorter_resolver

    async def resolve(self, db: object, decision: Any) -> WmsConfirmationRequest:
        if decision.operation in _OPERATIONS:
            return await self._execution_resolver.resolve(db, decision)
        return await self._rough_sorter_resolver.resolve(db, decision)


def _canonical_evidence_id(evidence_refs: object) -> int:
    if not isinstance(evidence_refs, tuple) or len(evidence_refs) != 1:
        raise ValueError("E03/E07 必须引用一个持久 evidence")
    value = evidence_refs[0]
    if not isinstance(value, str) or not value.isascii() or not value.isdigit() or value.startswith("0"):
        raise ValueError("E03/E07 evidence ref 不是 canonical id")
    return int(value)


__all__ = ["ExecutionConfirmationRequestResolver", "WmsConfirmationRequestTypedRouter"]
