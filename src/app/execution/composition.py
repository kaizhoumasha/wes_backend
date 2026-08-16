"""Execution 核心对象的显式组合根。"""

from __future__ import annotations

from dataclasses import dataclass

from src.app.execution.repositories import (
    InboundEvidenceRepository,
    MaterialExecutionRepository,
    WmsConfirmationRepository,
)
from src.app.execution.services import (
    InboundEvidenceService,
    MaterialExecutionService,
    WmsConfirmationService,
)


@dataclass(frozen=True, slots=True)
class ExecutionRuntime:
    material_execution_service: MaterialExecutionService
    inbound_evidence_service: InboundEvidenceService
    wms_confirmation_service: WmsConfirmationService


def build_execution_runtime() -> ExecutionRuntime:
    material_repository = MaterialExecutionRepository()
    evidence_repository = InboundEvidenceRepository()
    confirmation_repository = WmsConfirmationRepository()
    return ExecutionRuntime(
        material_execution_service=MaterialExecutionService(repository=material_repository),
        inbound_evidence_service=InboundEvidenceService(repository=evidence_repository),
        wms_confirmation_service=WmsConfirmationService(repository=confirmation_repository),
    )


__all__ = ["ExecutionRuntime", "build_execution_runtime"]
