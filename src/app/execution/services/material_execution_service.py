"""MaterialExecution 应用服务。"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Protocol

from src.app.execution.models.material_execution import MaterialExecution, MaterialExecutionStatus
from src.app.execution.repositories.material_execution_repository import material_execution_repository


class ActiveMaterialExecutionExistsError(ValueError):
    """同一 trace 已有活动执行。"""


class MaterialExecutionRepositoryPort(Protocol):
    async def lock_material_trace(self, db: object, material_trace_id: str) -> None: ...

    async def get_active_by_trace_for_update(
        self,
        db: object,
        material_trace_id: str,
    ) -> MaterialExecution | None: ...

    async def add(self, db: object, execution: MaterialExecution) -> MaterialExecution: ...

    async def flush(self, db: object) -> None: ...


def _transition_evidence(reason_code: str, evidence_id: int) -> tuple[str, int]:
    reason = reason_code.strip()
    if not reason:
        raise ValueError("reason_code 必须是非空字符串")
    if evidence_id <= 0:
        raise ValueError("evidence_id 必须引用已持久化 evidence")
    return reason, evidence_id


class MaterialExecutionService:
    def __init__(self, repository: MaterialExecutionRepositoryPort | None = None) -> None:
        self._repository = repository or material_execution_repository

    async def create(
        self,
        db: object,
        *,
        execution_code: str,
        material_trace_id: str,
        workline_id: int,
        line_run_epoch_id: int,
        changed_at: datetime,
        reason_code: str,
        evidence_id: int,
    ) -> MaterialExecution:
        reason, evidence_id = _transition_evidence(reason_code, evidence_id)
        await self._repository.lock_material_trace(db, material_trace_id)
        active = await self._repository.get_active_by_trace_for_update(db, material_trace_id)
        if active is not None:
            raise ActiveMaterialExecutionExistsError(
                f"material trace {material_trace_id} 已由活动执行 {active.execution_code} 拥有"
            )
        return await self._repository.add(
            db,
            MaterialExecution(
                execution_code=execution_code,
                material_trace_id=material_trace_id,
                workline_id=workline_id,
                line_run_epoch_id=line_run_epoch_id,
                last_transition_reason=reason,
                last_transition_evidence_id=evidence_id,
                status_changed_at=changed_at,
            ),
        )

    async def transition(
        self,
        db: object,
        execution: MaterialExecution,
        *,
        target: MaterialExecutionStatus,
        changed_at: datetime,
        reason_code: str,
        evidence_id: int,
    ) -> MaterialExecution:
        reason, evidence_id = _transition_evidence(reason_code, evidence_id)
        execution.transition_to(
            target,
            changed_at=changed_at,
            reason_code=reason,
            evidence_id=evidence_id,
        )
        await self._repository.flush(db)
        return execution


material_execution_service = MaterialExecutionService()

__all__ = [
    "ActiveMaterialExecutionExistsError",
    "MaterialExecutionService",
    "material_execution_service",
]
