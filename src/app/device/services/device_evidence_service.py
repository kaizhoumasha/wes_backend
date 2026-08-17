"""设备结果和事件 evidence 的持久接收与异步应用。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from src.app.device.contracts import (
    DeviceEvidenceReceipt,
    EcsCommandResult,
    EcsCommandResultValue,
    EcsDeviceEvent,
)
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.repositories.command_repository import device_command_repository
from src.app.execution.models.inbound_evidence import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
    InboundEvidenceKind,
)
from src.app.execution.repositories.inbound_evidence_repository import inbound_evidence_repository
from src.app.execution.services.inbound_evidence_service import (
    InboundEvidenceConflictResult,
    InboundEvidenceDigestPolicy,
    InboundEvidenceService,
)
from src.app.execution.services.inbound_evidence_service import (
    inbound_evidence_service as default_inbound_evidence_service,
)
from src.app.workline.models.line_run_epoch import LineRunEpochDeviceBinding  # noqa: TC001
from src.app.workline.repositories.line_run_epoch_repository import line_run_epoch_repository
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class UnknownDeviceCommandError(LookupError):
    """结果引用了不存在的命令。"""


class DeviceEvidenceConflictError(ValueError):
    """同一 source_event_id 被用于不同语义载荷。"""


class DeviceResultConflictError(ValueError):
    """同一命令出现第二个终态结果身份。"""


class DeviceEventContractMismatchError(ValueError):
    """事件合同与活动 Epoch 冻结绑定不一致。"""


class EvidenceProcessingRepositoryPort(Protocol):
    async def get_device_result_for_command_for_update(
        self,
        db: object,
        command_code: str,
    ) -> InboundEvidence | None: ...

    async def claim_next_pending(
        self,
        db: object,
        *,
        kinds: tuple[InboundEvidenceKind, ...],
    ) -> InboundEvidence | None: ...

    async def mark_applied(self, db: object, evidence: InboundEvidence, *, processed_at: object) -> None: ...

    async def mark_reconciling(self, db: object, evidence: InboundEvidence, *, processed_at: object) -> None: ...


class EvidenceCommandRepositoryPort(Protocol):
    async def get_by_command_code(
        self,
        db: object,
        command_code: str,
        *,
        for_update: bool = False,
    ) -> DeviceCommand | None: ...


class EvidenceEpochRepositoryPort(Protocol):
    async def get_active_binding_for_device(self, db: object, device_code: str) -> LineRunEpochDeviceBinding | None: ...


class DeviceEvidenceService:
    """把外部 callback 先固化为证据；不在 ingress 中推进业务对象。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        inbound_evidence_service: InboundEvidenceService | None = None,
        processing_repository: EvidenceProcessingRepositoryPort | None = None,
        command_repository: EvidenceCommandRepositoryPort | None = None,
        epoch_repository: EvidenceEpochRepositoryPort | None = None,
    ) -> None:
        self._sessions = session_factory
        self._ingress = inbound_evidence_service or default_inbound_evidence_service
        self._processing = processing_repository or inbound_evidence_repository
        self._commands = command_repository or device_command_repository
        self._epochs = epoch_repository or line_run_epoch_repository

    async def accept_result(self, result: EcsCommandResult) -> DeviceEvidenceReceipt:
        payload = result.model_dump(mode="json", exclude_unset=True)
        rejection: Exception | None = None
        receipt: DeviceEvidenceReceipt | None = None
        async with self._sessions.begin() as db:
            command = await self._commands.get_by_command_code(db, result.command_code, for_update=True)
            line_run_epoch_id = command.line_run_epoch_id if command is not None else None
            command_code: str | None = None
            apply_status = InboundEvidenceApplyStatus.IGNORED
            prior_result: InboundEvidence | None = None
            if command is None:
                rejection = UnknownDeviceCommandError(result.command_code)
            else:
                try:
                    _validate_result_identity(command, result)
                except DeviceResultConflictError as error:
                    rejection = error
                if rejection is None:
                    prior_result = await self._processing.get_device_result_for_command_for_update(
                        db,
                        result.command_code,
                    )
                    if prior_result is None or prior_result.source_identity == result.source_event_id:
                        command_code = result.command_code
                        apply_status = InboundEvidenceApplyStatus.PENDING
                    else:
                        rejection = DeviceResultConflictError(result.command_code)

            accepted = await self._ingress.accept(
                db,
                kind=InboundEvidenceKind.DEVICE_RESULT,
                source_identity=result.source_event_id,
                normalized_payload=payload,
                received_at=timezone.now_for_db(),
                line_run_epoch_id=line_run_epoch_id,
                material_execution_id=command.material_execution_id if command is not None else None,
                device_code=result.device_code,
                command_code=command_code,
                contract_key=result.contract_key,
                contract_version=result.contract_version,
                apply_status=apply_status,
                digest_policy=InboundEvidenceDigestPolicy.UNIFORM_WIRE,
            )
            if isinstance(accepted, InboundEvidenceConflictResult):
                if (
                    accepted.conflict.reason_code == "SOURCE_IDENTITY_CORRELATION_CONFLICT"
                    and accepted.evidence.apply_status == InboundEvidenceApplyStatus.IGNORED
                    and accepted.evidence.command_code is None
                ):
                    rejection = UnknownDeviceCommandError(result.command_code)
                else:
                    rejection = DeviceEvidenceConflictError(accepted.evidence.source_identity)
            elif isinstance(rejection, DeviceResultConflictError) and prior_result is not None:
                await self._ingress.record_conflict(
                    db,
                    first=prior_result,
                    source_identity=result.source_event_id,
                    normalized_payload=payload,
                    reason_code="COMMAND_RESULT_CONFLICT",
                    received_at=timezone.now_for_db(),
                    digest_policy=InboundEvidenceDigestPolicy.UNIFORM_WIRE,
                )
            elif rejection is None:
                receipt = _receipt(accepted.evidence, duplicate=accepted.duplicate, trace_id=result.trace_id)
        if rejection is not None:
            raise rejection
        if receipt is None:
            raise RuntimeError("result evidence ingress 未产生确定结果")
        return receipt

    async def accept_event(self, event: EcsDeviceEvent) -> DeviceEvidenceReceipt:
        payload = event.model_dump(mode="json", exclude_unset=True)
        rejection: Exception | None = None
        receipt: DeviceEvidenceReceipt | None = None
        async with self._sessions.begin() as db:
            binding = await self._epochs.get_active_binding_for_device(db, event.device_code)
            binding_matches = binding is None or (
                binding.contract_key == event.contract_key and binding.contract_version == event.contract_version
            )
            accepted = await self._ingress.accept(
                db,
                kind=InboundEvidenceKind.DEVICE_EVENT,
                source_identity=event.source_event_id,
                normalized_payload=payload,
                received_at=timezone.now_for_db(),
                line_run_epoch_id=binding.line_run_epoch_id if binding is not None else None,
                device_code=event.device_code,
                contract_key=event.contract_key,
                contract_version=event.contract_version,
                apply_status=(
                    InboundEvidenceApplyStatus.PENDING if binding_matches else InboundEvidenceApplyStatus.IGNORED
                ),
                digest_policy=InboundEvidenceDigestPolicy.UNIFORM_WIRE,
            )
            if isinstance(accepted, InboundEvidenceConflictResult):
                rejection = (
                    DeviceEventContractMismatchError(event.device_code)
                    if accepted.conflict.reason_code == "SOURCE_IDENTITY_CORRELATION_CONFLICT"
                    else DeviceEvidenceConflictError(accepted.evidence.source_identity)
                )
            elif accepted.duplicate and accepted.evidence.apply_status != InboundEvidenceApplyStatus.IGNORED:
                receipt = _receipt(accepted.evidence, duplicate=True, trace_id=event.trace_id)
            elif not binding_matches or accepted.evidence.apply_status == InboundEvidenceApplyStatus.IGNORED:
                rejection = DeviceEventContractMismatchError(event.device_code)
            else:
                receipt = _receipt(accepted.evidence, duplicate=accepted.duplicate, trace_id=event.trace_id)
        if rejection is not None:
            raise rejection
        if receipt is None:
            raise RuntimeError("event evidence ingress 未产生确定结果")
        return receipt

    async def process_one(self) -> bool:
        """异步完成设备 evidence 的基础验证，业务消费由 FactProcessor 承接。"""

        now = timezone.now_for_db()
        async with self._sessions.begin() as db:
            evidence = await self._processing.claim_next_pending(
                db,
                kinds=(InboundEvidenceKind.DEVICE_EVENT, InboundEvidenceKind.DEVICE_RESULT),
            )
            if evidence is None:
                return False
            if evidence.kind == InboundEvidenceKind.DEVICE_EVENT:
                await self._processing.mark_applied(db, evidence, processed_at=now)
                return True
            if evidence.command_code is None:
                await self._processing.mark_reconciling(db, evidence, processed_at=now)
                return True
            command = await self._commands.get_by_command_code(db, evidence.command_code, for_update=True)
            if command is None or evidence.line_run_epoch_id != command.line_run_epoch_id:
                await self._processing.mark_reconciling(db, evidence, processed_at=now)
                return True
            if command.status not in {
                CommandStatus.DISPATCHING,
                CommandStatus.ACKNOWLEDGED,
                CommandStatus.RECONCILING,
            }:
                await self._processing.mark_reconciling(db, evidence, processed_at=now)
                return True
            payload = EcsCommandResult.model_validate(evidence.normalized_payload)
            command.result_evidence_id = evidence.id
            command.transition_to(
                CommandStatus.SUCCEEDED if payload.result is EcsCommandResultValue.SUCCESS else CommandStatus.FAILED
            )
            if payload.result is EcsCommandResultValue.FAILED:
                command.failure_code = "DEVICE_REPORTED_FAILURE"
            command.claim_token = None
            command.claimed_at = None
            command.claim_expires_at = None
            await self._processing.mark_applied(db, evidence, processed_at=now)
        return True


def _validate_result_identity(command: DeviceCommand, result: EcsCommandResult) -> None:
    if (
        command.device_code != result.device_code
        or command.contract_key != result.contract_key
        or command.contract_version != result.contract_version
    ):
        raise DeviceResultConflictError(result.command_code)


def _receipt(
    evidence: InboundEvidence,
    *,
    duplicate: bool,
    trace_id: str | None,
) -> DeviceEvidenceReceipt:
    if evidence.id is None:
        raise RuntimeError("持久化 evidence 缺少主键")
    return DeviceEvidenceReceipt(
        evidence_id=evidence.id,
        source_event_id=evidence.source_identity,
        duplicate=duplicate,
        trace_id=trace_id,
    )


__all__ = [
    "DeviceEventContractMismatchError",
    "DeviceEvidenceConflictError",
    "DeviceEvidenceService",
    "DeviceResultConflictError",
    "UnknownDeviceCommandError",
]
