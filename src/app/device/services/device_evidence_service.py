"""设备结果和事件 evidence 的持久接收与异步应用。"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Protocol

from src.app.device.contracts import (
    DeviceCommandHandle,
    DeviceEvidenceReceipt,
    DeviceEvidenceUpdate,
    DeviceIngressKind,
    EcsCommandResult,
    EcsCommandResultReport,
    EcsCommandResultValue,
    EcsDeviceEvent,
    EcsDeviceEventReport,
    EcsErrorDetail,
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
from src.app.sys.services.event_stream_service import DEVICE_EVIDENCE_STREAM_CHANNEL
from src.app.workline.models.line_run_epoch import LineRunEpochDeviceBinding  # noqa: TC001
from src.app.workline.repositories.line_run_epoch_repository import line_run_epoch_repository
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.core.task_queue_gateway import TaskQueueGateway

logger = logging.getLogger(__name__)

_UNBOUND_EVENT_CONTRACT_KEY = "third_party_integration"
_UNBOUND_EVENT_CONTRACT_VERSION = "1.1"


class UnknownDeviceCommandError(LookupError):
    """结果引用了不存在的命令。"""


class _DeviceEvidenceRejectedError(ValueError):
    """已持久化但 HTTP 拒绝的 evidence，并保留诊断关联。"""

    receipt: DeviceEvidenceReceipt | None = None


class DeviceEvidenceConflictError(_DeviceEvidenceRejectedError):
    """同一 source_event_id 被用于不同语义载荷。"""


class DeviceResultConflictError(_DeviceEvidenceRejectedError):
    """同一命令出现第二个终态结果身份。"""


class DeviceResultOutOfOrderError(_DeviceEvidenceRejectedError):
    """命令尚未下发就收到了 RESULT。"""


class EvidenceProcessingRepositoryPort(Protocol):
    async def get_by_source_identity_for_update(
        self,
        db: object,
        source_identity: str,
    ) -> InboundEvidence | None: ...

    async def claim_next_pending(
        self,
        db: object,
        *,
        kinds: tuple[InboundEvidenceKind, ...],
    ) -> InboundEvidence | None: ...

    async def mark_applied(self, db: object, evidence: InboundEvidence, *, processed_at: object) -> None: ...

    async def mark_ignored(self, db: object, evidence: InboundEvidence, *, processed_at: object) -> None: ...

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


class EventPublisherPort(Protocol):
    async def publish_to(self, channel: str, event_type: str, payload: dict[str, object]) -> bool: ...


class EventDebugCommandServicePort(Protocol):
    async def create_event_debug_command_in_session(
        self,
        db: object,
        *,
        evidence: InboundEvidence,
    ) -> DeviceCommandHandle: ...


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
        task_queue_gateway: TaskQueueGateway | None = None,
        event_publisher: EventPublisherPort | None = None,
        event_debug_command_service: EventDebugCommandServicePort | None = None,
    ) -> None:
        self._sessions = session_factory
        self._ingress = inbound_evidence_service or default_inbound_evidence_service
        self._processing = processing_repository or inbound_evidence_repository
        self._commands = command_repository or device_command_repository
        self._epochs = epoch_repository or line_run_epoch_repository
        self._task_queue = task_queue_gateway
        self._event_publisher = event_publisher
        self._event_debug_commands = event_debug_command_service

    async def accept_result(self, report: EcsCommandResultReport) -> DeviceEvidenceReceipt:
        rejection: Exception | None = None
        receipt: DeviceEvidenceReceipt | None = None
        async with self._sessions.begin() as db:
            command = await self._commands.get_by_command_code(db, report.command_code, for_update=True)
            if command is None:
                raise UnknownDeviceCommandError(report.command_code)
            result = _normalize_result(command, report)
            payload = result.model_dump(mode="json", exclude_unset=True)
            try:
                _validate_result_identity(command, result)
            except DeviceResultConflictError as error:
                rejection = error
            if command.status == CommandStatus.PENDING:
                command.transition_to(CommandStatus.RECONCILING)
                command.reconciliation_reason = "RESULT_BEFORE_DISPATCH"
                if rejection is None:
                    rejection = DeviceResultOutOfOrderError(result.command_code)
            elif (
                rejection is None
                and command.status == CommandStatus.RECONCILING
                and command.reconciliation_reason == "RESULT_BEFORE_DISPATCH"
            ):
                rejection = DeviceResultOutOfOrderError(result.command_code)

            accepted = await self._ingress.accept(
                db,
                kind=InboundEvidenceKind.DEVICE_RESULT,
                source_identity=result.source_event_id,
                normalized_payload=payload,
                received_at=timezone.now_for_db(),
                line_run_epoch_id=command.line_run_epoch_id,
                material_execution_id=command.material_execution_id,
                device_code=result.device_code,
                command_code=result.command_code if rejection is None else None,
                contract_key=result.contract_key,
                contract_version=result.contract_version,
                apply_status=(
                    InboundEvidenceApplyStatus.PENDING if rejection is None else InboundEvidenceApplyStatus.IGNORED
                ),
                digest_policy=InboundEvidenceDigestPolicy.UNIFORM_WIRE,
            )
            if isinstance(accepted, InboundEvidenceConflictResult):
                rejection = DeviceEvidenceConflictError(accepted.evidence.source_identity)
                receipt = _receipt(accepted.evidence, duplicate=False, trace_id=result.trace_id)
                rejection.receipt = receipt
            else:
                receipt = _receipt(accepted.evidence, duplicate=accepted.duplicate, trace_id=result.trace_id)
                if rejection is not None:
                    rejection.receipt = receipt
        if rejection is not None:
            raise rejection
        if receipt is None:
            raise RuntimeError("result evidence ingress 未产生确定结果")
        return receipt

    async def accept_event(self, report: EcsDeviceEventReport) -> DeviceEvidenceReceipt:
        rejection: Exception | None = None
        receipt: DeviceEvidenceReceipt | None = None
        async with self._sessions.begin() as db:
            source_identity = _event_source_identity(report)
            existing = await self._processing.get_by_source_identity_for_update(db, source_identity)
            binding = await self._epochs.get_active_binding_for_device(db, report.device_code)
            contract_key = (
                existing.contract_key
                if existing is not None and existing.contract_key is not None
                else binding.contract_key
                if binding is not None
                else _UNBOUND_EVENT_CONTRACT_KEY
            )
            contract_version = (
                existing.contract_version
                if existing is not None and existing.contract_version is not None
                else binding.contract_version
                if binding is not None
                else _UNBOUND_EVENT_CONTRACT_VERSION
            )
            event = _normalize_event(
                report,
                source_identity=source_identity,
                contract_key=contract_key,
                contract_version=contract_version,
            )
            payload = event.model_dump(mode="json", exclude_unset=True)
            accepted = await self._ingress.accept(
                db,
                kind=InboundEvidenceKind.DEVICE_EVENT,
                source_identity=event.source_event_id,
                normalized_payload=payload,
                received_at=timezone.now_for_db(),
                line_run_epoch_id=(
                    existing.line_run_epoch_id
                    if existing is not None
                    else binding.line_run_epoch_id
                    if binding is not None
                    else None
                ),
                device_code=event.device_code,
                contract_key=event.contract_key,
                contract_version=event.contract_version,
                apply_status=InboundEvidenceApplyStatus.PENDING,
                digest_policy=InboundEvidenceDigestPolicy.UNIFORM_WIRE,
            )
            if isinstance(accepted, InboundEvidenceConflictResult):
                rejection = DeviceEvidenceConflictError(accepted.evidence.source_identity)
                receipt = _receipt(accepted.evidence, duplicate=False, trace_id=event.trace_id)
                rejection.receipt = receipt
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
        wake_execution = False
        update: DeviceEvidenceUpdate | None = None
        debug_command_code: str | None = None
        async with self._sessions.begin() as db:
            evidence = await self._processing.claim_next_pending(
                db,
                kinds=(InboundEvidenceKind.DEVICE_EVENT, InboundEvidenceKind.DEVICE_RESULT),
            )
            if evidence is None:
                return False
            if evidence.kind == InboundEvidenceKind.DEVICE_EVENT:
                event = EcsDeviceEvent.model_validate(evidence.normalized_payload)
                if event.is_debug:
                    if self._event_debug_commands is None:
                        await self._processing.mark_reconciling(db, evidence, processed_at=now)
                    else:
                        try:
                            handle = await self._event_debug_commands.create_event_debug_command_in_session(
                                db,
                                evidence=evidence,
                            )
                        except ValueError:
                            logger.exception("device.event_debug.command_rejected")
                            await self._processing.mark_reconciling(db, evidence, processed_at=now)
                        else:
                            debug_command_code = handle.command_code
                            await self._processing.mark_ignored(db, evidence, processed_at=now)
                else:
                    await self._processing.mark_applied(db, evidence, processed_at=now)
                    wake_execution = True
            elif evidence.command_code is None:
                await self._processing.mark_reconciling(db, evidence, processed_at=now)
            else:
                command = await self._commands.get_by_command_code(db, evidence.command_code, for_update=True)
                if (
                    command is None
                    or evidence.line_run_epoch_id != command.line_run_epoch_id
                    or command.status
                    not in {
                        CommandStatus.DISPATCHING,
                        CommandStatus.ACKNOWLEDGED,
                        CommandStatus.RECONCILING,
                    }
                ):
                    await self._processing.mark_reconciling(db, evidence, processed_at=now)
                else:
                    payload = EcsCommandResult.model_validate(evidence.normalized_payload)
                    command.result_evidence_id = evidence.id
                    command.transition_to(
                        CommandStatus.SUCCEEDED
                        if payload.result is EcsCommandResultValue.SUCCESS
                        else CommandStatus.FAILED
                    )
                    if payload.result is EcsCommandResultValue.FAILED:
                        command.failure_code = "DEVICE_REPORTED_FAILURE"
                    command.claim_token = None
                    command.claimed_at = None
                    command.claim_expires_at = None
                    await self._processing.mark_applied(db, evidence, processed_at=now)
                    wake_execution = evidence.material_execution_id is not None
            update = _evidence_update(evidence, processed_at=now, command_code=debug_command_code)
        await self._publish_update(update)
        if wake_execution:
            self._enqueue_execution_facts()
        return True

    async def _publish_update(self, update: DeviceEvidenceUpdate) -> None:
        if self._event_publisher is None:
            return
        try:
            _ = await self._event_publisher.publish_to(
                DEVICE_EVIDENCE_STREAM_CHANNEL,
                "device_evidence.updated",
                update.model_dump(mode="json"),
            )
        except Exception:
            logger.exception("device.evidence.update_publish_failed")

    def _enqueue_execution_facts(self) -> None:
        if self._task_queue is None:
            return
        try:
            self._task_queue.enqueue_execution_facts()
        except Exception:
            logger.exception("device.evidence.execution_wake_failed", extra={"event": "execution_wake_failed"})


def _normalize_result(command: DeviceCommand, report: EcsCommandResultReport) -> EcsCommandResult:
    payload: dict[str, object] = {
        "command_code": report.command_code,
        "device_code": report.device_code,
        "contract_key": command.contract_key,
        "contract_version": command.contract_version,
        "result": report.result,
        "finish_time": report.finish_time,
        "source_event_id": f"RESULT:{report.command_code}",
        "data": report.data,
        "error_detail": (
            EcsErrorDetail(code=report.error_detail.code, message=report.error_detail.msg)
            if report.error_detail is not None
            else None
        ),
    }
    if command.trace_id is not None:
        payload["trace_id"] = command.trace_id
    return EcsCommandResult.model_validate(payload)


def _event_source_identity(report: EcsDeviceEventReport) -> str:
    wire_payload = report.model_dump(mode="json")
    encoded = json.dumps(wire_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return f"EVENT:{hashlib.sha256(encoded).hexdigest()}"


def _normalize_event(
    report: EcsDeviceEventReport,
    *,
    source_identity: str,
    contract_key: str,
    contract_version: str,
) -> EcsDeviceEvent:
    return EcsDeviceEvent.model_validate(
        {
            "device_code": report.device_code,
            "contract_key": contract_key,
            "contract_version": contract_version,
            "event_type": report.event_type,
            "timestamp": report.timestamp,
            "source_event_id": source_identity,
            "is_debug": report.is_debug,
            "data": report.data,
        }
    )


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
        apply_status=InboundEvidenceApplyStatus(evidence.apply_status).value,
    )


def _evidence_update(
    evidence: InboundEvidence,
    *,
    processed_at: datetime,
    command_code: str | None = None,
) -> DeviceEvidenceUpdate:
    if evidence.id is None or evidence.device_code is None:
        raise RuntimeError("device evidence 缺少 update snapshot 字段")
    kind = DeviceIngressKind(getattr(evidence.kind, "value", evidence.kind))
    raw_event_type = evidence.normalized_payload.get("event_type") if kind is DeviceIngressKind.DEVICE_EVENT else None
    return DeviceEvidenceUpdate(
        evidence_id=evidence.id,
        kind=kind,
        source_event_id=evidence.source_identity,
        device_code=evidence.device_code,
        command_code=command_code or evidence.command_code,
        event_type=raw_event_type if isinstance(raw_event_type, str) else None,
        apply_status=InboundEvidenceApplyStatus(evidence.apply_status).value,
        processed_at=timezone.to_utc(processed_at).isoformat(),
    )


__all__ = [
    "DeviceEvidenceConflictError",
    "DeviceEvidenceService",
    "DeviceResultConflictError",
    "DeviceResultOutOfOrderError",
    "UnknownDeviceCommandError",
]
