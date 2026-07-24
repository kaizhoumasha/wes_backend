"""租户作用域的北向只读运维查询 Service。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.app.runtime.orchestration.operational_models import (
    NorthboundOperationalPrincipal,
    NorthboundOperationalSnapshot,
    NorthboundOperationHealth,
)
from src.app.runtime.orchestration.repositories.northbound_operations_repository import (
    northbound_operations_repository,
)
from src.app.sys.models.audit_log import OperaStatus
from src.app.sys.services.audit_service import audit_log_service
from src.core.exceptions import PermissionException
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class NorthboundOperationsQueryService:
    """执行 RBAC 之后的 owner scope 校验、聚合查询与读取审计。"""

    def __init__(
        self,
        *,
        repository: Any = northbound_operations_repository,
        audit_service: Any = audit_log_service,
    ) -> None:
        self._repository = repository
        self._audit_service = audit_service

    async def get_snapshot(
        self,
        db: AsyncSession,
        *,
        principal: NorthboundOperationalPrincipal,
        workline_id: int | None,
    ) -> NorthboundOperationalSnapshot:
        tenant_id = None if principal.is_superuser else principal.tenant_id
        if workline_id is not None and tenant_id is not None:
            owned = await self._repository.workline_is_owned_by(
                db,
                workline_id=workline_id,
                tenant_id=tenant_id,
            )
            if not owned:
                await self._record_audit(
                    db,
                    principal=principal,
                    workline_id=workline_id,
                    decision="DENIED",
                )
                raise PermissionException(
                    "无权读取该 WorkLine 的北向运维快照",
                    detail={
                        "scope": "WORKLINE_OWNER",
                        "workline_id": str(workline_id),
                    },
                )

        rows = await self._repository.load_snapshot(
            db,
            tenant_id=tenant_id,
            workline_id=workline_id,
        )
        snapshot = NorthboundOperationalSnapshot(
            generated_at=timezone.now_utc(),
            tenant_scope="PLATFORM" if principal.is_superuser else "WORKLINE_OWNER",
            tenant_id=tenant_id,
            workline_id=workline_id,
            operations=tuple(NorthboundOperationHealth.model_validate(row) for row in rows),
        )
        await self._record_audit(
            db,
            principal=principal,
            workline_id=workline_id,
            decision="ALLOWED",
        )
        return snapshot

    async def _record_audit(
        self,
        db: AsyncSession,
        *,
        principal: NorthboundOperationalPrincipal,
        workline_id: int | None,
        decision: str,
    ) -> None:
        status = OperaStatus.SUCCESS if decision == "ALLOWED" else OperaStatus.FAIL
        args = {
            "model": "NorthboundOperationalSnapshot",
            "operation": "read",
            "record_id": str(workline_id) if workline_id is not None else "all",
            "decision": decision,
            "scope": "PLATFORM" if principal.is_superuser else "WORKLINE_OWNER",
            "tenant_id": str(principal.tenant_id),
            "viewer_user_id": str(principal.user_id),
        }
        if workline_id is not None:
            args["workline_id"] = str(workline_id)
        await self._audit_service.create_audit_log(
            db,
            method="GET",
            title="Northbound Runtime Operations Read",
            path="/v1/workline/runtime-operations/northbound",
            args=args,
            status=status,
            code="200" if decision == "ALLOWED" else "403",
            msg="OK" if decision == "ALLOWED" else "FORBIDDEN",
        )
        await db.commit()


northbound_operations_query_service = NorthboundOperationsQueryService()


__all__ = [
    "NorthboundOperationsQueryService",
    "northbound_operations_query_service",
]
