"""DeviceCommand 应用端口实现。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from src.app.device.contracts import (
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
from src.app.device.event_block_contracts import EventDebugCommandBlocked, EventDebugCommandReady
from src.app.device.models.command import (
    DIAGNOSTIC_REF_TYPES,
    EVENT_DEBUG_REF_TYPE,
    MANUAL_DEBUG_REF_TYPE,
    CommandStatus,
    DeviceCommand,
    DeviceCommandRequestData,
)
from src.app.device.models.event_command_block import DeviceEventCommandBlockStatus
from src.app.device.repositories.command_repository import device_command_repository
from src.app.device.repositories.event_command_block_repository import device_event_command_block_repository
from src.app.device.services.device_command_admission import (
    DeviceCommandAdmissionError,
    ensure_runtime_admissible,
    ensure_status_fresh,
)
from src.app.execution.models.inbound_evidence import InboundEvidence, InboundEvidenceApplyStatus
from src.app.execution.repositories.inbound_evidence_repository import inbound_evidence_repository
from src.app.sys.models.audit_log import OperaStatus
from src.app.sys.services.audit_service import audit_log_service
from src.app.workline.models.line_run_epoch import LineRunEpochDeviceBinding  # noqa: TC001
from src.app.workline.repositories.line_run_epoch_repository import (
    line_run_epoch_repository,
)
from src.core.uuid7 import new_uuid7
from src.utils.canonical_json import canonical_json_digest
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


class DeviceCommandManualReconciliationNotFoundError(LookupError):
    """人工闭合请求没有命中指定 EVENT 的因果 blocker。"""


class DeviceCommandManualReconciliationConflictError(RuntimeError):
    """人工闭合的冻结因果或实时安全证明不再成立。"""


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

    async def get_binding_for_dispatch(
        self,
        db: object,
        *,
        line_run_epoch_id: int,
        device_code: str,
    ) -> LineRunEpochDeviceBinding | None: ...


class EvidenceRepositoryPort(Protocol):
    async def get_by_source_identity_for_update(
        self,
        db: object,
        source_identity: str,
    ) -> InboundEvidence | None: ...

    async def get_device_result_for_command(self, db: object, command_code: str) -> InboundEvidence | None: ...


class EventCommandBlockRepositoryPort(Protocol):
    async def get_by_id_for_update(self, db: object, *, block_id: int, evidence_id: int): ...

    async def get_latest_for_evidence(self, db: object, *, evidence_id: int): ...


class AuditServicePort(Protocol):
    async def create_audit_log(self, db: object, **values: object) -> object: ...


class ManualDebugAdapterPort(Protocol):
    async def fetch_statuses(self) -> tuple[EcsDeviceStatus, ...]: ...

    async def fetch_status(self, device_code: str) -> EcsDeviceStatus: ...


class ManualDebugAdapterProviderPort(Protocol):
    async def get_adapter(self, endpoint_base_url: str) -> ManualDebugAdapterPort: ...


_EVENT_DEBUG_COMMAND_TIMEOUT_MS = 30_000
_EVENT_DEBUG_ENDPOINT = "http://10.24.209.26:8080"
_MANUAL_RECONCILIATION_FAILURE_CODE = "MANUAL_RECONCILIATION_DEVICE_IDLE"


@dataclass(frozen=True, slots=True)
class _ManualReconciliationTarget:
    evidence_id: int
    block_id: int
    command_id: int
    command_code: str
    command_version: int
    device_code: str
    endpoint_base_url: str
    status_max_age_ms: int


class DeviceCommandService:
    """创建命令并提供与业务无关的 typed outcome。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        command_repository: CommandRepositoryPort | None = None,
        epoch_repository: EpochRepositoryPort | None = None,
        evidence_repository: EvidenceRepositoryPort | None = None,
        adapter_provider: ManualDebugAdapterProviderPort | None = None,
        event_command_block_repository: EventCommandBlockRepositoryPort | None = None,
        audit_service: AuditServicePort | None = None,
        clock: Callable[[], datetime] = timezone.now_for_db,
    ) -> None:
        self._sessions = session_factory
        self._commands = command_repository or device_command_repository
        self._epochs = epoch_repository or line_run_epoch_repository
        self._evidences = evidence_repository or inbound_evidence_repository
        self._adapter_provider = adapter_provider
        self._event_command_blocks = event_command_block_repository or device_event_command_block_repository
        self._audit = audit_service or audit_log_service
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
        execution_reason: str,
        created_by: int,
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

        adapter = await self._manual_debug_adapter(endpoint)
        status = await adapter.fetch_status(validated.device_code)
        ensure_runtime_admissible(
            status=status,
            expected_device_code=validated.device_code,
            task_type=validated.task_type,
        )

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
            existing = await self._commands.get_unclosed_for_device_for_update(db, validated.device_code)
            if existing is not None:
                raise DeviceCommandCapacityError(validated.device_code)
            command = DeviceCommand(
                command_code=new_uuid7(),
                device_code=validated.device_code,
                line_run_epoch_id=None,
                device_binding_id=None,
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
            persisted = await self._commands.add(db, command)
        return DeviceCommandHandle(command_code=persisted.command_code, status=CommandStatus(persisted.status))

    async def create_event_debug_command_in_session(
        self,
        db: object,
        *,
        evidence: InboundEvidence,
    ) -> EventDebugCommandReady | EventDebugCommandBlocked:
        """从已持久化的调试 EVENT 创建不进入业务 Decision 的可靠命令。"""

        event = EcsDeviceEvent.model_validate(evidence.normalized_payload)
        if not event.is_debug:
            raise ValueError("EVENT_DEBUG evidence 缺少调试标记")
        now = self._clock()
        validated = DeviceCommandRequestData.model_validate(
            {
                "device_code": event.device_code,
                "line_run_epoch_id": None,
                "execution_ref_type": EVENT_DEBUG_REF_TYPE,
                "execution_ref_id": evidence.source_identity,
                "material_execution_id": None,
                "contract_key": event.contract_key,
                "contract_version": event.contract_version,
                "task_type": "MOVE_FORWARD",
                "params": event.data,
                "deadline_at": now + timedelta(milliseconds=_EVENT_DEBUG_COMMAND_TIMEOUT_MS),
                "trace_id": event.trace_id,
                "endpoint_base_url": _EVENT_DEBUG_ENDPOINT,
                "command_timeout_ms": _EVENT_DEBUG_COMMAND_TIMEOUT_MS,
                "execution_reason": f"ECS_EVENT_DEBUG:{evidence.source_identity}",
            }
        )
        payload_digest = _command_payload_digest(validated)
        await self._commands.lock_creation_for_device(db, validated.device_code)
        same_identity = await self._commands.get_by_execution_ref_for_update(
            db,
            line_run_epoch_id=None,
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
        existing = await self._commands.get_unclosed_for_device_for_update(db, validated.device_code)
        if existing is not None:
            if existing.id is None:
                raise RuntimeError("未闭合 DeviceCommand 缺少持久化 ID")
            return EventDebugCommandBlocked(
                blocking_command_id=existing.id,
                blocking_command_code=existing.command_code,
                blocking_command_status=CommandStatus(existing.status),
                blocking_reconciliation_reason=existing.reconciliation_reason,
            )
        command = DeviceCommand(
            command_code=new_uuid7(),
            device_code=validated.device_code,
            line_run_epoch_id=None,
            device_binding_id=None,
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
        persisted = await self._commands.add(db, command)
        return EventDebugCommandReady(
            command_code=persisted.command_code,
            status=CommandStatus(persisted.status),
            created=True,
        )

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

    async def reconcile_delivery_unknown_as_device_idle(
        self,
        *,
        source_event_id: str,
        block_id: int,
        reason: str,
        actor_id: int,
    ) -> DeviceCommandHandle:
        """以实时空闲证明人工闭合指定 blocker 对应的 DELIVERY_UNKNOWN 命令。"""

        canonical_reason = reason.strip()
        if not canonical_reason or len(canonical_reason) > 500:
            raise ValueError("reason 必须是 1..500 个字符的非空文本")
        target = await self._freeze_manual_reconciliation_target(
            source_event_id=source_event_id,
            block_id=block_id,
        )

        adapter = await self._manual_debug_adapter(target.endpoint_base_url)
        status = await adapter.fetch_status(target.device_code)
        ensure_runtime_admissible(status=status, expected_device_code=target.device_code)
        observed_at = self._clock()
        ensure_status_fresh(
            status=status,
            observed_at=observed_at,
            status_max_age_ms=target.status_max_age_ms,
        )

        async with self._sessions.begin() as db:
            evidence = await self._evidences.get_by_source_identity_for_update(db, source_event_id)
            if evidence is None or evidence.id != target.evidence_id:
                raise DeviceCommandManualReconciliationConflictError("EVENT evidence 已漂移")
            block = await self._event_command_blocks.get_by_id_for_update(
                db,
                block_id=target.block_id,
                evidence_id=target.evidence_id,
            )
            latest = await self._event_command_blocks.get_latest_for_evidence(db, evidence_id=target.evidence_id)
            if (
                block is None
                or latest is None
                or latest.id != target.block_id
                or DeviceEventCommandBlockStatus(block.status) is not DeviceEventCommandBlockStatus.BLOCKED
            ):
                raise DeviceCommandManualReconciliationConflictError("目标 blocker 已漂移")
            await self._commands.lock_creation_for_device(db, target.device_code)
            command = await self._commands.get_by_command_code(db, target.command_code, for_update=True)
            if not _matches_manual_reconciliation_target(command, target):
                raise DeviceCommandManualReconciliationConflictError("阻塞命令已漂移")
            if await self._evidences.get_device_result_for_command(db, target.command_code) is not None:
                raise DeviceCommandManualReconciliationConflictError("阻塞命令已有 DEVICE_RESULT")

            command.failure_code = _MANUAL_RECONCILIATION_FAILURE_CODE
            command.transition_to(CommandStatus.FAILED)
            state = status.state
            await self._audit.create_audit_log(
                db,
                method="POST",
                title="人工闭合 DELIVERY_UNKNOWN DeviceCommand",
                path=(f"/api/v1/device/evidences/{source_event_id}/blockers/{target.block_id}/reconcile-device-idle"),
                args={
                    "model": "DeviceCommand",
                    "operation": "manual_reconcile_device_idle",
                    "record_id": target.command_id,
                    "source_event_id": source_event_id,
                    "block_id": target.block_id,
                    "command_code": target.command_code,
                    "device_code": target.device_code,
                    "previous_status": CommandStatus.RECONCILING.value,
                    "reconciliation_reason": "DELIVERY_UNKNOWN",
                    "is_online": state.is_online,
                    "mode": state.mode.value,
                    "status": state.status.value,
                    "current_command_code": state.current_command_code,
                    "updated_at": state.updated_at,
                    "status_max_age_ms": target.status_max_age_ms,
                    "actor_id": actor_id,
                    "reason": canonical_reason,
                },
                status=OperaStatus.SUCCESS,
                code="200",
                msg="人工确认设备空闲并闭合 DELIVERY_UNKNOWN 命令",
            )
        return DeviceCommandHandle(command_code=target.command_code, status=CommandStatus.FAILED)

    async def _freeze_manual_reconciliation_target(
        self,
        *,
        source_event_id: str,
        block_id: int,
    ) -> _ManualReconciliationTarget:
        async with self._sessions.begin() as db:
            evidence = await self._evidences.get_by_source_identity_for_update(db, source_event_id)
            if evidence is None or evidence.id is None:
                raise DeviceCommandManualReconciliationNotFoundError(source_event_id)
            block = await self._event_command_blocks.get_by_id_for_update(
                db,
                block_id=block_id,
                evidence_id=evidence.id,
            )
            if block is None:
                raise DeviceCommandManualReconciliationNotFoundError(f"{source_event_id}:{block_id}")
            latest = await self._event_command_blocks.get_latest_for_evidence(db, evidence_id=evidence.id)
            if (
                latest is None
                or latest.id != block_id
                or DeviceEventCommandBlockStatus(block.status) is not DeviceEventCommandBlockStatus.BLOCKED
            ):
                raise DeviceCommandManualReconciliationConflictError("目标 blocker 不是当前 BLOCKED 因果")
            command = await self._commands.get_by_command_code(db, block.blocking_command_code)
            if command is None or command.id != block.blocking_command_id or command.id is None:
                raise DeviceCommandManualReconciliationConflictError("blocker 指向的命令不存在")
            if not _is_delivery_unknown_reconciling(command):
                raise DeviceCommandManualReconciliationConflictError("命令不是 DELIVERY_UNKNOWN 对账态")
            if command.execution_ref_type in DIAGNOSTIC_REF_TYPES or command.line_run_epoch_id is None:
                raise DeviceCommandManualReconciliationConflictError("诊断命令没有冻结的新鲜度合同")
            if await self._evidences.get_device_result_for_command(db, command.command_code) is not None:
                raise DeviceCommandManualReconciliationConflictError("阻塞命令已有 DEVICE_RESULT")
            binding = await self._epochs.get_binding_for_dispatch(
                db,
                line_run_epoch_id=command.line_run_epoch_id,
                device_code=command.device_code,
            )
            if binding is None or binding.status_max_age_ms <= 0:
                raise DeviceCommandManualReconciliationConflictError("冻结设备 binding 不可解析")
            try:
                endpoint = validate_device_endpoint_base_url(binding.endpoint_base_url)
            except ValueError as error:
                raise DeviceCommandManualReconciliationConflictError("冻结设备 Endpoint 不可解析") from error
            return _ManualReconciliationTarget(
                evidence_id=evidence.id,
                block_id=block_id,
                command_id=command.id,
                command_code=command.command_code,
                command_version=command.version,
                device_code=command.device_code,
                endpoint_base_url=endpoint,
                status_max_age_ms=binding.status_max_age_ms,
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


def _is_delivery_unknown_reconciling(command: DeviceCommand) -> bool:
    return (
        CommandStatus(command.status) is CommandStatus.RECONCILING
        and command.reconciliation_reason == "DELIVERY_UNKNOWN"
    )


def _matches_manual_reconciliation_target(
    command: DeviceCommand | None,
    target: _ManualReconciliationTarget,
) -> bool:
    return (
        command is not None
        and command.id == target.command_id
        and command.command_code == target.command_code
        and command.device_code == target.device_code
        and command.version == target.command_version
        and _is_delivery_unknown_reconciling(command)
    )


__all__ = [
    "DeviceCommandCapacityError",
    "DeviceCommandDeadlineError",
    "DeviceCommandIdentityConflictError",
    "DeviceCommandManualReconciliationConflictError",
    "DeviceCommandManualReconciliationNotFoundError",
    "DeviceCommandNotFoundError",
    "DeviceCommandService",
    "DeviceContractMismatchError",
    "DeviceNotFoundError",
]
