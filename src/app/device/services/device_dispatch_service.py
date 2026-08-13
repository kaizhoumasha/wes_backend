"""DeviceCommand 状态准入、派发与 fenced 写回。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from src.app.device.contracts import EcsDeviceMode, EcsDeviceState, EcsDeviceStatus, EcsSubmitDisposition
from src.app.device.ecs_adapter import EcsAdapter  # noqa: TC001
from src.app.device.models.command import DeviceCommand  # noqa: TC001
from src.app.device.models.evidence import DeviceStatusObservation
from src.app.device.repositories.command_repository import device_command_repository
from src.app.device.repositories.evidence_repository import device_evidence_repository
from src.app.workline.models.line_run_epoch import LineRunEpochDeviceBinding  # noqa: TC001
from src.app.workline.repositories.line_run_epoch_repository import line_run_epoch_repository
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class DeviceDispatchAdmissionError(ValueError):
    """设备状态不满足可靠派发条件。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DispatchCommandRepositoryPort(Protocol):
    async def claim_next_pending(self, db: object, *, token: str, now: datetime, claim_expires_at: datetime): ...

    async def get_claimed_for_update(self, db: object, *, command_code: str, claim_token: str): ...

    async def release_retryable(self, db: object, command: DeviceCommand, *, next_attempt_at: datetime): ...

    async def mark_acknowledged(self, db: object, command: DeviceCommand, *, acknowledged_at: datetime): ...

    async def mark_failed(self, db: object, command: DeviceCommand, *, failure_code: str): ...

    async def mark_reconciling(self, db: object, command: DeviceCommand, *, reason: str): ...


class DispatchEpochRepositoryPort(Protocol):
    async def get_binding_for_command(
        self, db: object, *, line_run_epoch_id: int, device_code: str
    ) -> LineRunEpochDeviceBinding | None: ...


class ObservationRepositoryPort(Protocol):
    async def add_status_observation(self, db: object, observation: DeviceStatusObservation): ...


