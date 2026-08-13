"""设备结果和事件 evidence 的持久接收与异步应用。"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any, Protocol

from src.app.device.contracts import (
    DeviceEvidenceReceipt,
    EcsCommandResult,
    EcsCommandResultValue,
    EcsDeviceEvent,
)
from src.app.device.models.command import CommandStatus, DeviceCommand
from src.app.device.models.evidence import (
    DeviceEvidence,
    DeviceEvidenceConflict,
    DeviceEvidenceKind,
)
from src.app.device.repositories.command_repository import device_command_repository
from src.app.device.repositories.evidence_repository import device_evidence_repository
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


class EvidenceRepositoryPort(Protocol):
    async def lock_source_event_id(self, db: object, source_event_id: str) -> None: ...

    async def get_by_source_event_id_for_update(self, db: object, source_event_id: str) -> DeviceEvidence | None: ...

    async def get_result_for_command_for_update(self, db: object, command_code: str) -> DeviceEvidence | None: ...

    async def add(self, db: object, evidence: DeviceEvidence) -> DeviceEvidence: ...

    async def add_conflict(self, db: object, conflict: DeviceEvidenceConflict) -> DeviceEvidenceConflict: ...

    async def claim_next_pending(self, db: object) -> DeviceEvidence | None: ...

    async def mark_applied(self, db: object, evidence: DeviceEvidence, *, processed_at: object) -> None: ...

    async def mark_reconciling(self, db: object, evidence: DeviceEvidence, *, processed_at: object) -> None: ...


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
        evidence_repository: EvidenceRepositoryPort | None = None,
        command_repository: EvidenceCommandRepositoryPort | None = None,
        epoch_repository: EvidenceEpochRepositoryPort | None = None,
    ) -> None:
        self._sessions = session_factory
        self._evidences = evidence_repository or device_evidence_repository
        self._commands = command_repository or device_command_repository
        self._epochs = epoch_repository or line_run_epoch_repository

    async def accept_result(self, result: EcsCommandResult) -> DeviceEvidenceReceipt:
        payload = result.model_dump(mode="json")
        digest = normalized_evidence_digest(payload)
        rejection: Exception | None = None
        receipt: DeviceEvidenceReceipt | None = None
        async with self._sessions.begin() as db:
            await self._evidences.lock_source_event_id(db, result.source_event_id)
            existing = await self._evidences.get_by_source_event_id_for_update(db, result.source_event_id)
            if existing is not None:
                if existing.payload_digest == digest:
                    receipt = _receipt(existing, duplicate=True, trace_id=result.trace_id)
                else:
                    await self._record_conflict(
                        db,
                        first=existing,
                        digest=digest,
                        payload=payload,
                        reason="SOURCE_EVENT_ID_PAYLOAD_CONFLICT",
                    )
                    rejection = DeviceEvidenceConflictError(existing.source_event_id)
            else:
                command = await self._commands.get_by_command_code(db, result.command_code, for_update=True)
                if command is None:
                    raise UnknownDeviceCommandError(result.command_code)
                _validate_result_identity(command, result)

                prior_result = await self._evidences.get_result_for_command_for_update(db, result.command_code)
                if prior_result is not None:
                    await self._record_conflict(
                        db,
                        first=prior_result,
                        digest=digest,
                        payload=payload,
                        reason="COMMAND_RESULT_CONFLICT",
                    )
                    rejection = DeviceResultConflictError(result.command_code)
                else:
                    evidence = await self._evidences.add(
                        db,
                        DeviceEvidence(
                            kind=DeviceEvidenceKind.RESULT,
                            source_event_id=result.source_event_id,
                            device_code=result.device_code,
                            command_code=result.command_code,
                            contract_key=result.contract_key,
                            contract_version=result.contract_version,
                            line_run_epoch_id=command.line_run_epoch_id,
                            payload_digest=digest,
                            raw_payload=payload,
                            received_at=timezone.now_for_db(),
                        ),
                    )
                    receipt = _receipt(evidence, duplicate=False, trace_id=result.trace_id)
        if rejection is not None:
            raise rejection
        if receipt is None:
            raise RuntimeError("result evidence ingress 未产生确定结果")
        return receipt

    async def accept_event(self, event: EcsDeviceEvent) -> DeviceEvidenceReceipt:
        payload = event.model_dump(mode="json")
        digest = normalized_evidence_digest(payload)
        rejection: Exception | None = None
        receipt: DeviceEvidenceReceipt | None = None
        async with self._sessions.begin() as db:
            await self._evidences.lock_source_event_id(db, event.source_event_id)
            existing = await self._evidences.get_by_source_event_id_for_update(db, event.source_event_id)
            if existing is not None:
                if existing.payload_digest == digest:
                    receipt = _receipt(existing, duplicate=True, trace_id=event.trace_id)
                else:
                    await self._record_conflict(
                        db,
                        first=existing,
                        digest=digest,
                        payload=payload,
                        reason="SOURCE_EVENT_ID_PAYLOAD_CONFLICT",
                    )
                    rejection = DeviceEvidenceConflictError(existing.source_event_id)
            else:
                binding = await self._epochs.get_active_binding_for_device(db, event.device_code)
                if binding is not None and (
                    binding.contract_key != event.contract_key or binding.contract_version != event.contract_version
                ):
                    raise DeviceEventContractMismatchError(event.device_code)
                evidence = await self._evidences.add(
                    db,
                    DeviceEvidence(
                        kind=DeviceEvidenceKind.EVENT,
                        source_event_id=event.source_event_id,
                        device_code=event.device_code,
                        command_code=None,
                        contract_key=event.contract_key,
                        contract_version=event.contract_version,
                        line_run_epoch_id=binding.line_run_epoch_id if binding is not None else None,
                        payload_digest=digest,
                        raw_payload=payload,
                        received_at=timezone.now_for_db(),
                    ),
                )
                receipt = _receipt(evidence, duplicate=False, trace_id=event.trace_id)
        if rejection is not None:
            raise rejection
        if receipt is None:
            raise RuntimeError("event evidence ingress 未产生确定结果")
        return receipt

    async def process_one(self) -> bool:
        """异步应用一条 evidence；事件无业务 consumer，仅完成公共验证。"""

        now = timezone.now_for_db()
        async with self._sessions.begin() as db:
            evidence = await self._evidences.claim_next_pending(db)
            if evidence is None:
                return False
            if evidence.kind == DeviceEvidenceKind.EVENT:
                await self._evidences.mark_applied(db, evidence, processed_at=now)
                return True
            if evidence.command_code is None:
                await self._evidences.mark_reconciling(db, evidence, processed_at=now)
                return True
            command = await self._commands.get_by_command_code(db, evidence.command_code, for_update=True)
            if command is None or evidence.line_run_epoch_id != command.line_run_epoch_id:
                await self._evidences.mark_reconciling(db, evidence, processed_at=now)
                return True
            if command.status not in {
                CommandStatus.DISPATCHING,
                CommandStatus.ACKNOWLEDGED,
                CommandStatus.RECONCILING,
            }:
                await self._evidences.mark_reconciling(db, evidence, processed_at=now)
                return True
            payload = EcsCommandResult.model_validate(evidence.raw_payload)
            command.result_evidence_id = evidence.id
            command.transition_to(
                CommandStatus.SUCCEEDED if payload.result is EcsCommandResultValue.SUCCESS else CommandStatus.FAILED
            )
            if payload.result is EcsCommandResultValue.FAILED:
                command.failure_code = "DEVICE_REPORTED_FAILURE"
            command.claim_token = None
            command.claimed_at = None
            command.claim_expires_at = None
            await self._evidences.mark_applied(db, evidence, processed_at=now)
        return True

    async def _record_conflict(
        self,
        db: object,
        *,
        first: DeviceEvidence,
        digest: str,
        payload: dict[str, Any],
        reason: str,
    ) -> None:
        if first.id is None:
            raise RuntimeError("持久化 evidence 缺少主键")
        await self._evidences.add_conflict(
            db,
            DeviceEvidenceConflict(
                source_event_id=payload["source_event_id"],
                first_evidence_id=first.id,
                conflicting_digest=digest,
                raw_payload=payload,
                reason_code=reason,
                received_at=timezone.now_for_db(),
            ),
        )


def normalized_evidence_digest(payload: dict[str, Any]) -> str:
    """按 uniform wire 语义规范化，唯一排除诊断字段 trace_id。"""

    semantic_payload = {key: value for key, value in payload.items() if key != "trace_id"}
    encoded = json.dumps(semantic_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_result_identity(command: DeviceCommand, result: EcsCommandResult) -> None:
    if (
        command.device_code != result.device_code
        or command.contract_key != result.contract_key
        or command.contract_version != result.contract_version
    ):
        raise DeviceResultConflictError(result.command_code)


def _receipt(
    evidence: DeviceEvidence,
    *,
    duplicate: bool,
    trace_id: str | None,
) -> DeviceEvidenceReceipt:
    if evidence.id is None:
        raise RuntimeError("持久化 evidence 缺少主键")
    return DeviceEvidenceReceipt(
        evidence_id=evidence.id,
        source_event_id=evidence.source_event_id,
        duplicate=duplicate,
        trace_id=trace_id,
    )


__all__ = [
    "DeviceEventContractMismatchError",
    "DeviceEvidenceConflictError",
    "DeviceEvidenceService",
    "DeviceResultConflictError",
    "UnknownDeviceCommandError",
    "normalized_evidence_digest",
]
