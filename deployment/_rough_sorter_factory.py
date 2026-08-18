"""粗分机 FactFactory facade：持久 evidence/runtime snapshot 与 typed dispatch。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from wes_plugin_sdk import (
    DeviceBindingSnapshot,
    DeviceResultReadyFact,
    EpochConfigurationSnapshot,
    EvidenceReadyFact,
    ExecutionLifecycle,
    ExecutionSnapshot,
    FactReference,
    PositionBindingSnapshot,
    TransportResultReadyFact,
    WmsResultReadyFact,
)
from wes_plugin_sdk import (
    RecoveryDecidedFact as BaseRecoveryDecidedFact,
)

from deployment._rough_sorter_device_facts import build_device_fact
from deployment._rough_sorter_persistence import (
    DeviceCommandRepositoryPort,
    DeviceReadinessReader,
    EpochRepositoryPort,
    EvidenceRepositoryPort,
    ExecutionRepositoryPort,
    PersistedDeviceReadinessReader,
    RackPlacementRepositoryPort,
    RackPositionRepositoryPort,
    RackReplacementBindingRepositoryPort,
    WmsConfirmationRepositoryPort,
    WorkLineRepositoryPort,
)
from deployment._rough_sorter_transport_recovery_facts import (
    build_recovery_fact,
    build_transport_fact,
    current_rack_id,
)
from deployment._rough_sorter_values import (
    device_position,
    position_binding,
    required_string,
    stable_operation_id,
    strict_object,
)
from deployment._rough_sorter_wms_facts import build_wms_fact
from src.app.device.repositories.command_repository import device_command_repository
from src.app.execution.repositories import inbound_evidence_repository, material_execution_repository
from src.app.execution.repositories.rack_replacement_transport_binding_repository import (
    rack_replacement_transport_binding_repository,
)
from src.app.execution.repositories.wms_confirmation_repository import wms_confirmation_repository
from src.app.resource.repositories import rack_placement_repository
from src.app.runtime.orchestration.repositories.rack_position_repository import workline_rack_position_repository
from src.app.runtime.orchestration.repository_wiring import workline_repository
from src.app.transport.repository import TransportRepository
from src.app.workline.epoch_digest import configuration_digest, topology_digest
from src.app.workline.models.line_run_epoch import LineRunEpochStatus
from src.app.workline.repositories.line_run_epoch_repository import line_run_epoch_repository

if TYPE_CHECKING:
    from deployment._rough_sorter_types import RoughSorterTypes
    from src.app.execution.models import InboundEvidence, MaterialExecution
    from src.app.workline.models import LineRunEpochDeviceBinding, LineRunEpochPositionBinding


_DEVICE_CONTRACTS = {
    "MEASUREMENT_DEVICE": "rough_sorter.measurement_device",
    "TRANSFER_DEVICE": "rough_sorter.transfer_device",
    "PLACEMENT_DEVICE": "rough_sorter.placement_device",
}
_POSITION_ROLES = frozenset({"MEASUREMENT_POSITION", "PIPELINE_INLET", "PIPELINE_OUTLET", "NG_POSITION"})


class RoughSorterPluginFactFactory:
    """在 FactProcessor 当前事务中重建并验证插件 typed Fact。"""

    def __init__(
        self,
        *,
        types: RoughSorterTypes,
        evidence_repository: EvidenceRepositoryPort = inbound_evidence_repository,
        execution_repository: ExecutionRepositoryPort = material_execution_repository,
        epoch_repository: EpochRepositoryPort = line_run_epoch_repository,
        workline_repository: WorkLineRepositoryPort = workline_repository,
        wms_confirmation_repository: WmsConfirmationRepositoryPort = wms_confirmation_repository,
        device_readiness_reader: DeviceReadinessReader | None = None,
        device_command_repository: DeviceCommandRepositoryPort = device_command_repository,
        rack_position_repository: RackPositionRepositoryPort = workline_rack_position_repository,
        rack_placement_repository: RackPlacementRepositoryPort = rack_placement_repository,
        rack_replacement_binding_repository: RackReplacementBindingRepositoryPort = (
            rack_replacement_transport_binding_repository
        ),
        transport_repository: TransportRepository | None = None,
    ) -> None:
        self.types = types
        self._evidences = evidence_repository
        self._executions = execution_repository
        self._epochs = epoch_repository
        self._worklines = workline_repository
        self._wms_confirmations = wms_confirmation_repository
        self._device_readiness = device_readiness_reader or PersistedDeviceReadinessReader()
        self._commands = device_command_repository
        self._rack_positions = rack_position_repository
        self._rack_placements = rack_placement_repository
        self._rack_replacement_bindings = rack_replacement_binding_repository
        self._transport_tasks = transport_repository or TransportRepository()

    async def build(self, db: object, fact: FactReference) -> FactReference:
        evidence = await self._load_evidence(db, fact.evidence_id)
        execution = await self._executions.get_by_execution_code_for_update(db, fact.material_execution_id)
        if execution is None or execution.id is None:
            raise LookupError("MaterialExecution 不存在或未持久化")
        if evidence.material_execution_id != execution.id or evidence.line_run_epoch_id != execution.line_run_epoch_id:
            raise ValueError("Fact evidence 与 execution correlation 不匹配")
        runtime = await self._runtime_snapshot(db, execution)
        if type(fact) is EvidenceReadyFact:
            return self._build_scan_fact(fact, evidence, execution, runtime)
        if type(fact) is WmsResultReadyFact:
            return await build_wms_fact(
                db=db,
                fact=fact,
                evidence=evidence,
                execution=execution,
                runtime=runtime,
                types=self.types,
                evidences=self._evidences,
                epochs=self._epochs,
                confirmations=self._wms_confirmations,
                commands=self._commands,
                readiness=self._device_readiness,
                rack_bindings=self._rack_replacement_bindings,
                current_rack_id=self._current_rack_id,
            )
        if type(fact) is DeviceResultReadyFact:
            return await build_device_fact(
                db=db,
                fact=fact,
                evidence=evidence,
                execution=execution,
                runtime=runtime,
                types=self.types,
                evidences=self._evidences,
                epochs=self._epochs,
                confirmations=self._wms_confirmations,
                commands=self._commands,
                readiness=self._device_readiness,
                current_rack_id=self._current_rack_id,
            )
        if type(fact) is TransportResultReadyFact:
            return await build_transport_fact(
                db=db,
                fact=fact,
                evidence=evidence,
                execution=execution,
                runtime=runtime,
                types=self.types,
                evidences=self._evidences,
                confirmations=self._wms_confirmations,
                bindings=self._rack_replacement_bindings,
                transport_tasks=self._transport_tasks,
            )
        if type(fact) is BaseRecoveryDecidedFact:
            return await build_recovery_fact(
                db=db,
                fact=fact,
                evidence=evidence,
                execution=execution,
                runtime=runtime,
                types=self.types,
                evidences=self._evidences,
                epochs=self._epochs,
                commands=self._commands,
                readiness=self._device_readiness,
            )
        raise TypeError(f"rough sorter 不支持基础 Fact: {type(fact).__name__}")

    async def _load_evidence(self, db: object, evidence_id: str) -> InboundEvidence:
        if not evidence_id.isascii() or not evidence_id.isdigit() or evidence_id.startswith("0"):
            raise ValueError("Fact evidence_id 必须是 canonical positive integer string")
        evidence = await self._evidences.get_by_id_for_update(db, int(evidence_id))
        if evidence is None or evidence.id is None:
            raise LookupError("InboundEvidence 不存在或未持久化")
        return evidence

    async def _runtime_snapshot(self, db: object, execution: MaterialExecution) -> Any:
        epoch = await self._epochs.get_by_id_for_update(db, execution.line_run_epoch_id)
        if epoch is None or epoch.id is None or epoch.status != LineRunEpochStatus.ACTIVE:
            raise ValueError("execution 未关联活动 Epoch")
        if epoch.plugin_key != self.types.plugin_key or epoch.plugin_version != self.types.plugin_version:
            raise ValueError("Epoch plugin identity 与 rough sorter deployment 不匹配")
        devices = tuple(await self._epochs.list_bindings(db, epoch.id))
        positions = tuple(await self._epochs.list_position_bindings(db, epoch.id))
        self._validate_bindings(devices, positions)
        if epoch.configuration_digest != configuration_digest(epoch.plugin_key, epoch.plugin_version, epoch.flow_mode):
            raise ValueError("Epoch configuration digest drift")
        if epoch.topology_digest != topology_digest(devices, positions):
            raise ValueError("Epoch topology digest drift")
        workline = await self._worklines.get_by_id(db, epoch.workline_id)
        if workline is None or getattr(workline, "id", None) != execution.workline_id:
            raise ValueError("Epoch/execution WorkLine identity 不匹配")
        return self.types.RoughSorterRuntimeSnapshot(
            execution=ExecutionSnapshot(
                material_execution_id=execution.execution_code,
                material_trace_id=execution.material_trace_id,
                line_run_epoch_id=str(epoch.id),
                lifecycle=ExecutionLifecycle(execution.status),
                version=execution.version,
            ),
            epoch=EpochConfigurationSnapshot(
                line_run_epoch_id=str(epoch.id),
                workline_code=required_string(getattr(workline, "line_code", None), "workline.line_code"),
                plugin_key=epoch.plugin_key,
                plugin_version=epoch.plugin_version,
                config_digest=epoch.configuration_digest,
                topology_digest=epoch.topology_digest,
                device_bindings=tuple(
                    DeviceBindingSnapshot(
                        device_role=item.device_role,
                        device_code=item.device_code,
                        contract_key=item.contract_key,
                        contract_version=item.contract_version,
                    )
                    for item in sorted(devices, key=lambda item: item.device_role)
                ),
                position_bindings=tuple(
                    PositionBindingSnapshot(
                        position_role=item.position_role,
                        location_id=item.location_id,
                        location_type=item.location_type,
                    )
                    for item in sorted(positions, key=lambda item: item.position_role)
                ),
            ),
        )

    @staticmethod
    def _validate_bindings(
        devices: tuple[LineRunEpochDeviceBinding, ...], positions: tuple[LineRunEpochPositionBinding, ...]
    ) -> None:
        if {item.device_role: item.contract_key for item in devices} != _DEVICE_CONTRACTS:
            raise ValueError("rough sorter Epoch 必须精确绑定三个设备角色与合同")
        if any(item.contract_version != "1.0" for item in devices):
            raise ValueError("rough sorter device contract_version 必须固定为 1.0")
        if {item.position_role for item in positions} != set(_POSITION_ROLES) or len(positions) != len(_POSITION_ROLES):
            raise ValueError("rough sorter Epoch 必须精确绑定四个位置角色")
        if any(item.location_type != item.position_role for item in positions):
            raise ValueError("rough sorter position binding type 与 role 不匹配")

    def _build_scan_fact(
        self, fact: EvidenceReadyFact, evidence: InboundEvidence, execution: MaterialExecution, runtime: Any
    ) -> Any:
        if evidence.normalized_payload.get("event_type") != "SCAN_COMPLETED":
            raise ValueError("rough sorter initial evidence 必须是 SCAN_COMPLETED")
        data = strict_object(
            evidence.normalized_payload.get("data"),
            {
                "material_trace_id",
                "LotCode",
                "DateCode",
                "Qty",
                "ProductNo",
                "MfrPN",
                "PONumber",
                "diameter_mm",
                "thickness_mm",
                "shape_result",
                "position",
            },
            "SCAN_COMPLETED.data",
        )
        position = device_position(data["position"], execution.material_trace_id)
        expected = position_binding(runtime, "MEASUREMENT_POSITION")
        if position.location_id != expected.location_id or position.location_type != expected.location_type:
            raise ValueError("SCAN position 与 Epoch measurement binding 不匹配")
        return self.types.MaterialEvidenceReadyFact(
            fact_id=fact.fact_id,
            evidence_id=fact.evidence_id,
            fact_version=fact.fact_version,
            material_execution_id=fact.material_execution_id,
            runtime_snapshot=runtime,
            material_trace_id=required_string(data["material_trace_id"], "material_trace_id"),
            line_run_epoch_id=runtime.epoch.line_run_epoch_id,
            workline_code=runtime.epoch.workline_code,
            lot_code=required_string(data["LotCode"], "LotCode"),
            date_code=required_string(data["DateCode"], "DateCode"),
            qty=required_string(data["Qty"], "Qty"),
            product_no=required_string(data["ProductNo"], "ProductNo"),
            mfr_pn=required_string(data["MfrPN"], "MfrPN"),
            po_number=required_string(data["PONumber"], "PONumber"),
            diameter_mm=required_string(data["diameter_mm"], "diameter_mm"),
            thickness_mm=required_string(data["thickness_mm"], "thickness_mm"),
            shape_result=self.types.ShapeResult(required_string(data["shape_result"], "shape_result")),
            source_position=position,
            request_operation_id=stable_operation_id(evidence, "admission"),
        )

    async def _current_rack_id(self, db: object, runtime: Any) -> str:
        return await current_rack_id(
            db=db, runtime=runtime, rack_positions=self._rack_positions, rack_placements=self._rack_placements
        )


__all__ = ["RoughSorterPluginFactFactory"]
