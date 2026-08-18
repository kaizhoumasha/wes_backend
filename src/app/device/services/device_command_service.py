"""DeviceCommand 应用端口实现。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from src.app.device.contracts import DeviceCommandHandle, DeviceCommandOutcome, DeviceCommandRequest
from src.app.device.models.command import CommandStatus, DeviceCommand, DeviceCommandRequestData
from src.app.device.repositories.command_repository import device_command_repository
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


class CommandRepositoryPort(Protocol):
    async def get_by_execution_ref_for_update(
        self,
        db: object,
        *,
        line_run_epoch_id: int,
        device_code: str,
        execution_ref_type: str,
        execution_ref_id: str,
    ) -> DeviceCommand | None: ...

    async def get_unclosed_for_device_for_update(self, db: object, device_code: str) -> DeviceCommand | None: ...

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


class DeviceCommandService:
    """创建命令并提供与业务无关的 typed outcome。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        command_repository: CommandRepositoryPort | None = None,
        epoch_repository: EpochRepositoryPort | None = None,
        clock: Callable[[], datetime] = timezone.now_for_db,
    ) -> None:
        self._sessions = session_factory
        self._commands = command_repository or device_command_repository
        self._epochs = epoch_repository or line_run_epoch_repository
        self._clock = clock

    async def create_command(self, request: DeviceCommandRequest) -> DeviceCommandHandle:
        async with self._sessions.begin() as db:
            return await self.create_command_in_session(db, request)

    async def create_command_in_session(self, db: object, request: DeviceCommandRequest) -> DeviceCommandHandle:
        """在调用方事务中创建命令；只持久化，不触发设备派发。"""

        validated = DeviceCommandRequestData.model_validate(asdict(request))
        if validated.deadline_at.tzinfo is not None:
            raise DeviceCommandDeadlineError("deadline_at 必须是数据库合同要求的 naive UTC")
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
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "DeviceCommandCapacityError",
    "DeviceCommandDeadlineError",
    "DeviceCommandIdentityConflictError",
    "DeviceCommandService",
    "DeviceContractMismatchError",
    "DeviceNotFoundError",
]
