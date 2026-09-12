"""DeviceCommand 应用端口实现。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol, cast

from src.app.device.contracts import (
    DEVICE_INTEGRATION_CONTRACT_KEY,
    DEVICE_INTEGRATION_CONTRACT_VERSION,
    DeviceCommandCallbackSnapshot,
    DeviceCommandHandle,
    DeviceCommandOutcome,
    DeviceCommandRequest,
    EcsCommandResult,
    EcsDeviceEvent,
    EcsDeviceStatus,
    ManualDebugDeviceCommandSnapshot,
    ManualDebugDevicePreflightItem,
    ManualDebugDevicePreflightSnapshot,
)
from src.app.device.endpoint import validate_device_endpoint_base_url
from src.app.device.event_debug_contracts import EventDebugCommandReady
from src.app.device.evidence_projection import (
    DeviceEvidenceEventPublisherPort,
    build_device_evidence_update,
    publish_device_evidence_update,
)
from src.app.device.models.command import (
    DIAGNOSTIC_REF_TYPES,
    EVENT_DEBUG_REF_TYPE,
    MANUAL_DEBUG_REF_TYPE,
    CommandStatus,
    DeviceCommand,
    DeviceCommandRequestData,
)
from src.app.device.repositories.command_repository import device_command_repository
from src.app.device.services.device_command_admission import (
    DeviceCommandAdmissionError,
    ensure_runtime_admissible,
)
from src.app.execution.models.inbound_evidence import (
    InboundEvidence,
    InboundEvidenceApplyStatus,
)
from src.app.execution.repositories.inbound_evidence_repository import inbound_evidence_repository
from src.app.execution.services.inbound_evidence_service import (
    InboundEvidenceService,
)
from src.app.workline.repositories.workline_repository import WorkLineRepository
from src.core.conf import settings
from src.core.transaction_wakeup import defer_wakeup
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.workline.activation import WorkLineDeviceBinding
    from src.core.task_queue_gateway import TaskQueueGateway


class DeviceNotFoundError(LookupError):
    """活动 WorkLine 中未绑定目标设备。"""


class DeviceContractMismatchError(ValueError):
    """请求合同与 WorkLine 冻结合同不一致。"""


class DeviceCommandIdentityConflictError(ValueError):
    """同一插件执行身份被用于不同的不可变命令请求。"""


class DeviceCommandDeadlineError(ValueError):
    """请求截止时间不符合 WorkLine 冻结设备合同。"""


class DeviceCommandNotFoundError(LookupError):
    """调试命令不存在或不是 MANUAL_DEBUG 命令。"""


class CommandRepositoryPort(Protocol):
    async def lock_creation_for_device(self, db: AsyncSession, device_code: str) -> None: ...

    async def lock_manual_debug_identity(self, db: AsyncSession, client_request_id: str) -> None: ...

    async def get_by_execution_ref_for_update(
        self,
        db: AsyncSession,
        *,
        workline_id: int | None,
        device_code: str,
        execution_ref_type: str,
        execution_ref_id: str,
    ) -> DeviceCommand | None: ...

    async def get_manual_debug_by_client_request_id_for_update(
        self,
        db: AsyncSession,
        client_request_id: str,
    ) -> DeviceCommand | None: ...

    async def add(self, db: AsyncSession, command: DeviceCommand) -> DeviceCommand: ...

    async def get_by_command_code(
        self,
        db: AsyncSession,
        command_code: str,
        *,
        for_update: bool = False,
    ) -> DeviceCommand | None: ...

    async def claim_next_reconcilable(self, db: AsyncSession, *, now: datetime) -> DeviceCommand | None: ...


class WorkLineRepositoryPort(Protocol):
    async def get_for_update(self, db: AsyncSession, workline_id: int) -> object | None: ...

    async def get_binding_for_command_creation(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        device_code: str,
    ) -> WorkLineDeviceBinding | None: ...

    async def get_binding_for_dispatch(
        self,
        db: AsyncSession,
        *,
        workline_id: int,
        device_code: str,
    ) -> WorkLineDeviceBinding | None: ...


class EvidenceRepositoryPort(Protocol):
    async def lock_source_identity(self, db: AsyncSession, source_identity: str) -> None: ...

    async def requeue_unassociated_device_results(
        self,
        db: AsyncSession,
        *,
        command_code: str,
        device_code: str,
        workline_id: int | None,
        material_execution_id: int | None,
        contract_key: str,
        contract_version: str,
        source_contract_key: str,
        source_contract_version: str,
    ) -> int: ...

    async def get_by_source_identity_for_update(
        self,
        db: AsyncSession,
        source_identity: str,
    ) -> InboundEvidence | None: ...

    async def get_device_result_for_command(self, db: AsyncSession, command_code: str) -> InboundEvidence | None: ...


class ManualDebugAdapterPort(Protocol):
    async def fetch_statuses(self) -> tuple[EcsDeviceStatus, ...]: ...


class ManualDebugAdapterProviderPort(Protocol):
    async def get_adapter(self, endpoint_base_url: str) -> ManualDebugAdapterPort: ...


_EVENT_DEBUG_COMMAND_TIMEOUT_MS = 30_000


class DeviceCommandService:
    """创建命令并提供与业务无关的 typed outcome。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        command_repository: CommandRepositoryPort | None = None,
        workline_repository: WorkLineRepositoryPort | None = None,
        evidence_repository: EvidenceRepositoryPort | None = None,
        evidence_service: InboundEvidenceService | None = None,
        adapter_provider: ManualDebugAdapterProviderPort | None = None,
        clock: Callable[[], datetime] = timezone.now_for_db,
        task_queue_gateway: TaskQueueGateway | None = None,
        event_publisher: DeviceEvidenceEventPublisherPort | None = None,
    ) -> None:
        self._sessions = session_factory
        self._task_queue = task_queue_gateway
        self._event_publisher = event_publisher
        self._commands = command_repository or device_command_repository
        self._worklines = workline_repository or WorkLineRepository()
        self._evidences = evidence_repository or inbound_evidence_repository
        self._evidence_service = evidence_service or InboundEvidenceService()
        self._adapter_provider = adapter_provider
        self._clock = clock

    async def preflight_manual_debug(self, endpoint_base_url: str) -> ManualDebugDevicePreflightSnapshot:
        """枚举 ECS 设备，并返回不含业务 binding 的运行态准入结果。"""

        endpoint = validate_device_endpoint_base_url(endpoint_base_url)
        adapter = await self._manual_debug_adapter(endpoint)
        statuses = await adapter.fetch_statuses()
        devices: list[ManualDebugDevicePreflightItem] = []
        for status in statuses:
            rejection_code = None
            try:
                ensure_runtime_admissible(
                    status=status,
                    expected_device_code=status.device.device_code,
                )
            except DeviceCommandAdmissionError as error:
                rejection_code = error.code
            devices.append(ManualDebugDevicePreflightItem(status=status, rejection_code=rejection_code))
        return ManualDebugDevicePreflightSnapshot(endpoint_base_url=endpoint, devices=tuple(devices))

    async def create_command(self, request: DeviceCommandRequest) -> DeviceCommandHandle:
        async with self._sessions.begin() as db:
            return await self.create_command_in_session(db, request)

    async def create_command_in_session(self, db: AsyncSession, request: DeviceCommandRequest) -> DeviceCommandHandle:
        """在调用方事务中创建命令；只持久化，并登记事务提交后的派发唤醒。"""

        validated = DeviceCommandRequestData.model_validate(asdict(request))
        if validated.deadline_at.tzinfo is not None:
            raise DeviceCommandDeadlineError("deadline_at 必须是数据库合同要求的 naive UTC")
        _ = await self._worklines.get_for_update(db, cast("int", validated.workline_id))
        await self._commands.lock_creation_for_device(db, validated.device_code)
        payload_digest = _command_payload_digest(validated)
        same_identity = await self._commands.get_by_execution_ref_for_update(
            db,
            workline_id=cast("int", validated.workline_id),
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
        binding = await self._worklines.get_binding_for_command_creation(
            db,
            workline_id=cast("int", validated.workline_id),
            device_code=validated.device_code,
        )
        if binding is None:
            raise DeviceNotFoundError(validated.device_code)
        if binding.contract_key != validated.contract_key or binding.contract_version != validated.contract_version:
            raise DeviceContractMismatchError(validated.device_code)
        now = self._clock()
        if validated.deadline_at <= now or validated.deadline_at > now + timedelta(
            milliseconds=binding.command_timeout_ms
        ):
            raise DeviceCommandDeadlineError("deadline_at 超出冻结 binding 的 command_timeout_ms")
        command = DeviceCommand(
            command_code=new_uuid7(),
            device_code=validated.device_code,
            workline_id=cast("int", validated.workline_id),
            endpoint_base_url=binding.endpoint_base_url,
            command_timeout_ms=binding.command_timeout_ms,
            status_max_age_ms=binding.status_max_age_ms,
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
        persisted = await self._persist_command(db, command)
        if self._task_queue is not None and persisted.status == CommandStatus.PENDING:
            defer_wakeup(db, self._task_queue.enqueue_device_commands)
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
        execution_reason: str,
        created_by: int,
    ) -> DeviceCommandHandle:
        """创建不依赖 WorkLine/WorkLine 的供应商联调命令。"""

        endpoint = validate_device_endpoint_base_url(endpoint_base_url)
        now = self._clock()
        try:
            deadline_at = now + timedelta(milliseconds=command_timeout_ms)
        except (OverflowError, TypeError) as error:
            raise DeviceCommandDeadlineError("command_timeout_ms 无法形成有效截止时间") from error
        validated = DeviceCommandRequestData.model_validate(
            {
                "device_code": device_code,
                "workline_id": None,
                "execution_ref_type": MANUAL_DEBUG_REF_TYPE,
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
                "execution_reason": execution_reason,
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
                if not _same_manual_debug_identity(
                    same_identity,
                    payload_digest=payload_digest,
                    execution_reason=validated.execution_reason,
                    created_by=created_by,
                ):
                    raise DeviceCommandIdentityConflictError(validated.execution_ref_id)
                return DeviceCommandHandle(
                    command_code=same_identity.command_code,
                    status=CommandStatus(same_identity.status),
                )
            command = DeviceCommand(
                command_code=new_uuid7(),
                device_code=validated.device_code,
                workline_id=None,
                execution_ref_type=MANUAL_DEBUG_REF_TYPE,
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
                execution_reason=validated.execution_reason,
                next_attempt_at=now,
                created_at=now,
                created_by=created_by,
            )
            persisted = await self._persist_command(db, command)
            if self._task_queue is not None and persisted.status == CommandStatus.PENDING:
                defer_wakeup(db, self._task_queue.enqueue_device_commands)
        return DeviceCommandHandle(command_code=persisted.command_code, status=CommandStatus(persisted.status))

    async def create_event_debug_command_in_session(
        self,
        db: AsyncSession,
        *,
        evidence: InboundEvidence,
    ) -> EventDebugCommandReady:
        """从已持久化的调试 EVENT 创建不进入业务 Decision 的可靠命令。"""

        event = EcsDeviceEvent.model_validate(evidence.normalized_payload)
        if not event.is_debug:
            raise ValueError("EVENT_DEBUG evidence 缺少调试标记")
        now = self._clock()
        validated = DeviceCommandRequestData.model_validate(
            {
                "device_code": event.device_code,
                "workline_id": None,
                "execution_ref_type": EVENT_DEBUG_REF_TYPE,
                "execution_ref_id": evidence.source_identity,
                "material_execution_id": None,
                "contract_key": event.contract_key,
                "contract_version": event.contract_version,
                "task_type": "MOVE_FORWARD",
                "params": event.data,
                "deadline_at": now + timedelta(milliseconds=_EVENT_DEBUG_COMMAND_TIMEOUT_MS),
                "trace_id": event.trace_id,
                "endpoint_base_url": validate_device_endpoint_base_url(settings.DEVICE_EVENT_DEBUG_ENDPOINT_BASE_URL),
                "command_timeout_ms": _EVENT_DEBUG_COMMAND_TIMEOUT_MS,
                "execution_reason": f"ECS_EVENT_DEBUG:{evidence.source_identity}",
            }
        )
        payload_digest = _command_payload_digest(validated)
        await self._commands.lock_creation_for_device(db, validated.device_code)
        same_identity = await self._commands.get_by_execution_ref_for_update(
            db,
            workline_id=None,
            device_code=validated.device_code,
            execution_ref_type=EVENT_DEBUG_REF_TYPE,
            execution_ref_id=validated.execution_ref_id,
        )
        if same_identity is not None:
            if same_identity.payload_digest != payload_digest:
                raise DeviceCommandIdentityConflictError(validated.execution_ref_id)
            return EventDebugCommandReady(
                command_code=same_identity.command_code,
                status=CommandStatus(same_identity.status),
                created=False,
            )
        command = DeviceCommand(
            command_code=new_uuid7(),
            device_code=validated.device_code,
            workline_id=None,
            execution_ref_type=EVENT_DEBUG_REF_TYPE,
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
            execution_reason=validated.execution_reason,
            status=CommandStatus.PENDING,
            next_attempt_at=now,
            created_at=now,
            created_by=None,
        )
        persisted = await self._persist_command(db, command)
        return EventDebugCommandReady(
            command_code=persisted.command_code,
            status=CommandStatus(persisted.status),
            created=True,
        )

    async def _persist_command(self, db: AsyncSession, command: DeviceCommand) -> DeviceCommand:
        # 与回调共享精确身份事务锁：无行时也保证后提交的一侧看见另一侧。
        await self._evidences.lock_source_identity(db, f"device-result:{command.command_code}")
        # 与 Evidence worker 一致先锁/登记 Evidence，再插入 Command，避免 E→C / C→E 环。
        registered = await self._evidences.requeue_unassociated_device_results(
            db,
            command_code=command.command_code,
            device_code=command.device_code,
            workline_id=command.workline_id,
            material_execution_id=command.material_execution_id,
            contract_key=command.contract_key,
            contract_version=command.contract_version,
            source_contract_key=DEVICE_INTEGRATION_CONTRACT_KEY,
            source_contract_version=DEVICE_INTEGRATION_CONTRACT_VERSION,
        )
        persisted = await self._commands.add(db, command)
        if registered:
            # 未提交命令不能借晚关联伪造执行；worker 留存关联诊断，不再下发或推进业务。
            command.transition_to(CommandStatus.RECONCILING)
            command.reconciliation_reason = "RESULT_BEFORE_DISPATCH"
            if self._task_queue is not None:
                defer_wakeup(db, self._task_queue.enqueue_device_evidence)
        return persisted

    async def get_command_snapshot(self, command_code: str) -> ManualDebugDeviceCommandSnapshot:
        async with self._sessions.begin() as db:
            command = await self._commands.get_by_command_code(db, command_code)
            if command is None or command.execution_ref_type != MANUAL_DEBUG_REF_TYPE:
                raise DeviceCommandNotFoundError(command_code)
            evidence = await self._evidences.get_device_result_for_command(db, command_code)
        if command.endpoint_base_url is None or command.command_timeout_ms is None:
            raise RuntimeError("MANUAL_DEBUG DeviceCommand 缺少冻结派发上下文")
        if command.execution_reason is None or command.created_by is None:
            raise RuntimeError("MANUAL_DEBUG DeviceCommand 缺少审计上下文")
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
            execution_reason=command.execution_reason,
            created_by=command.created_by,
            callback=callback,
        )

    async def _manual_debug_adapter(self, endpoint_base_url: str) -> ManualDebugAdapterPort:
        if self._adapter_provider is None:
            raise RuntimeError("Device Endpoint provider 不可用")
        return await self._adapter_provider.get_adapter(endpoint_base_url)

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
                observation = "NOT_ACCEPTED"
                reason_code = "COMMAND_DEADLINE_EXPIRED"
                observed_at = command.deadline_at
                command.transition_to(CommandStatus.TIMED_OUT)
            elif status is CommandStatus.DISPATCHING:
                observation = "RESULT_UNKNOWN"
                reason_code = "DISPATCH_LEASE_EXPIRED"
                observed_at = command.claim_expires_at or command.deadline_at
                command.reconciliation_reason = "DISPATCH_LEASE_EXPIRED"
                command.transition_to(CommandStatus.RECONCILING)
            elif status is CommandStatus.ACKNOWLEDGED:
                observation = "RESULT_UNKNOWN"
                reason_code = "ACK_DEADLINE_EXPIRED"
                observed_at = command.deadline_at
                command.reconciliation_reason = "ACK_DEADLINE_EXPIRED"
                command.transition_to(CommandStatus.RECONCILING)
            else:
                raise RuntimeError(f"不可对账的 DeviceCommand 状态: {status.value}")
            accepted = await self._evidence_service.record_device_observation(
                db,
                command_code=command.command_code,
                device_code=command.device_code,
                observation=observation,
                reason_code=reason_code,
                observed_at=observed_at,
                received_at=now,
                workline_id=command.workline_id,
                material_execution_id=command.material_execution_id,
                contract_key=command.contract_key,
                contract_version=command.contract_version,
            )
            update = build_device_evidence_update(accepted.evidence)
        await publish_device_evidence_update(self._event_publisher, update)
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
    if request.execution_ref_type in DIAGNOSTIC_REF_TYPES:
        payload.update(
            {
                "endpoint_base_url": request.endpoint_base_url,
                "command_timeout_ms": request.command_timeout_ms,
            }
        )
    return canonical_json_digest(payload)


def _same_manual_debug_identity(
    command: DeviceCommand,
    *,
    payload_digest: str,
    execution_reason: str | None,
    created_by: int,
) -> bool:
    return (
        command.payload_digest == payload_digest
        and command.execution_reason == execution_reason
        and command.created_by == created_by
    )


__all__ = [
    "DeviceCommandDeadlineError",
    "DeviceCommandIdentityConflictError",
    "DeviceCommandNotFoundError",
    "DeviceCommandService",
    "DeviceContractMismatchError",
    "DeviceNotFoundError",
]