class DeviceDispatchService:
    """HTTP 在事务外执行；每次写回都用 claim token 重新锁定。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        adapter: EcsAdapter,
        command_repository: DispatchCommandRepositoryPort | None = None,
        epoch_repository: DispatchEpochRepositoryPort | None = None,
        observation_repository: ObservationRepositoryPort | None = None,
        clock: Callable[[], datetime] = timezone.now_for_db,
    ) -> None:
        self._sessions = session_factory
        self._adapter = adapter
        self._commands = command_repository or device_command_repository
        self._epochs = epoch_repository or line_run_epoch_repository
        self._observations = observation_repository or device_evidence_repository
        self._clock = clock

    async def dispatch_one(self, *, now: datetime) -> bool:
        claim_token = new_uuid7()
        async with self._sessions.begin() as db:
            command = await self._commands.claim_next_pending(
                db,
                token=claim_token,
                now=now,
                claim_expires_at=now + timedelta(seconds=30),
            )
            if command is None:
                return False
            command_code = command.command_code
            device_code = command.device_code

        try:
            status = await self._adapter.fetch_status(device_code)
        except Exception:
            # 状态探测发生在命令发送前，失败时可证明请求未离开 WES。
            await self._write_retryable(command_code, claim_token, now=now)
            return True

        # 新鲜度必须以状态响应到达 WES 的时间为基准；领取时间早于 ECS 响应时间，
        # 会把正常状态误判为未来数据。
        observed_at = self._clock()
        async with self._sessions.begin() as db:
            command = await self._commands.get_claimed_for_update(
                db, command_code=command_code, claim_token=claim_token
            )
            if command is None:
                return True
            binding = await self._epochs.get_binding_for_command(
                db,
                line_run_epoch_id=command.line_run_epoch_id,
                device_code=command.device_code,
            )
            if binding is None:
                await self._commands.mark_reconciling(db, command, reason="EPOCH_BINDING_UNAVAILABLE")
                return True
            await self._observations.add_status_observation(
                db,
                _status_observation(command, status, observed_at),
            )
            try:
                self.ensure_admissible(command=command, binding=binding, status=status, observed_at=observed_at)
            except DeviceDispatchAdmissionError as error:
                await self._commands.mark_failed(db, command, failure_code=error.code)
                return True
            submit_snapshot = _submit_snapshot(command)

        submit_result = await self._adapter.submit_command(**submit_snapshot)
        async with self._sessions.begin() as db:
            command = await self._commands.get_claimed_for_update(
                db, command_code=command_code, claim_token=claim_token
            )
            if command is None:
                return True
            if submit_result.disposition is EcsSubmitDisposition.ACKNOWLEDGED:
                await self._commands.mark_acknowledged(db, command, acknowledged_at=now)
            elif submit_result.disposition is EcsSubmitDisposition.RETRYABLE_NOT_ACCEPTED:
                await self._commands.release_retryable(
                    db,
                    command,
                    next_attempt_at=now + timedelta(seconds=5),
                )
            elif submit_result.disposition is EcsSubmitDisposition.CONTRACT_REJECTED:
                await self._commands.mark_failed(db, command, failure_code="ECS_CONTRACT_REJECTED")
            else:
                await self._commands.mark_reconciling(db, command, reason="DELIVERY_UNKNOWN")
        return True

    async def _write_reconciling(self, command_code: str, claim_token: str, reason: str) -> None:
        async with self._sessions.begin() as db:
            command = await self._commands.get_claimed_for_update(
                db, command_code=command_code, claim_token=claim_token
            )
            if command is not None:
                await self._commands.mark_reconciling(db, command, reason=reason)

    async def _write_retryable(self, command_code: str, claim_token: str, *, now: datetime) -> None:
        async with self._sessions.begin() as db:
            command = await self._commands.get_claimed_for_update(
                db, command_code=command_code, claim_token=claim_token
            )
            if command is not None:
                await self._commands.release_retryable(
                    db,
                    command,
                    next_attempt_at=now + timedelta(seconds=5),
                )

    @staticmethod
    def ensure_admissible(
        *,
        command: DeviceCommand,
        binding: LineRunEpochDeviceBinding,
        status: EcsDeviceStatus,
        observed_at: datetime,
    ) -> None:
        if status.device_code != command.device_code or binding.device_code != command.device_code:
            raise DeviceDispatchAdmissionError("DEVICE_IDENTITY_MISMATCH")
        if (
            command.device_binding_id != binding.id
            or command.line_run_epoch_id != binding.line_run_epoch_id
            or status.contract_key != binding.contract_key
            or status.contract_version != binding.contract_version
            or command.contract_key != binding.contract_key
            or command.contract_version != binding.contract_version
        ):
            raise DeviceDispatchAdmissionError("DEVICE_CONTRACT_MISMATCH")
        status_at = datetime.fromtimestamp(status.timestamp / 1000, tz=UTC).replace(tzinfo=None)
        age_ms = (observed_at - status_at).total_seconds() * 1000
        if age_ms < 0 or age_ms > binding.status_max_age_ms:
            raise DeviceDispatchAdmissionError("DEVICE_STATUS_STALE")
        if status.mode is not EcsDeviceMode.AUTO:
            raise DeviceDispatchAdmissionError("DEVICE_MODE_NOT_AUTO")
        if status.status is not EcsDeviceState.IDLE:
            raise DeviceDispatchAdmissionError("DEVICE_NOT_IDLE")
        if status.current_command_code is not None:
            raise DeviceDispatchAdmissionError("DEVICE_HAS_ACTIVE_COMMAND")


def _status_observation(
    command: DeviceCommand,
    status: EcsDeviceStatus,
    received_at: datetime,
) -> DeviceStatusObservation:
    payload = status.model_dump(mode="json")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return DeviceStatusObservation(
        device_code=status.device_code,
        command_code=command.command_code,
        contract_key=status.contract_key,
        contract_version=status.contract_version,
        mode=status.mode,
        status=status.status,
        current_command_code=status.current_command_code,
        device_timestamp=status.timestamp,
        received_at=received_at,
        payload_digest=hashlib.sha256(encoded).hexdigest(),
        raw_payload=payload,
    )


def _submit_snapshot(command: DeviceCommand) -> dict[str, object]:
    created_at = command.created_at.replace(tzinfo=UTC)
    return {
        "device_code": command.device_code,
        "command_code": command.command_code,
        "contract_key": command.contract_key,
        "contract_version": command.contract_version,
        "task_type": command.task_type,
        "timestamp_ms": int(created_at.timestamp() * 1000),
        "params": command.params,
        "trace_id": command.trace_id,
    }


__all__ = ["DeviceDispatchAdmissionError", "DeviceDispatchService"]
