"""DeviceCommand 状态准入、派发与 fenced 写回。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict

from src.app.device.contracts import DeviceEvidenceUpdate, EcsDeviceStatus, EcsSubmitDisposition
from src.app.device.ecs_adapter import EcsAdapter  # noqa: TC001
from src.app.device.evidence_projection import (
    DeviceEvidenceEventPublisherPort,
    build_device_evidence_update,
    publish_device_evidence_update,
)
from src.app.device.models.command import DIAGNOSTIC_REF_TYPES, EVENT_DEBUG_REF_TYPE, DeviceCommand
from src.app.device.models.evidence import DeviceStatusObservation
from src.app.device.repositories.command_repository import device_command_repository
from src.app.device.repositories.status_observation_repository import device_status_observation_repository
from src.app.device.services.device_command_admission import (
    DeviceCommandAdmissionError,
    ensure_runtime_admissible,
    ensure_status_fresh,
)
from src.app.execution.services.inbound_evidence_service import InboundEvidenceService
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest
from src.utils.timezone import timezone

_RETRYABLE_BUSINESS_ADMISSION_CODES = frozenset(
    {
        "DEVICE_OFFLINE",
        "DEVICE_MODE_NOT_AUTO",
        "DEVICE_NOT_IDLE",
        "DEVICE_HAS_ACTIVE_COMMAND",
        "DEVICE_STATUS_STALE",
    }
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.workline.activation import WorkLineDeviceBinding


class DispatchCommandRepositoryPort(Protocol):
    async def claim_next_pending(self, db: object, *, token: str, now: datetime, claim_expires_at: datetime): ...

    async def get_claimed_for_update(self, db: object, *, command_code: str, claim_token: str): ...

    async def release_retryable(self, db: object, command: DeviceCommand, *, next_attempt_at: datetime): ...

    async def mark_acknowledged(self, db: object, command: DeviceCommand, *, acknowledged_at: datetime): ...

    async def mark_failed(self, db: object, command: DeviceCommand, *, failure_code: str): ...

    async def mark_timed_out(self, db: object, command: DeviceCommand): ...

    async def mark_reconciling(self, db: object, command: DeviceCommand, *, reason: str): ...

    async def mark_late_ack_reconciling(self, db: object, command: DeviceCommand, *, acknowledged_at: datetime): ...


class ObservationRepositoryPort(Protocol):
    async def add_status_observation(self, db: object, observation: DeviceStatusObservation): ...


class EndpointAdapterProviderPort(Protocol):
    async def get_adapter(self, endpoint_base_url: str) -> EcsAdapter: ...


@dataclass(frozen=True, slots=True)
class _FrozenDispatchContext:
    device_code: str
    workline_id: int | None
    endpoint_base_url: str
    contract_key: str
    contract_version: str
    status_max_age_ms: int | None


@dataclass(slots=True)
class _PendingEvidenceUpdate:
    update: DeviceEvidenceUpdate | None = None


class DeviceDispatchService:
    """HTTP 在事务外执行；每次写回都用 claim token 重新锁定。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        adapter_provider: EndpointAdapterProviderPort,
        command_repository: DispatchCommandRepositoryPort | None = None,
        observation_repository: ObservationRepositoryPort | None = None,
        evidence_service: InboundEvidenceService | None = None,
        event_publisher: DeviceEvidenceEventPublisherPort | None = None,
        clock: Callable[[], datetime] = timezone.now_for_db,
    ) -> None:
        self._sessions = session_factory
        self._adapter_provider = adapter_provider
        self._commands = command_repository or device_command_repository
        self._observations = observation_repository or device_status_observation_repository
        self._evidence_service = evidence_service or InboundEvidenceService()
        self._event_publisher = event_publisher
        self._clock = clock

    @asynccontextmanager
    async def _observation_transaction(self) -> AsyncIterator[tuple[AsyncSession, _PendingEvidenceUpdate]]:
        pending = _PendingEvidenceUpdate()
        async with self._sessions.begin() as db:
            yield db, pending
        await publish_device_evidence_update(self._event_publisher, pending.update)

    async def dispatch_one(self, *, now: datetime) -> bool:  # noqa: PLR0911, PLR0912
        claim_token = new_uuid7()
        async with self._observation_transaction() as (db, pending_update):
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
                        pending_update.update = await self._mark_not_accepted(
                            db, command, reason_code="EVENT_DEBUG_CONTEXT_INVALID", observed_at=now
                        )
                    else:
                        await self._commands.mark_reconciling(db, command, reason="MANUAL_DEBUG_CONTEXT_INVALID")
                        pending_update.update = await self._record_observation(
                            db,
                            command,
                            observation="NOT_ACCEPTED",
                            reason_code="MANUAL_DEBUG_CONTEXT_INVALID",
                            observed_at=now,
                            received_at=now,
                        )
                    return True
                dispatch_context = _FrozenDispatchContext(
                    device_code=command.device_code,
                    workline_id=None,
                    endpoint_base_url=command.endpoint_base_url,
                    contract_key=command.contract_key,
                    contract_version=command.contract_version,
                    status_max_age_ms=None,
                )
            else:
                if command.endpoint_base_url is None or command.status_max_age_ms is None:
                    await self._commands.mark_reconciling(db, command, reason="COMMAND_CONTRACT_UNAVAILABLE")
                    pending_update.update = await self._record_observation(
                        db,
                        command,
                        observation="NOT_ACCEPTED",
                        reason_code="COMMAND_CONTRACT_UNAVAILABLE",
                        observed_at=now,
                        received_at=now,
                    )
                    return True
                dispatch_context = _FrozenDispatchContext(
                    device_code=command.device_code,
                    workline_id=command.workline_id,
                    endpoint_base_url=command.endpoint_base_url,
                    contract_key=command.contract_key,
                    contract_version=command.contract_version,
                    status_max_age_ms=command.status_max_age_ms,
                )

        try:
            adapter = await self._adapter_provider.get_adapter(dispatch_context.endpoint_base_url)
        except ValueError:
            if event_debug:
                await self._write_failed(command_code, claim_token, "EVENT_DEBUG_ENDPOINT_INVALID", observed_at=now)
            else:
                await self._write_reconciling(
                    command_code, claim_token, "EPOCH_BINDING_ENDPOINT_INVALID", observed_at=now
                )
            return True
        except Exception:
            if event_debug:
                await self._write_failed(command_code, claim_token, "EVENT_DEBUG_ENDPOINT_UNAVAILABLE", observed_at=now)
            else:
                await self._write_retryable(command_code, claim_token, now=now)
            return True

        if diagnostic and self._clock() >= command.deadline_at:
            async with self._observation_transaction() as (db, pending_update):
                command = await self._commands.get_claimed_for_update(
                    db, command_code=command_code, claim_token=claim_token
                )
                if command is not None:
                    pending_update.update = await self._mark_timed_out_not_accepted(db, command, received_at=now)
            return True

        try:
            status = await adapter.fetch_status(device_code)
        except Exception:
            # 状态探测发生在命令发送前，失败时可证明请求未离开 WES。
            if event_debug:
                await self._write_failed(command_code, claim_token, "DEVICE_STATUS_UNAVAILABLE", observed_at=now)
            else:
                await self._write_retryable(command_code, claim_token, now=now)
            return True

        # 新鲜度必须以状态响应到达 WES 的时间为基准；领取时间早于 ECS 响应时间，
        # 会把正常状态误判为未来数据。
        observed_at = self._clock()
        async with self._observation_transaction() as (db, pending_update):
            command = await self._commands.get_claimed_for_update(
                db, command_code=command_code, claim_token=claim_token
            )
            if command is None:
                return True
            if not diagnostic:
                _ = await self._observations.add_status_observation(
                    db,
                    _status_observation(command, status, observed_at),
                )
            if command.deadline_at <= observed_at:
                pending_update.update = await self._mark_timed_out_not_accepted(db, command, received_at=observed_at)
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
                if not diagnostic and error.code in _RETRYABLE_BUSINESS_ADMISSION_CODES:
                    await self._commands.release_retryable(
                        db,
                        command,
                        next_attempt_at=min(observed_at + timedelta(seconds=5), command.deadline_at),
                    )
                else:
                    pending_update.update = await self._mark_not_accepted(
                        db, command, reason_code=error.code, observed_at=observed_at
                    )
                return True
            submit_snapshot = _submit_snapshot(command)

        if self._clock() >= command.deadline_at:
            async with self._observation_transaction() as (db, pending_update):
                command = await self._commands.get_claimed_for_update(
                    db, command_code=command_code, claim_token=claim_token
                )
                if command is not None:
                    pending_update.update = await self._mark_timed_out_not_accepted(
                        db, command, received_at=command.deadline_at
                    )
            return True
        submit_result = await adapter.submit_command(**submit_snapshot, deadline_at=command.deadline_at)
        response_at = self._clock()
        async with self._observation_transaction() as (db, pending_update):
            command = await self._commands.get_claimed_for_update(
                db, command_code=command_code, claim_token=claim_token
            )
            if command is None:
                return True
            if submit_result.disposition is EcsSubmitDisposition.ACKNOWLEDGED:
                if response_at >= command.deadline_at:
                    await self._commands.mark_late_ack_reconciling(db, command, acknowledged_at=response_at)
                    pending_update.update = await self._record_observation(
                        db,
                        command,
                        observation="RESULT_UNKNOWN",
                        reason_code="ACK_AFTER_DEADLINE",
                        observed_at=response_at,
                        received_at=response_at,
                    )
                else:
                    await self._commands.mark_acknowledged(db, command, acknowledged_at=response_at)
            elif submit_result.disposition is EcsSubmitDisposition.RETRYABLE_NOT_ACCEPTED:
                if event_debug:
                    pending_update.update = await self._mark_not_accepted(
                        db,
                        command,
                        reason_code="ECS_RETRYABLE_NOT_ACCEPTED",
                        observed_at=response_at,
                    )
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
                pending_update.update = await self._mark_not_accepted(
                    db,
                    command,
                    reason_code="ECS_CONTRACT_REJECTED",
                    observed_at=response_at,
                )
            else:
                await self._commands.mark_reconciling(db, command, reason="DELIVERY_UNKNOWN")
                pending_update.update = await self._record_observation(
                    db,
                    command,
                    observation="RESULT_UNKNOWN",
                    reason_code="DELIVERY_UNKNOWN",
                    observed_at=response_at,
                    received_at=response_at,
                )
        return True

    async def _write_reconciling(
        self, command_code: str, claim_token: str, reason: str, *, observed_at: datetime
    ) -> None:
        async with self._observation_transaction() as (db, pending_update):
            command = await self._commands.get_claimed_for_update(
                db, command_code=command_code, claim_token=claim_token
            )
            if command is not None:
                await self._commands.mark_reconciling(db, command, reason=reason)
                pending_update.update = await self._record_observation(
                    db,
                    command,
                    observation="NOT_ACCEPTED",
                    reason_code=reason,
                    observed_at=observed_at,
                    received_at=observed_at,
                )

    async def _write_failed(
        self, command_code: str, claim_token: str, failure_code: str, *, observed_at: datetime
    ) -> None:
        async with self._observation_transaction() as (db, pending_update):
            command = await self._commands.get_claimed_for_update(
                db, command_code=command_code, claim_token=claim_token
            )
            if command is not None:
                pending_update.update = await self._mark_not_accepted(
                    db, command, reason_code=failure_code, observed_at=observed_at
                )

    async def _mark_not_accepted(
        self,
        db: AsyncSession,
        command: DeviceCommand,
        *,
        reason_code: str,
        observed_at: datetime,
    ) -> DeviceEvidenceUpdate:
        await self._commands.mark_failed(db, command, failure_code=reason_code)
        return await self._record_observation(
            db,
            command,
            observation="NOT_ACCEPTED",
            reason_code=reason_code,
            observed_at=observed_at,
            received_at=observed_at,
        )

    async def _mark_timed_out_not_accepted(
        self,
        db: AsyncSession,
        command: DeviceCommand,
        *,
        received_at: datetime,
    ) -> DeviceEvidenceUpdate:
        await self._commands.mark_timed_out(db, command)
        return await self._record_observation(
            db,
            command,
            observation="NOT_ACCEPTED",
            reason_code="COMMAND_DEADLINE_EXPIRED",
            observed_at=command.deadline_at,
            received_at=received_at,
        )

    async def _record_observation(
        self,
        db: AsyncSession,
        command: DeviceCommand,
        *,
        observation: Literal["NOT_ACCEPTED", "RESULT_UNKNOWN"],
        reason_code: str,
        observed_at: datetime,
        received_at: datetime,
    ) -> DeviceEvidenceUpdate:
        accepted = await self._evidence_service.record_device_observation(
            db,
            command_code=command.command_code,
            device_code=command.device_code,
            observation=observation,
            reason_code=reason_code,
            observed_at=observed_at,
            received_at=received_at,
            workline_id=command.workline_id,
            material_execution_id=command.material_execution_id,
            contract_key=command.contract_key,
            contract_version=command.contract_version,
        )
        return build_device_evidence_update(accepted.evidence)

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
        binding: WorkLineDeviceBinding | _FrozenDispatchContext,
        status: EcsDeviceStatus,
        observed_at: datetime,
    ) -> None:
        context = (
            binding
            if isinstance(binding, _FrozenDispatchContext)
            else _FrozenDispatchContext(
                device_code=binding.device_code,
                workline_id=binding.workline_id,
                endpoint_base_url=binding.endpoint_base_url,
                contract_key=binding.contract_key,
                contract_version=binding.contract_version,
                status_max_age_ms=binding.status_max_age_ms,
            )
        )
        if status.device.device_code != command.device_code or context.device_code != command.device_code:
            raise DeviceCommandAdmissionError("DEVICE_IDENTITY_MISMATCH")
        if (
            command.workline_id != context.workline_id
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


class _SubmitSnapshot(TypedDict):
    device_code: str
    command_code: str
    task_type: str
    priority: int
    timeout_ms: int
    timestamp: int
    params: dict[str, Any]


def _submit_snapshot(command: DeviceCommand) -> _SubmitSnapshot:
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
