"""粗分机 deployment 所需的窄持久读取端口与只读 reader。"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any, Protocol

from src.app.device.contracts import EcsDeviceMode, EcsDeviceState
from src.app.device.repositories.status_observation_repository import device_status_observation_repository
from src.app.execution.models import InboundEvidenceApplyStatus, InboundEvidenceKind
from src.app.execution.plugin_binding import InitialExecutionDescriptor
from src.app.execution.repositories import inbound_evidence_repository
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from src.app.device.models.command import DeviceCommand
    from src.app.device.models.evidence import DeviceStatusObservation
    from src.app.execution.models import (
        InboundEvidence,
        MaterialExecution,
        RackReplacementTransportBinding,
        WmsConfirmation,
    )
    from src.app.resource.models import RackPlacement
    from src.app.runtime.orchestration.models.rack_position import WorklineRackPosition
    from src.app.workline.models import LineRunEpoch, LineRunEpochDeviceBinding, LineRunEpochPositionBinding, WorkLine


class EvidenceRepositoryPort(Protocol):
    async def get_by_id_for_update(self, db: Any, evidence_id: int) -> InboundEvidence | None: ...


class ExecutionRepositoryPort(Protocol):
    async def get_by_execution_code_for_update(self, db: Any, execution_code: str) -> MaterialExecution | None: ...


class EpochRepositoryPort(Protocol):
    async def get_by_id_for_update(self, db: Any, line_run_epoch_id: int) -> LineRunEpoch | None: ...

    async def list_bindings(self, db: Any, line_run_epoch_id: int) -> list[LineRunEpochDeviceBinding]: ...

    async def list_position_bindings(self, db: Any, line_run_epoch_id: int) -> list[LineRunEpochPositionBinding]: ...

    async def get_binding_by_role_for_update(
        self, db: Any, *, line_run_epoch_id: int, device_role: str
    ) -> LineRunEpochDeviceBinding | None: ...


class WorkLineRepositoryPort(Protocol):
    async def get_by_id(self, db: Any, id: int) -> WorkLine | None: ...


class WmsConfirmationRepositoryPort(Protocol):
    async def get_by_identity_for_update(
        self, db: Any, operation: str, operation_id: str
    ) -> WmsConfirmation | None: ...

    async def list_for_execution(self, db: Any, material_execution_id: int) -> list[WmsConfirmation]: ...

    async def list_for_executions_for_update(
        self, db: Any, *, material_execution_ids: tuple[int, ...], operation: str
    ) -> list[WmsConfirmation]: ...


class RackPositionRepositoryPort(Protocol):
    async def get_by_workline_logic_location(
        self, db: Any, *, workline_code: str, logic_location_code: str
    ) -> WorklineRackPosition | None: ...


class RackPlacementRepositoryPort(Protocol):
    async def list_active_by_workline_position(
        self, db: Any, *, workline_code: str, position_code: str
    ) -> list[RackPlacement]: ...


class RackReplacementBindingRepositoryPort(Protocol):
    async def get_by_client_request_id_for_update(
        self, db: Any, client_request_id: str
    ) -> RackReplacementTransportBinding | None: ...


class DeviceStatusRepositoryPort(Protocol):
    async def get_latest_for_device(self, db: Any, device_code: str) -> DeviceStatusObservation | None: ...


class DeviceCommandRepositoryPort(Protocol):
    async def get_by_command_code(
        self, db: Any, command_code: str, *, for_update: bool = False
    ) -> DeviceCommand | None: ...

    async def list_for_material_execution(
        self, db: Any, *, line_run_epoch_id: int, material_execution_id: int
    ) -> list[DeviceCommand]: ...

    async def list_for_epoch_for_update(self, db: Any, *, line_run_epoch_id: int) -> list[DeviceCommand]: ...


class DeviceReadinessReader(Protocol):
    async def is_ready(
        self,
        db: Any,
        binding: LineRunEpochDeviceBinding,
        *,
        observed_at: datetime,
    ) -> bool: ...


class PersistedDeviceReadinessReader:
    """只用冻结合同与已持久 ECS status observation 判断设备准入。"""

    def __init__(self, repository: DeviceStatusRepositoryPort = device_status_observation_repository) -> None:
        self._repository = repository

    async def is_ready(
        self,
        db: object,
        binding: LineRunEpochDeviceBinding,
        *,
        observed_at: datetime,
    ) -> bool:
        status = await self._repository.get_latest_for_device(db, binding.device_code)
        if status is None:
            return False
        observed_at_ms = int(timezone.to_utc(observed_at).timestamp() * 1000)
        return (
            status.contract_key == binding.contract_key
            and status.contract_version == binding.contract_version
            and status.mode == EcsDeviceMode.AUTO
            and status.status == EcsDeviceState.IDLE
            and status.current_command_code is None
            and 0 <= observed_at_ms - status.device_timestamp <= binding.status_max_age_ms
        )


class RoughSorterInitialExecutionCorrelator:
    """只从已提交的 SCAN evidence 建立稳定初始 execution identity。"""

    def __init__(self, *, evidence_repository: EvidenceRepositoryPort = inbound_evidence_repository) -> None:
        self._evidences = evidence_repository

    async def correlate(self, db: object, evidence_id: str) -> InitialExecutionDescriptor | None:
        if not evidence_id.isascii() or not evidence_id.isdigit() or evidence_id.startswith("0"):
            raise ValueError("initial evidence_id 必须是 canonical positive integer string")
        evidence = await self._evidences.get_by_id_for_update(db, int(evidence_id))
        if evidence is None:
            return None
        if (
            evidence.kind != InboundEvidenceKind.DEVICE_EVENT
            or evidence.apply_status != InboundEvidenceApplyStatus.APPLIED
            or evidence.line_run_epoch_id is None
            or evidence.normalized_payload.get("event_type") != "SCAN_COMPLETED"
        ):
            raise ValueError("initial execution 只能关联已应用的 SCAN_COMPLETED")
        data = evidence.normalized_payload.get("data")
        if not isinstance(data, dict):
            raise TypeError("SCAN_COMPLETED.data 缺失")
        material_trace_id = _required_string(data.get("material_trace_id"), "material_trace_id")
        digest = hashlib.sha256(f"{evidence.line_run_epoch_id}:{material_trace_id}".encode()).hexdigest()
        return InitialExecutionDescriptor(
            material_trace_id=material_trace_id,
            execution_code=f"rough-sorter-{digest}",
        )


def _required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 必须是非空字符串")
    return value


__all__ = [
    "DeviceCommandRepositoryPort",
    "DeviceReadinessReader",
    "EpochRepositoryPort",
    "EvidenceRepositoryPort",
    "ExecutionRepositoryPort",
    "PersistedDeviceReadinessReader",
    "RackPlacementRepositoryPort",
    "RackPositionRepositoryPort",
    "RackReplacementBindingRepositoryPort",
    "RoughSorterInitialExecutionCorrelator",
    "WmsConfirmationRepositoryPort",
    "WorkLineRepositoryPort",
]
