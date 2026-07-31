"""EFFECT reconciliation 人工决议服务。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.repositories.northbound_operations_repository import (
    northbound_operations_repository,
)
from src.app.runtime.orchestration.runtime_intent_log import RuntimeIntentStatus
from src.app.runtime.orchestration.services.effect_reducer_service import EffectIntentNotFound
from src.app.runtime.orchestration.wms_sync_obligation import (
    WMS_SYNC_OBLIGATION_OPERATION_IDENTITIES,
    WmsSyncObligationResolution,
)
from src.app.sys.repositories import system_outbox_repository
from src.core.exceptions import PermissionException
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from src.app.runtime.orchestration.effect_bridges import EffectReconciliationBridge


class EffectReconciliationResolutionService:
    """以稳定请求身份原子提交 EFFECT 人工对账决议。"""

    def __init__(
        self,
        *,
        reconciliation_bridge: EffectReconciliationBridge | None = None,
        outbox_repository: Any = system_outbox_repository,
        owner_scope_repository: Any = northbound_operations_repository,
    ) -> None:
        self._reconciliation_bridge = reconciliation_bridge
        self._outbox_repository = outbox_repository
        self._owner_scope_repository = owner_scope_repository

    def _resolve_reconciliation_bridge(self) -> EffectReconciliationBridge:
        if self._reconciliation_bridge is None:
            from src.app.runtime.orchestration.effect_bridges import effect_reconciliation_bridge

            self._reconciliation_bridge = effect_reconciliation_bridge
        return self._reconciliation_bridge

    async def resolve(
        self,
        db: Any,
        *,
        dispatch_key: str,
        request_id: str | None,
        resolution: str | None,
        operator_note: str,
        operator_id: int,
        is_superuser: bool,
        obligation_resolution: WmsSyncObligationResolution | None = None,
    ) -> dict[str, Any]:
        outbox = await self._outbox_repository.get_by_dispatch_key(db, dispatch_key)
        if outbox is None:
            raise EffectIntentNotFound(f"dispatch_key={dispatch_key} 对应的 EFFECT outbox 不存在")
        workline_id = getattr(outbox, "workline_id", None)
        if not is_superuser and (
            workline_id is None
            or not await self._owner_scope_repository.workline_is_owned_by(
                db,
                workline_id=workline_id,
                tenant_id=operator_id,
            )
        ):
            raise PermissionException(
                "无权提交该 WorkLine 的 EFFECT reconciliation 决议",
                detail={
                    "scope": "WORKLINE_OWNER",
                    "dispatch_key": dispatch_key,
                },
            )
        operation_identity = str(getattr(outbox, "operation_identity", "") or "")
        if operation_identity in WMS_SYNC_OBLIGATION_OPERATION_IDENTITIES:
            if obligation_resolution is None or request_id is not None or resolution is not None:
                raise ValueError("E03/E07 reconciliation requires typed obligation resolution")
            if obligation_resolution.resolved_operation_identity != operation_identity:
                raise ValueError("typed obligation resolution operation identity differs from outbox")
            normalized_request_id = obligation_resolution.source_event_id
            target = None
            response_resolution = obligation_resolution.resolution
        else:
            if obligation_resolution is not None:
                raise ValueError("typed obligation resolution is restricted to E03/E07")
            normalized_request_id = request_id.strip() if isinstance(request_id, str) else ""
            if not normalized_request_id:
                raise ValueError("request_id 不能为空")
            target = RuntimeIntentStatus(resolution)
            if target not in {RuntimeIntentStatus.COMPLETED, RuntimeIntentStatus.REJECTED}:
                raise ValueError("generic EFFECT resolution requires COMPLETED or REJECTED")
            response_resolution = target.value
        result = await self._resolve_reconciliation_bridge().resolve(
            db,
            dispatch_key=dispatch_key,
            occurred_at_ms=int(timezone.now_utc().timestamp() * 1000),
            resolution=target,
            obligation_resolution=obligation_resolution,
            reason_code="MANUAL_EFFECT_RECONCILIATION_RESOLUTION",
            source_event_id=normalized_request_id,
            evidence_json={
                "operator_id": operator_id,
                "operator_note": operator_note,
                "request_id": normalized_request_id,
            },
        )
        await db.commit()
        return {
            "dispatch_key": dispatch_key,
            "resolution": response_resolution,
            "request_id": normalized_request_id,
            "intent_status": result.intent_status.value,
            "case_status": result.case_status.value if result.case_status is not None else None,
            "state_changed": result.state_changed,
        }


effect_reconciliation_resolution_service = EffectReconciliationResolutionService()

__all__ = ["EffectReconciliationResolutionService", "effect_reconciliation_resolution_service"]
