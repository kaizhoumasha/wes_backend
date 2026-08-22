"""DeviceCommand 应用端口实现。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from src.app.device.contracts import (
    DeviceCommandCallbackSnapshot,
    DeviceCommandHandle,
    DeviceCommandOutcome,
    DeviceCommandRequest,
    EcsCommandResult,
    ManualDebugDeviceCommandSnapshot,
)
from src.app.device.endpoint import validate_device_endpoint_base_url
from src.app.device.models.command import CommandStatus, DeviceCommand, DeviceCommandRequestData
from src.app.device.repositories.command_repository import device_command_repository
from src.app.execution.models.inbound_evidence import InboundEvidence, InboundEvidenceApplyStatus
from src.app.execution.repositories.inbound_evidence_repository import inbound_evidence_repository
from src.app.workline.models.line_run_epoch import LineRunEpochDeviceBinding  # noqa: TC001
from src.app.workline.repositories.line_run_epoch_repository import (
    line_run_epoch_repository,
)
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class DeviceNotFoundError(LookupError):
    """活动 Epoch 中未绑定目标设备。"""


class DeviceContractMismatchError(ValueError):
    """请求合同与 Epoch 冻结合同不一致。"""


class DeviceCommandCapacityError(RuntimeError):
    """目标设备仍有未闭合命令。"""


class DeviceCommandIdentityConflictError(ValueError):
    """同一插件执行身份被用于不同的不可变命令请求。"""


class DeviceCommandDeadlineError(ValueError):
    """请求截止时间不符合 Epoch 冻结设备合同。"""


class DeviceCommandNotFoundError(LookupError):
    """调试命令不存在或不是 MANUAL_DEBUG 命令。"""


class CommandRepositoryPort(Protocol):
    async def lock_creation_for_device(self, db: object, device_code: str) -> None: ...

    async def lock_manual_debug_identity(self, db: object, client_request_id: str) -> None: ...

    async def get_by_execution_ref_for_update(
        self,
        db: object,
        *,
        line_run_epoch_id: int | None,
        device_code: str,
        execution_ref_type: str,
        execution_ref_id: str,
    ) -> DeviceCommand | None: ...

    async def get_unclosed_for_device_for_update(self, db: object, device_code: str) -> DeviceCommand | None: ...

    async def get_manual_debug_by_client_request_id_for_update(
        self,
        db: object,
        client_request_id: str,
    ) -> DeviceCommand | None: ...

    async def add(self, db: object, command: DeviceCommand) -> DeviceCommand: ...

    async def get_by_command_code(
        self,
        db: object,
        command_code: str,
        *,
        for_update: bool = False,
    ) -> DeviceCommand | None: ...

    async def claim_next_reconcilable(self, db: object, *, now: datetime) -> DeviceCommand | None: ...


class EpochRepositoryPort(Protocol):
    async def get_binding_for_command_creation(
        self,
        db: object,
        *,
        line_run_epoch_id: int,
        device_code: str,
    ) -> LineRunEpochDeviceBinding | None: ...


class EvidenceRepositoryPort(Protocol):
    async def get_device_result_for_command(self, db: object, command_code: str) -> InboundEvidence | None: ...


_MANUAL_DEBUG_REF_TYPE = "MANUAL_DEBUG"


class DeviceCommandService:
    """创建命令并提供与业务无关的 typed outcome。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        command_repository: CommandRepositoryPort | None = None,
        epoch_repository: EpochRepositoryPort | None = None,
        evidence_repository: EvidenceRepositoryPort | None = None,
        clock: Callable[[], datetime] = timezone.now_for_db,
    ) -> None:
        self._sessions = session_factory
        self._commands = command_repository or device_command_repository
        self._epochs = epoch_repository or line_run_epoch_repository
        self._evidences = evidence_repository or inbound_evidence_repository
        self._clock = clock

    async def create_command(self, request: DeviceCommandRequest) -> DeviceCommandHandle:
        async with self._sessions.begin() as db:
            return await self.create_command_in_session(db, request)

    async def create_command_in_session(self, db: object, request: DeviceCommandRequest) -> DeviceCommandHandle:
        """在调用方事务中创建命令；只持久化，不触发设备派发。"""

        validated = DeviceCommandRequestData.model_validate(asdict(request))
        if validated.deadline_at.tzinfo is not None:
            raise DeviceCommandDeadlineError("deadline_at 必须是数据库合同要求的 naive UTC")
        await self._commands.lock_creation_for_device(db, validated.device_code)
        binding = await self._epochs.get_binding_for_command_creation(
            db,
            line_run_epoch_id=validated.line_run_epoch_id,
            device_code=validated.device_code,
        )
        if binding is None or binding.id is None:
            raise DeviceNotFoundError(validated.device_code)
        if binding.contract_key != validated.contract_key or binding.contract_version != validated.contract_version:
            raise DeviceContractMismatchError(validated.device_code)
        payload_digest = _command_payload_digest(validated)
        same_identity = await self._commands.get_by_execution_ref_for_update(
            db,
            line_run_epoch_id=validated.line_run_epoch_id,
            device_code=validated.device_code,
            execution_ref_type=validated.execution_ref_type,
            execution_ref_id=validated.execution_ref_id,
        )
        if same_identity is not None:
            if same_identity.payload_digest != payload_digest or same_identity.deadline_at != validated.deadline_at:
                raise DeviceCommandIdentityConflictError(validated.execution_ref_id)
            return DeviceCommandHandle(
                command_code=same_identity.command_code,
                status=CommandStatus(same_identity.status),
            )
        now = self._clock()
        if validated.deadline_at <= now or validated.deadline_at > now + timedelta(
            milliseconds=binding.command_timeout_ms
        ):
            raise DeviceCommandDeadlineError("deadline_at 超出冻结 binding 的 command_timeout_ms")
        existing = await self._commands.get_unclosed_for_device_for_update(db, validated.device_code)
        if existing is not None:
            raise DeviceCommandCapacityError(validated.device_code)
        command = DeviceCommand(
            command_code=new_uuid7(),
            device_code=validated.device_code,
            line_run_epoch_id=validated.line_run_epoch_id,
            device_binding_id=binding.id,
            execution_ref_type=validated.execution_ref_type,
            execution_ref_id=validated.execution_ref_id,
            material_execution_id=validated.material_execution_id,
            contract_key=validated.contract_key,
            contract_version=validated.contract_version,
            task_type=validated.task_type,
            params=validated.params,
            payload_digest=payload_digest,
            deadline_at=validated.deadline_at,
            trace_id=validated.trace_id,
            next_attempt_at=now,
            created_at=now,
        )
        persisted = await self._commands.add(db, command)
        return DeviceCommandHandle(command_code=persisted.command_code, status=CommandStatus(persisted.status))

    async def create_manual_debug_command(
        self,
        *,
        client_request_id: str,
        endpoint_base_url: str,
        device_code: str,
        contract_key: str,
        contract_version: str,
        command_timeout_ms: int,
        task_type: str,
        params: dict[str, object],
        trace_id: str | None,
    ) -> DeviceCommandHandle:
        """创建不依赖 WorkLine/Epoch 的供应商联调命令。"""

        endpoint = validate_device_endpoint_base_url(endpoint_base_url)
        now = self._clock()
        try:
            deadline_at = now + timedelta(milliseconds=command_timeout_ms)
        except (OverflowError, TypeError) as error:
            raise DeviceCommandDeadlineError("command_timeout_ms 无法形成有效截止时间") from error
        validated = DeviceCommandRequestData.model_validate(
            {
                "device_code": device_code,
                "line_run_epoch_id": None,
                "execution_ref_type": _MANUAL_DEBUG_REF_TYPE,
                "execution_ref_id": client_request_id,
                "material_execution_id": None,
                "contract_key": contract_key,
                "contract_version": contract_version,
                "task_type": task_type,
                "params": params,
                "deadline_at": deadline_at,
                "trace_id": trace_id,
                "endpoint_base_url": endpoint,
                "command_timeout_ms": command_timeout_ms,
            }
        )
        payload_digest = _command_payload_digest(validated)
        async with self._sessions.begin() as db:
            await self._commands.lock_manual_debug_identity(db, validated.execution_ref_id)
            await self._commands.lock_creation_for_device(db, validated.device_code)
            same_identity = await self._commands.get_manual_debug_by_client_request_id_for_update(
                db, validated.execution_ref_id
            )
            if same_identity is not None:
                if same_identity.payload_digest != payload_digest:
                    raise DeviceCommandIdentityConflictError(validated.execution_ref_id)
                return DeviceCommandHandle(
                    command_code=same_identity.command_code,
                    status=CommandStatus(same_identity.status),
                )
            existing = await self._commands.get_unclosed_for_device_for_update(db, validated.device_code)
            if existing is not None:
                raise DeviceCommandCapacityError(validated.device_code)
            command = DeviceCommand(
                command_code=new_uuid7(),
                device_code=validated.device_code,
                line_run_epoch_id=None,
                device_binding_id=None,
                execution_ref_type=_MANUAL_DEBUG_REF_TYPE,
                execution_ref_id=validated.execution_ref_id,
                material_execution_id=None,
                contract_key=validated.contract_key,
                contract_version=validated.contract_version,
                task_type=validated.task_type,
                params=validated.params,
                payload_digest=payload_digest,
                deadline_at=validated.deadline_at,
                trace_id=validated.trace_id,
                endpoint_base_url=validated.endpoint_base_url,
                command_timeout_ms=validated.command_timeout_ms,
                next_attempt_at=now,
                created_at=now,
            )
            persisted = await self._commands.add(db, command)
        return DeviceCommandHandle(command_code=persisted.command_code, status=CommandStatus(persisted.status))

    async def get_command_snapshot(self, command_code: str) -> ManualDebugDeviceCommandSnapshot:
        async with self._sessions.begin() as db:
            command = await self._commands.get_by_command_code(db, command_code)
            if command is None or command.execution_ref_type != _MANUAL_DEBUG_REF_TYPE:
                raise DeviceCommandNotFoundError(command_code)
            evidence = await self._evidences.get_device_result_for_command(db, command_code)
        if command.endpoint_base_url is None or command.command_timeout_ms is None:
            raise RuntimeError("MANUAL_DEBUG DeviceCommand 缺少冻结派发上下文")
        callback = None
        if evidence is not None:
            result = EcsCommandResult.model_validate(evidence.normalized_payload)
            callback = DeviceCommandCallbackSnapshot(
                result=result.result.value,
                data=result.data,
                error_detail=(result.error_detail.model_dump(mode="json") if result.error_detail is not None else None),
                source_event_id=result.source_event_id,
                received_at=evidence.received_at,
                apply_status=InboundEvidenceApplyStatus(evidence.apply_status).value,
            )
        return ManualDebugDeviceCommandSnapshot(
            command_code=command.command_code,
            client_request_id=command.execution_ref_id,
            device_code=command.device_code,
            endpoint_base_url=command.endpoint_base_url,
            contract_key=command.contract_key,
            contract_version=command.contract_version,
            command_timeout_ms=command.command_timeout_ms,
            task_type=command.task_type,
            params=command.params,
            trace_id=command.trace_id,
            status=CommandStatus(command.status),
            attempt_count=command.attempt_count,
            ack_received_at=command.ack_received_at,
            completed_at=command.completed_at,
            failure_code=command.failure_code,
            reconciliation_reason=command.reconciliation_reason,
            callback=callback,
        )

    async def get_outcome(self, command_code: str) -> DeviceCommandOutcome | None:
        async with self._sessions.begin() as db:
            command = await self._commands.get_by_command_code(db, command_code)
        if command is None or command.occupies_device_slot:
            return None
        return DeviceCommandOutcome(
            command_code=command.command_code,
            status=CommandStatus(command.status),
            failure_code=command.failure_code,
            completed_at=command.completed_at,
            version=command.version,
        )

    async def reconcile_one(self, *, now: datetime) -> bool:
        """推进一条到期命令；只有未发送的 PENDING 可以进入 TIMED_OUT。"""

        async with self._sessions.begin() as db:
            command = await self._commands.claim_next_reconcilable(db, now=now)
            if command is None:
                return False
            status = CommandStatus(command.status)
            if status is CommandStatus.PENDING:
                command.transition_to(CommandStatus.TIMED_OUT)
            elif status is CommandStatus.DISPATCHING:
                command.reconciliation_reason = "DISPATCH_LEASE_EXPIRED"
                command.transition_to(CommandStatus.RECONCILING)
            elif status is CommandStatus.ACKNOWLEDGED:
                command.reconciliation_reason = "ACK_DEADLINE_EXPIRED"
                command.transition_to(CommandStatus.RECONCILING)
            else:
                raise RuntimeError(f"不可对账的 DeviceCommand 状态: {status.value}")
        return True


def _command_payload_digest(request: DeviceCommandRequestData) -> str:
    payload = {
        "device_code": request.device_code,
        "material_execution_id": request.material_execution_id,
        "contract_key": request.contract_key,
        "contract_version": request.contract_version,
        "task_type": request.task_type,
        "params": request.params,
    }
    if request.execution_ref_type == _MANUAL_DEBUG_REF_TYPE:
        payload.update(
            {
                "endpoint_base_url": request.endpoint_base_url,
                "command_timeout_ms": request.command_timeout_ms,
            }
        )
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "DeviceCommandCapacityError",
    "DeviceCommandDeadlineError",
    "DeviceCommandIdentityConflictError",
    "DeviceCommandNotFoundError",
    "DeviceCommandService",
    "DeviceContractMismatchError",
    "DeviceNotFoundError",
]
