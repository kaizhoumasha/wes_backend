"""把 SDK 封闭 Decision 原子映射到已有领域应用端口。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, cast

from wes_plugin_sdk import (
    CompleteExecution,
    CreateDeviceCommand,
    CreateTransportTask,
    CreateWmsConfirmation,
    FactReference,
    PauseForReconciliation,
    TransportRackPosition,
    TransportRackReference,
    TransportZonePosition,
    Wait,
)

from src.app.device.contracts import DeviceCommandRequest
from src.app.execution.models import InboundEvidence, MaterialExecution, TransportDecisionBinding
from src.app.execution.models.material_execution import MaterialExecutionStatus
from src.app.execution.repositories import transport_decision_binding_repository
from src.app.execution.services.wms_confirmation_service import (
    WMS_CONFIRMATION_DISPATCH_WINDOW,
    WmsConfirmationIdentityConflictResult,
)
from src.app.transport.contracts import (
    RackPosition,
    RackReference,
    RcsTemplateId,
    TransportCaller,
    TransportExecutionAuthority,
    ZonePosition,
)
from src.app.wms_adapter.inbound_wire import parse_outbound_request
from src.app.workline.repositories.line_run_epoch_repository import line_run_epoch_repository
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from src.app.workline.models.line_run_epoch import LineRunEpochDeviceBinding


class EpochRepositoryPort(Protocol):
    async def get_binding_by_role_and_code_for_update(
        self,
        db: object,
        *,
        line_run_epoch_id: int,
        device_role: str,
        device_code: str,
    ) -> LineRunEpochDeviceBinding | None: ...


class DeviceCommandServicePort(Protocol):
    async def create_command_in_session(self, db: object, request: DeviceCommandRequest) -> object: ...


class WmsConfirmationServicePort(Protocol):
    async def create_or_get(self, db: object, **kwargs: object) -> object: ...


class TransportBindingRepositoryPort(Protocol):
    async def lock_resource_fence(self, db: object, *, line_run_epoch_id: int, resource_fence_id: str) -> None: ...

    async def lock_decision_identity(
        self,
        db: object,
        *,
        line_run_epoch_id: int,
        correlation_id: str,
        step: str,
    ) -> None: ...

    async def get_by_decision_identity_for_update(
        self,
        db: object,
        *,
        line_run_epoch_id: int,
        correlation_id: str,
        step: str,
    ) -> TransportDecisionBinding | None: ...

    async def add(
        self,
        db: object,
        binding: TransportDecisionBinding,
    ) -> TransportDecisionBinding: ...


class TransportServicePort(Protocol):
    async def move_rack_in_session(self, db: object, **kwargs: object) -> object: ...


class MaterialExecutionServicePort(Protocol):
    async def transition(self, db: object, execution: MaterialExecution, **kwargs: object) -> MaterialExecution: ...


_DECISION_DISCRIMINATORS: dict[type[object], str] = {
    Wait: "WAIT",
    CreateDeviceCommand: "CREATE_DEVICE_COMMAND",
    CreateWmsConfirmation: "CREATE_WMS_CONFIRMATION",
    CreateTransportTask: "CREATE_TRANSPORT_TASK",
    PauseForReconciliation: "PAUSE_FOR_RECONCILIATION",
    CompleteExecution: "COMPLETE_EXECUTION",
}


def decision_digest(decisions: tuple[object, ...]) -> str:
    if type(decisions) is not tuple or not decisions:
        raise ValueError("handler must return a non-empty Decision tuple")
    canonical: list[dict[str, object]] = []
    for ordinal, decision in enumerate(decisions):
        discriminator = _DECISION_DISCRIMINATORS.get(type(decision))
        if discriminator is None:
            raise TypeError(f"unsupported Decision: {type(decision).__name__}")
        canonical.append(
            {
                "decision_type": discriminator,
                "ordinal": ordinal,
                "payload": asdict(cast("Any", decision)),
            }
        )
    return canonical_json_digest(canonical)


class DecisionApplier:
    def __init__(
        self,
        *,
        epoch_repository: EpochRepositoryPort | None = None,
        device_command_service: DeviceCommandServicePort,
        wms_confirmation_service: WmsConfirmationServicePort,
        transport_binding_repository: TransportBindingRepositoryPort | None = None,
        transport_service: TransportServicePort,
        material_execution_service: MaterialExecutionServicePort,
        clock: Any = timezone.now_for_db,
        uuid_factory: Any = new_uuid7,
    ) -> None:
        self._epochs: EpochRepositoryPort = epoch_repository or line_run_epoch_repository
        self._device_commands = device_command_service
        self._wms_confirmations = wms_confirmation_service
        self._transport_bindings: TransportBindingRepositoryPort = (
            transport_binding_repository or transport_decision_binding_repository
        )
        self._transport = transport_service
        self._executions = material_execution_service
        self._clock = clock
        self._uuid_factory = uuid_factory

    async def apply(
        self,
        db: object,
        evidence: InboundEvidence,
        execution: MaterialExecution,
        fact: FactReference,
        decisions: tuple[object, ...],
    ) -> str:
        digest = decision_digest(decisions)
        self._validate_identity(evidence, execution, fact, decisions)
        for ordinal, decision in enumerate(decisions):
            await self._apply_one(db, evidence, execution, ordinal, decision)
        return digest

    @staticmethod
    def _validate_identity(
        evidence: InboundEvidence,
        execution: MaterialExecution,
        fact: FactReference,
        decisions: tuple[object, ...],
    ) -> None:
        if evidence.id is None or execution.id is None:
            raise ValueError("Decision 只能引用已持久化 evidence 和 execution")
        if fact.evidence_id != str(evidence.id) or fact.material_execution_id != execution.execution_code:
            raise ValueError("Fact identity 与持久化对象不匹配")
        for decision in decisions:
            if decision.material_execution_id != execution.execution_code:
                raise ValueError("Decision material_execution_id 与当前 execution 不匹配")
            if decision.fact_id != fact.fact_id:
                raise ValueError("Decision fact_id 与当前 Fact 不匹配")
            if type(decision) is CreateDeviceCommand and decision.material_trace_id != execution.material_trace_id:
                raise ValueError("CreateDeviceCommand material_trace_id 与当前 execution 不匹配")

    async def _apply_one(
        self,
        db: object,
        evidence: InboundEvidence,
        execution: MaterialExecution,
        ordinal: int,
        decision: object,
    ) -> None:
        now = self._clock()
        if type(decision) is Wait:
            await self._transition(db, execution, evidence, MaterialExecutionStatus.HOLD, decision.reason_code, now)
            return
        if type(decision) is PauseForReconciliation:
            await self._transition(
                db,
                execution,
                evidence,
                MaterialExecutionStatus.RECONCILING,
                decision.reason_code,
                now,
                refresh_reconciliation_fence=True,
            )
            return
        if type(decision) is CompleteExecution:
            await self._transition(db, execution, evidence, MaterialExecutionStatus.CLOSED, decision.reason_code, now)
            return
        if type(decision) is CreateDeviceCommand:
            await self._create_device_command(db, evidence, execution, ordinal, decision, now)
        elif type(decision) is CreateWmsConfirmation:
            await self._create_wms_confirmation(db, evidence, execution, decision, now)
        elif type(decision) is CreateTransportTask:
            await self._create_transport_task(db, evidence, execution, decision)
        else:  # decision_digest 已封闭类型，这里保持 fail closed 防御。
            raise TypeError(f"unsupported Decision: {type(decision).__name__}")
        await self._transition(db, execution, evidence, MaterialExecutionStatus.RUNNING, type(decision).__name__, now)

    async def _create_device_command(
        self,
        db: object,
        evidence: InboundEvidence,
        execution: MaterialExecution,
        ordinal: int,
        decision: CreateDeviceCommand,
        now: datetime,
    ) -> None:
        binding = await self._epochs.get_binding_by_role_and_code_for_update(
            db,
            line_run_epoch_id=execution.line_run_epoch_id,
            device_role=decision.device_role,
            device_code=decision.device_code,
        )
        if binding is None:
            raise LookupError(f"Epoch 未绑定指定设备: {decision.device_role}/{decision.device_code}")
        params = {
            "material_trace_id": decision.material_trace_id,
            "source": asdict(decision.source),
            "target": asdict(decision.target),
        }
        _ = await self._device_commands.create_command_in_session(
            db,
            DeviceCommandRequest(
                device_code=binding.device_code,
                line_run_epoch_id=execution.line_run_epoch_id,
                execution_ref_type="PLUGIN_DECISION",
                execution_ref_id=(f"evidence:{evidence.id}:execution:{execution.id}:CREATE_DEVICE_COMMAND:{ordinal}"),
                material_execution_id=cast("int", execution.id),
                contract_key=binding.contract_key,
                contract_version=binding.contract_version,
                task_type=decision.task_type,
                params=cast("Any", params),
                deadline_at=now + timedelta(milliseconds=binding.command_timeout_ms),
            ),
        )

    async def _create_wms_confirmation(
        self,
        db: object,
        evidence: InboundEvidence,
        execution: MaterialExecution,
        decision: CreateWmsConfirmation,
        now: datetime,
    ) -> None:
        timestamp = int(timezone.to_utc(evidence.received_at).timestamp() * 1000)
        request_payload = parse_outbound_request(
            {
                "operation": decision.operation,
                "operation_id": decision.operation_id,
                "timestamp": timestamp,
                "data": decision.request_data,
            }
        ).model_dump(mode="json", exclude_none=True)
        result = await self._wms_confirmations.create_or_get(
            db,
            operation=decision.operation,
            operation_id=decision.operation_id,
            material_execution_id=cast("int", execution.id),
            request_payload=request_payload,
            deadline_at=evidence.received_at + WMS_CONFIRMATION_DISPATCH_WINDOW,
            created_at=now,
        )
        if isinstance(result, WmsConfirmationIdentityConflictResult):
            raise result.to_exception()

    async def _create_transport_task(
        self,
        db: object,
        evidence: InboundEvidence,
        execution: MaterialExecution,
        decision: CreateTransportTask,
    ) -> None:
        step = decision.step
        await self._transport_bindings.lock_resource_fence(
            db,
            line_run_epoch_id=execution.line_run_epoch_id,
            resource_fence_id=decision.resource_fence_id,
        )
        await self._transport_bindings.lock_decision_identity(
            db,
            line_run_epoch_id=execution.line_run_epoch_id,
            correlation_id=decision.correlation_id,
            step=step,
        )
        binding = await self._transport_bindings.get_by_decision_identity_for_update(
            db,
            line_run_epoch_id=execution.line_run_epoch_id,
            correlation_id=decision.correlation_id,
            step=step,
        )
        if binding is None:
            binding = await self._transport_bindings.add(
                db,
                TransportDecisionBinding(
                    correlation_id=decision.correlation_id,
                    step=step,
                    line_run_epoch_id=execution.line_run_epoch_id,
                    resource_fence_id=decision.resource_fence_id,
                    client_request_id=self._uuid_factory(),
                    source_evidence_id=cast("int", evidence.id),
                ),
            )
        elif (
            binding.line_run_epoch_id != execution.line_run_epoch_id
            or binding.resource_fence_id != decision.resource_fence_id
            or binding.source_evidence_id != evidence.id
        ):
            raise ValueError("existing transport decision binding conflict")
        if type(decision.source) is TransportRackReference:
            source = RackReference(decision.source.location_code)
        elif type(decision.source) is TransportZonePosition:
            source = ZonePosition(decision.source.location_code)
        elif type(decision.source) is TransportRackPosition:
            source = RackPosition(decision.source.location_code)
        else:
            raise TypeError("unsupported Transport rack source")
        if type(decision.target) is TransportRackReference:
            target = RackReference(decision.target.location_code)
        elif type(decision.target) is TransportZonePosition:
            target = ZonePosition(decision.target.location_code)
        elif type(decision.target) is TransportRackPosition:
            target = RackPosition(decision.target.location_code)
        else:
            raise TypeError("unsupported Transport rack target")
        _ = await self._transport.move_rack_in_session(
            db,
            client_request_id=binding.client_request_id,
            caller=TransportCaller(workline_id=str(execution.workline_id)),
            rack_id=decision.rack_id,
            source=source,
            target=target,
            target_face=decision.target_face,
            rcs_template_id=RcsTemplateId(decision.rcs_template_id.value),
            execution_authority=TransportExecutionAuthority(
                workline_id=execution.workline_id,
                line_run_epoch_id=execution.line_run_epoch_id,
            ),
        )

    async def _transition(
        self,
        db: object,
        execution: MaterialExecution,
        evidence: InboundEvidence,
        target: MaterialExecutionStatus,
        reason_code: str,
        changed_at: datetime,
        *,
        refresh_reconciliation_fence: bool = False,
    ) -> None:
        _ = await self._executions.transition(
            db,
            execution,
            target=target,
            changed_at=changed_at,
            reason_code=reason_code,
            evidence_id=cast("int", evidence.id),
            refresh_reconciliation_fence=refresh_reconciliation_fence,
        )


__all__ = [
    "DecisionApplier",
    "decision_digest",
]
