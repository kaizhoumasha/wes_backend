"""DeviceCommand 状态准入、派发与 fenced 写回。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from src.app.device.contracts import EcsDeviceStatus, EcsSubmitDisposition
from src.app.device.ecs_adapter import EcsAdapter  # noqa: TC001
from src.app.device.models.command import DIAGNOSTIC_REF_TYPES, EVENT_DEBUG_REF_TYPE, DeviceCommand
from src.app.device.models.evidence import DeviceStatusObservation
from src.app.device.repositories.command_repository import device_command_repository
from src.app.device.repositories.status_observation_repository import device_status_observation_repository
from src.app.device.services.device_command_admission import (
    DeviceCommandAdmissionError,
    ensure_runtime_admissible,
    ensure_status_fresh,
)
from src.app.workline.models.line_run_epoch import LineRunEpochDeviceBinding  # noqa: TC001
from src.app.workline.repositories.line_run_epoch_repository import line_run_epoch_repository
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class DispatchCommandRepositoryPort(Protocol):
    async def claim_next_pending(self, db: object, *, token: str, now: datetime, claim_expires_at: datetime): ...

    async def get_claimed_for_update(self, db: object, *, command_code: str, claim_token: str): ...

    async def release_retryable(self, db: object, command: DeviceCommand, *, next_attempt_at: datetime): ...

    async def mark_acknowledged(self, db: object, command: DeviceCommand, *, acknowledged_at: datetime): ...

    async def mark_failed(self, db: object, command: DeviceCommand, *, failure_code: str): ...

    async def mark_timed_out(self, db: object, command: DeviceCommand): ...

    async def mark_reconciling(self, db: object, command: DeviceCommand, *, reason: str): ...

    async def mark_late_ack_reconciling(self, db: object, command: DeviceCommand, *, acknowledged_at: datetime): ...


class DispatchEpochRepositoryPort(Protocol):
    async def get_binding_for_dispatch(
        self, db: object, *, line_run_epoch_id: int, device_code: str
    ) -> LineRunEpochDeviceBinding | None: ...


class ObservationRepositoryPort(Protocol):
    async def add_status_observation(self, db: object, observation: DeviceStatusObservation): ...


class EndpointAdapterProviderPort(Protocol):
    async def get_adapter(self, endpoint_base_url: str) -> EcsAdapter: ...


@dataclass(frozen=True, slots=True)
class _FrozenDispatchContext:
    device_code: str
    line_run_epoch_id: int | None
    device_binding_id: int | None
    endpoint_base_url: str
    contract_key: str
    contract_version: str
    status_max_age_ms: int | None


class DeviceDispatchService:
    """HTTP 在事务外执行；每次写回都用 claim token 重新锁定。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        adapter_provider: EndpointAdapterProviderPort,
        command_repository: DispatchCommandRepositoryPort | None = None,
        epoch_repository: DispatchEpochRepositoryPort | None = None,
        observation_repository: ObservationRepositoryPort | None = None,
        clock: Callable[[], datetime] = timezone.now_for_db,
    ) -> None:
        self._sessions = session_factory
        self._adapter_provider = adapter_provider
        self._commands = command_repository or device_command_repository
        self._epochs = epoch_repository or line_run_epoch_repository
        self._observations = observation_repository or device_status_observation_repository
        self._clock = clock

    async def dispatch_one(self, *, now: datetime) -> bool:  # noqa: PLR0911, PLR0912
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
            diagnostic = command.execution_ref_type in DIAGNOSTIC_REF_TYPES
            event_debug = command.execution_ref_type == EVENT_DEBUG_REF_TYPE
            if diagnostic:
                if command.endpoint_base_url is None or command.command_timeout_ms is None:
                    if event_debug:
                        await self._commands.mark_failed(db, command, failure_code="EVENT_DEBUG_CONTEXT_INVALID")
                    else:
                        await self._commands.mark_reconciling(db, command, reason="MANUAL_DEBUG_CONTEXT_INVALID")
                    return True
                dispatch_context = _FrozenDispatchContext(
                    device_code=command.device_code,
                    line_run_epoch_id=None,
                    device_binding_id=None,
                    endpoint_base_url=command.endpoint_base_url,
                    contract_key=command.contract_key,
                    contract_version=command.contract_version,
                    status_max_age_ms=None,
                )
            else:
                binding = await self._epochs.get_binding_for_dispatch(
                    db,
                    line_run_epoch_id=command.line_run_epoch_id,
                    device_code=device_code,
                )
                if binding is None:
                    await self._commands.mark_reconciling(db, command, reason="EPOCH_BINDING_UNAVAILABLE")
                    return True
                dispatch_context = _FrozenDispatchContext(
                    device_code=binding.device_code,
                    line_run_epoch_id=binding.line_run_epoch_id,
                    device_binding_id=binding.id,
                    endpoint_base_url=binding.endpoint_base_url,
                    contract_key=binding.contract_key,
                    contract_version=binding.contract_version,
                    status_max_age_ms=binding.status_max_age_ms,
                )

        try:
            adapter = await self._adapter_provider.get_adapter(dispatch_context.endpoint_base_url)
        except ValueError:
            if event_debug:
                await self._write_failed(command_code, claim_token, "EVENT_DEBUG_ENDPOINT_INVALID")
            else:
                await self._write_reconciling(command_code, claim_token, "EPOCH_BINDING_ENDPOINT_INVALID")
            return True
        except Exception:
            if event_debug:
                await self._write_failed(command_code, claim_token, "EVENT_DEBUG_ENDPOINT_UNAVAILABLE")
            else:
                await self._write_retryable(command_code, claim_token, now=now)
            return True

        if diagnostic and self._clock() >= command.deadline_at:
            async with self._sessions.begin() as db:
                command = await self._commands.get_claimed_for_update(
                    db, command_code=command_code, claim_token=claim_token
                )
                if command is not None:
                    await self._commands.mark_timed_out(db, command)
            return True

        try:
            status = await adapter.fetch_status(device_code)
        except Exception:
            # 状态探测发生在命令发送前，失败时可证明请求未离开 WES。
            if event_debug:
                await self._write_failed(command_code, claim_token, "DEVICE_STATUS_UNAVAILABLE")
            else:
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
            if not diagnostic:
                await self._observations.add_status_observation(
                    db,
                    _status_observation(command, status, observed_at),
                )
            if command.deadline_at <= observed_at:
                await self._commands.mark_timed_out(db, command)
                return True
            try:
                if diagnostic:
                    ensure_runtime_admissible(
                        status=status,
                        expected_device_code=command.device_code,
                        task_type=command.task_type,
                    )
                else:
                    self.ensure_admissible(
                        command=command, binding=dispatch_context, status=status, observed_at=observed_at
                    )
            except DeviceCommandAdmissionError as error:
                await self._commands.mark_failed(db, command, failure_code=error.code)
                return True
            submit_snapshot = _submit_snapshot(command)

        if self._clock() >= command.deadline_at:
            async with self._sessions.begin() as db:
                command = await self._commands.get_claimed_for_update(
                    db, command_code=command_code, claim_token=claim_token
                )
                if command is not None:
                    await self._commands.mark_timed_out(db, command)
            return True
        submit_result = await adapter.submit_command(**submit_snapshot, deadline_at=command.deadline_at)
        response_at = self._clock()
        async with self._sessions.begin() as db:
            command = await self._commands.get_claimed_for_update(
                db, command_code=command_code, claim_token=claim_token
            )
            if command is None:
                return True
            if submit_result.disposition is EcsSubmitDisposition.ACKNOWLEDGED:
                if response_at >= command.deadline_at:
                    await self._commands.mark_late_ack_reconciling(db, command, acknowledged_at=response_at)
                else:
                    await self._commands.mark_acknowledged(db, command, acknowledged_at=response_at)
            elif submit_result.disposition is EcsSubmitDisposition.RETRYABLE_NOT_ACCEPTED:
                if event_debug:
                    await self._commands.mark_failed(db, command, failure_code="ECS_RETRYABLE_NOT_ACCEPTED")
                else:
                    retry_after_seconds = submit_result.retry_after_seconds
                    retry_base = self._clock()
                    try:
                        candidate = retry_base + timedelta(
                            seconds=5 if retry_after_seconds is None else retry_after_seconds
                        )
                    except (OverflowError, ValueError):
                        candidate = command.deadline_at
                    await self._commands.release_retryable(
                        db,
                        command,
                        next_attempt_at=min(candidate, command.deadline_at),
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

    async def _write_failed(self, command_code: str, claim_token: str, failure_code: str) -> None:
        async with self._sessions.begin() as db:
            command = await self._commands.get_claimed_for_update(
                db, command_code=command_code, claim_token=claim_token
            )
            if command is not None:
                await self._commands.mark_failed(db, command, failure_code=failure_code)

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
        binding: LineRunEpochDeviceBinding | _FrozenDispatchContext,
        status: EcsDeviceStatus,
        observed_at: datetime,
    ) -> None:
        context = (
            binding
            if isinstance(binding, _FrozenDispatchContext)
            else _FrozenDispatchContext(
                device_code=binding.device_code,
                line_run_epoch_id=binding.line_run_epoch_id,
                device_binding_id=binding.id,
                endpoint_base_url=binding.endpoint_base_url,
                contract_key=binding.contract_key,
                contract_version=binding.contract_version,
                status_max_age_ms=binding.status_max_age_ms,
            )
        )
        if status.device.device_code != command.device_code or context.device_code != command.device_code:
            raise DeviceCommandAdmissionError("DEVICE_IDENTITY_MISMATCH")
        if (
            command.device_binding_id != context.device_binding_id
            or command.line_run_epoch_id != context.line_run_epoch_id
            or command.contract_key != context.contract_key
            or command.contract_version != context.contract_version
        ):
            raise DeviceCommandAdmissionError("DEVICE_CONTRACT_MISMATCH")
        ensure_status_fresh(
            status=status,
            observed_at=observed_at,
            status_max_age_ms=context.status_max_age_ms,
        )
        ensure_runtime_admissible(status=status, expected_device_code=command.device_code)


def _status_observation(
    command: DeviceCommand,
    status: EcsDeviceStatus,
    received_at: datetime,
) -> DeviceStatusObservation:
    payload = status.model_dump(mode="json")
    return DeviceStatusObservation(
        device_code=status.device.device_code,
        command_code=command.command_code,
        contract_key=command.contract_key,
        contract_version=command.contract_version,
        mode=status.state.mode,
        status=status.state.status,
        current_command_code=status.state.current_command_code,
        device_timestamp=status.state.updated_at,
        received_at=received_at,
        payload_digest=canonical_json_digest(payload),
        raw_payload=payload,
    )


def _submit_snapshot(command: DeviceCommand) -> dict[str, object]:
    created_at = command.created_at.replace(tzinfo=UTC)
    timeout_ms = command.command_timeout_ms
    if timeout_ms is None:
        timeout_ms = max(1, int((command.deadline_at - command.created_at).total_seconds() * 1000))
    return {
        "device_code": command.device_code,
        "command_code": command.command_code,
        "task_type": command.task_type,
        "priority": 1,
        "timeout_ms": timeout_ms,
        "timestamp": int(created_at.timestamp() * 1000),
        "params": command.params,
    }


__all__ = ["DeviceDispatchService"]
