"""Transport 自动联调轮次的应用服务。"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

import wes_plugin_sdk as sdk
from sqlalchemy.exc import IntegrityError

from src.app.execution.models.wms_confirmation import WmsConfirmationStatus
from src.app.execution.repositories import InboundEvidenceRepository, WmsConfirmationRepository
from src.app.execution.services.wms_confirmation_service import WmsConfirmationLifecycleService
from src.app.sys.services.audit_service import audit_log_service
from src.app.sys.services.event_stream_service import TRANSPORT_DEBUG_RUN_STREAM_CHANNEL, event_stream_service
from src.app.transport.contracts import (
    MoveBinsRequest,
    RackPosition,
    RotateRackRequest,
    TransportContractError,
    TransportTaskStatus,
)
from src.app.transport.debug_run_contracts import (
    CreateTransportDebugRun,
    TransportDebugBinSelection,
    TransportDebugFaceGroup,
    TransportDebugRunPhase,
    TransportDebugRunStatus,
    TransportDebugRunStepStatus,
)
from src.app.transport.debug_run_evidence import Scan12EvidenceDisposition, evaluate_scan12_evidence
from src.app.transport.debug_run_state_machine import (
    build_debug_transport_request,
    evaluate_debug_transport_task,
    next_debug_step,
)
from src.app.transport.models import TransportDebugRun, TransportDebugRunStep, TransportMember
from src.app.wms_adapter.outbound_picking.return_batch_typed import decode_outcome, encode_request
from src.app.wms_adapter.outbound_picking.return_batch_wire import (
    BIN_RETURN_BATCH_OPERATION,
    parse_bin_return_batch_request,
    parse_bin_return_batch_response,
)
from src.app.workline.repositories import WorkLineRepository
from src.core.exceptions import NotFoundException
from src.core.transaction_wakeup import defer_wakeup
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.transport.debug_run_repository import TransportDebugRunRepository
    from src.app.transport.service import TransportService
    from src.core.task_queue_gateway import TaskQueueGateway

logger = logging.getLogger(__name__)

_STREAM_EVENT_TYPE = "transport_debug_run.updated"
_ACTIVE_SCOPE = "GLOBAL"
_ABORT_ASSERTION = "PHYSICAL_STATE_VERIFIED"
_TERMINAL_TRANSPORT_STATUSES = {
    TransportTaskStatus.REJECTED.value,
    TransportTaskStatus.SUCCEEDED.value,
    TransportTaskStatus.FAILED.value,
}
_CLAIM_LEASE = timedelta(seconds=30)
_RECOVERABLE_ATTENTION_CODES = {
    "EVIDENCE_RECONCILING",
    "WMS_RETURN_OWNER_INVALID",
    "TRANSPORT_RECONCILING",
    "TRANSPORT_DELIVERY_UNKNOWN",
    "TRANSPORT_POSITION_UNKNOWN",
    "TRANSPORT_RESULT_TIMEOUT",
}
_EVIDENCE_PAGE_SIZE = 1000


class TransportDebugReturnBatchOwner:
    """人工确认的联调轮次以冻结请求认领可靠义务，不依赖正式工作线的启用状态。"""

    def __init__(self, repository: TransportDebugRunRepository | None = None) -> None:
        from src.app.transport.debug_run_repository import TransportDebugRunRepository

        self._repository = repository or TransportDebugRunRepository()

    async def validate_owner(self, db: AsyncSession, *, workline_id: int, request_payload: dict[str, Any]) -> bool:
        try:
            request = parse_bin_return_batch_request(request_payload)
        except (ValueError, TypeError):
            return False
        run = await self._repository.get_return_request_owner(db, str(request.operation_id))
        if run is None:
            return False
        configuration = run.configuration_json
        return (
            configuration.get("workline_id") == workline_id
            and configuration.get("workline_code") == request.data.workline_code
            and configuration.get("return_requests", {}).get(str(request.operation_id)) == request_payload
        )


class TransportDebugRunContractError(ValueError):
    """自动联调轮次请求不满足稳定合同。"""


class TransportDebugRunConflict(TransportDebugRunContractError):
    """自动联调轮次与当前执行事实冲突。"""


class _TransportTaskIntegrityConflict(Exception):
    """Transport 意图持久化冲突，需在原事务回滚后记录。"""


class TransportDebugRunEventPublisher(Protocol):
    async def publish_to(self, channel: str, event_type: str, payload: dict[str, object]) -> bool: ...


@dataclass(frozen=True, slots=True)
class TransportDebugRunStepSnapshot:
    ordinal: int
    group_index: int | None
    phase: TransportDebugRunPhase
    status: TransportDebugRunStepStatus
    client_request_id: str | None
    transport_task_id: str | None
    evidence_high_watermark: int | None
    evidence_not_before_ms: int | None
    observed_bin_codes: tuple[str, ...]
    reason_code: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class TransportDebugRunSnapshot:
    workline_code: str
    returned_bins: tuple[dict[str, str], ...]
    run_id: str
    status: TransportDebugRunStatus
    rack_id: str
    face_groups: tuple[TransportDebugFaceGroup, ...]
    current_group_index: int
    current_phase: TransportDebugRunPhase
    current_step: TransportDebugRunStepSnapshot | None
    steps: tuple[TransportDebugRunStepSnapshot, ...]
    observed_bin_codes: tuple[str, ...]
    attention_code: str | None
    attention_detail: str | None
    can_abort: bool
    version: int
    created_by_user_id: int
    aborted_by_user_id: int | None
    aborted_reason: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class TransportDebugRunPage:
    items: tuple[TransportDebugRunSnapshot, ...]
    next_cursor: str | None


class TransportDebugRunService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        repository: TransportDebugRunRepository,
        transport_service: TransportService,
        *,
        clock: Callable[[], datetime] = timezone.now_for_db,
        event_publisher: TransportDebugRunEventPublisher = event_stream_service,
        task_queue_gateway: TaskQueueGateway | None = None,
    ) -> None:
        self._worklines = WorkLineRepository()
        self._confirmations = WmsConfirmationRepository()
        self._wms = WmsConfirmationLifecycleService(workline_owner=TransportDebugReturnBatchOwner(repository))
        self._wms_evidence = InboundEvidenceRepository()
        self._sessions = session_factory
        self._repository = repository
        self._transport = transport_service
        self._clock = clock
        self._event_publisher = event_publisher
        self._task_queue = task_queue_gateway

    async def create_run(
        self,
        request: CreateTransportDebugRun,
        *,
        actor_id: int,
    ) -> TransportDebugRunSnapshot:
        now = self._clock()
        run_id = f"debug-run-{new_uuid7()}"
        for group in request.face_groups:
            _ = sdk.wms_operations.outbound_bin_return_batch(
                operation_id=new_uuid7(),
                workline_code=request.workline_code,
                rack_id=request.rack_id,
                rack_face=group.face,
                return_candidates=tuple(
                    sdk.BinReturnCandidate(i, item.bin_code, "CNV0302") for i, item in enumerate(group.bins, 1)
                ),
            )
        configuration = _freeze_configuration(request)
        run = TransportDebugRun(
            run_id=run_id,
            status=TransportDebugRunStatus.RUNNING.value,
            active_scope=_ACTIVE_SCOPE,
            rack_id=request.rack_id,
            configuration_json=configuration,
            current_group_index=0,
            current_phase=TransportDebugRunPhase.RACK_TO_STATION.value,
            current_step_ordinal=0,
            version=1,
            created_by_user_id=actor_id,
            created_at=now,
            updated_at=now,
        )
        first_step = TransportDebugRunStep(
            run_id=run_id,
            ordinal=0,
            group_index=0,
            phase=TransportDebugRunPhase.RACK_TO_STATION.value,
            status=TransportDebugRunStepStatus.PENDING.value,
            client_request_id=new_uuid7(),
            created_at=now,
            updated_at=now,
        )
        try:
            async with self._sessions.begin() as db:
                if await self._repository.get_active_run(db, for_update=True) is not None:
                    raise TransportDebugRunConflict("an active debug run already exists")
                workline = await self._worklines.get_by_line_code(db, request.workline_code)
                if workline is None:
                    raise TransportDebugRunContractError("联调 WMS 归属工作线未登记")
                configuration["workline_id"] = workline.id
                first_request = build_debug_transport_request(run, first_step)
                if first_request is None:
                    raise TransportDebugRunContractError("first debug step configuration is invalid")
                handle = await self._transport.create_debug_task_in_session(db, first_request)
                if handle.client_request_id != first_step.client_request_id:
                    raise TransportDebugRunContractError("first debug task identity does not match the step")
                first_step.transport_task_id = handle.transport_task_id
                first_step.status = TransportDebugRunStepStatus.WAITING.value
                await self._repository.add_run(db, run, first_step)
                snapshot = await self._snapshot(db, run, steps=(first_step,))
                event_payload = _event_payload(run)
        except IntegrityError as error:
            async with self._sessions() as db:
                if await self._repository.get_active_run(db) is not None:
                    raise TransportDebugRunConflict("an active debug run already exists") from error
            raise TransportDebugRunConflict("transport resource is already active") from error
        await self._publish_update(event_payload)
        return snapshot

    async def get_run(self, run_id: str) -> TransportDebugRunSnapshot:
        async with self._sessions() as db:
            run = await self._repository.get_run(db, run_id)
            if run is None:
                raise NotFoundException(resource_type="TransportDebugRun", resource_id=run_id)
            return await self._snapshot(db, run)

    async def list_runs(self, *, limit: int, cursor: str | None) -> TransportDebugRunPage:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise TransportDebugRunContractError("limit must be between 1 and 100")
        before_created_at, before_id = _decode_cursor(cursor)
        async with self._sessions() as db:
            runs = await self._repository.list_recent_runs(
                db,
                limit=limit + 1,
                before_created_at=before_created_at,
                before_id=before_id,
            )
            visible = runs[:limit]
            step_histories = await self._repository.list_steps_for_runs(db, visible)
            items = tuple([await self._snapshot(db, run, steps=step_histories.get(run.run_id, ())) for run in visible])
        next_cursor = None
        if len(runs) > limit and visible:
            tail = visible[-1]
            if tail.id is None:
                raise TransportDebugRunContractError("persisted debug run is missing id")
            next_cursor = _encode_cursor(tail.created_at, tail.id)
        return TransportDebugRunPage(items=items, next_cursor=next_cursor)

    async def abort_run(
        self,
        run_id: str,
        *,
        assertion: str,
        reason: str,
        actor_id: int,
    ) -> TransportDebugRunSnapshot:
        if assertion != _ABORT_ASSERTION:
            raise TransportDebugRunContractError("assertion must be PHYSICAL_STATE_VERIFIED")
        if not isinstance(reason, str) or not reason.strip():
            raise TransportDebugRunContractError("reason must not be empty")
        now = self._clock()
        async with self._sessions.begin() as db:
            run = await self._repository.get_run(db, run_id, for_update=True)
            if run is None:
                raise NotFoundException(resource_type="TransportDebugRun", resource_id=run_id)
            if run.status != TransportDebugRunStatus.NEEDS_ATTENTION.value:
                raise TransportDebugRunConflict("debug run must be NEEDS_ATTENTION before abort")
            if await self._has_unresolved_wms_return(db, run):
                raise TransportDebugRunConflict("unresolved WMS return allocation prevents abort")
            steps = await self._repository.list_steps(db, run_id)
            await self._finalize_provably_unsent_tasks(db, steps)
            if not await self._all_tasks_terminal(db, steps):
                raise TransportDebugRunConflict("all associated transport tasks must be terminal")
            if await self._repository.has_active_transport_binding(db, run_id):
                raise TransportDebugRunConflict("active transport resource binding prevents abort")
            run.status = TransportDebugRunStatus.ABORTED.value
            run.active_scope = None
            run.claim_token = None
            run.claim_until = None
            run.aborted_by_user_id = actor_id
            run.aborted_reason = reason
            run.attention_code = None
            run.attention_detail = None
            run.version += 1
            run.updated_at = now
            _ = await audit_log_service.create_audit_log(
                db,
                method="POST",
                title="终止 Transport 自动联调轮次",
                path=f"/api/v1/transport/debug-runs/{run_id}/abort",
                args={
                    "model": "TransportDebugRun",
                    "operation": "update",
                    "record_id": run_id,
                    "changes": {
                        "status": TransportDebugRunStatus.ABORTED.value,
                        "assertion": assertion,
                        "reason": reason,
                        "actor_id": actor_id,
                    },
                },
            )
            snapshot = await self._snapshot(db, run, steps=steps)
            event_payload = _event_payload(run)
        await self._publish_update(event_payload)
        return snapshot

    async def advance_run(self, run_id: str) -> bool:
        """领取指定轮次并最多完成一次持久状态跃迁。"""

        token = new_uuid7()
        now = self._clock()
        async with self._sessions.begin() as db:
            claimed = await self._repository.claim_run(
                db,
                run_id=run_id,
                token=token,
                now=now,
                claim_until=now + _CLAIM_LEASE,
            )
        if not claimed:
            return False
        return await self._advance_claimed_run(run_id, token)

    async def advance_active_runs(self, limit: int) -> int:
        """批量领取活动轮次，再让每个轮次最多发生一次状态跃迁。"""

        if type(limit) is not int or not 1 <= limit <= 100:
            raise TransportDebugRunContractError("limit must be between 1 and 100")
        token = new_uuid7()
        now = self._clock()
        async with self._sessions.begin() as db:
            claimed = await self._repository.claim_active_runs(
                db,
                token=token,
                now=now,
                claim_until=now + _CLAIM_LEASE,
                limit=limit,
            )
        advanced = 0
        for run_id, claim_token in claimed:
            if await self._advance_claimed_run(run_id, claim_token):
                advanced += 1
        return advanced

    async def _advance_claimed_run(self, run_id: str, claim_token: str) -> bool:
        """只推进由当前 token 领取的轮次。"""

        now = self._clock()
        changed = False
        event_payload: dict[str, object] | None = None
        try:
            async with self._sessions.begin() as db:
                run = await self._repository.get_claimed_run(db, run_id=run_id, token=claim_token, now=now)
                if run is None:
                    return False
                step = await self._repository.get_current_step(db, run, for_update=True)
                if step is None:
                    changed = self._set_attention(run, None, "DEBUG_STEP_MISSING", now)
                elif await self._repository.has_pending_transport_evidence(db, run.run_id):
                    changed = False
                elif await self._repository.has_transport_evidence_conflict(db, run.run_id):
                    changed = self._set_attention(run, step, "TRANSPORT_EVIDENCE_CONFLICT", now)
                elif await self._repository.has_run_observed_evidence_conflict(db, run.run_id):
                    changed = self._set_attention(run, step, "EVIDENCE_SOURCE_EVENT_CONFLICT", now)
                elif run.status == TransportDebugRunStatus.NEEDS_ATTENTION.value and (
                    run.attention_code not in _RECOVERABLE_ATTENTION_CODES
                ):
                    changed = False
                elif (
                    not run.configuration_json.get("workline_code")
                    or type(run.configuration_json.get("workline_id")) is not int
                    or (
                        step.phase == TransportDebugRunPhase.BINS_TO_RACK.value
                        and step.transport_task_id is not None
                        and not run.configuration_json.get("return_batches", {}).get(str(step.ordinal), {}).get("moves")
                    )
                ):
                    changed = self._set_attention(
                        run,
                        step,
                        "DEBUG_RUN_CONFIGURATION_UPGRADE_REQUIRED",
                        now,
                        "旧轮次缺少冻结的工作线或 WMS 分配配置；保留执行身份和围栏，须核对原任务后人工处理",
                    )
                elif step.phase == TransportDebugRunPhase.WAIT_SCAN12.value:
                    changed = await self._advance_scan_wait(db, run, step, now)
                elif step.transport_task_id is None:
                    changed = await self._create_transport_task(db, run, step, now)
                else:
                    changed = await self._advance_transport_step(db, run, step, now)
                run.claim_token = None
                run.claim_until = None
                if changed:
                    if (
                        self._task_queue is not None
                        and run.status == TransportDebugRunStatus.RUNNING.value
                        and step is not None
                        and run.current_step_ordinal != step.ordinal
                    ):
                        defer_wakeup(db, self._task_queue.enqueue_transport_debug)
                    event_payload = _event_payload(run)
        except _TransportTaskIntegrityConflict:
            return await self._record_claimed_attention(
                run_id,
                claim_token,
                reason_code="TRANSPORT_RESOURCE_CONFLICT",
                detail="transport resource is already active",
                now=now,
            )
        if event_payload is not None:
            await self._publish_update(event_payload)
        return changed

    async def _create_transport_task(
        self,
        db: AsyncSession,
        run: TransportDebugRun,
        step: TransportDebugRunStep,
        now: datetime,
    ) -> bool:
        retry_owner_admission = (
            step.phase == TransportDebugRunPhase.BINS_TO_RACK.value
            and step.status == TransportDebugRunStepStatus.NEEDS_ATTENTION.value
            and run.attention_code == "WMS_RETURN_OWNER_INVALID"
        )
        if step.status != TransportDebugRunStepStatus.PENDING.value and not retry_owner_admission:
            return self._set_attention(run, step, "TRANSPORT_TASK_MISSING", now)
        if step.phase == TransportDebugRunPhase.BINS_TO_RACK.value and await self._has_observed_evidence_conflict(
            db, step
        ):
            return self._set_attention(run, step, "EVIDENCE_SOURCE_EVENT_CONFLICT", now)
        if step.phase == TransportDebugRunPhase.BINS_TO_RACK.value:
            allocation = await self._prepare_return_batch(db, run, step, now)
            if allocation != "READY":
                return allocation != "WAIT"
        try:
            request = build_debug_transport_request(run, step)
        except (ValueError, TypeError) as error:
            return self._set_attention(run, step, "DEBUG_STEP_CONFIGURATION_INVALID", now, str(error))
        if request is None:
            return self._set_attention(run, step, "DEBUG_STEP_CONFIGURATION_INVALID", now)
        try:
            if (
                isinstance(request, (MoveBinsRequest, RotateRackRequest))
                or step.phase == TransportDebugRunPhase.RACK_TO_STORAGE.value
            ):
                await self._transport.assert_debug_rack_position_in_session(
                    db,
                    run.rack_id,
                    RackPosition(_configuration_text(run.configuration_json, "workstation")),
                    _expected_rack_face(run, step),
                )
            handle = await self._transport.create_debug_task_in_session(db, request)
        except TransportContractError as error:
            return self._set_attention(run, step, "TRANSPORT_CONTRACT_REJECTED", now, str(error))
        except IntegrityError as error:
            raise _TransportTaskIntegrityConflict from error
        if handle.client_request_id != step.client_request_id:
            return self._set_attention(run, step, "TRANSPORT_TASK_IDENTITY_CONFLICT", now)
        step.transport_task_id = handle.transport_task_id
        step.status = TransportDebugRunStepStatus.WAITING.value
        step.updated_at = now
        self._touch(run, now)
        return True

    async def _prepare_return_batch(self, db, run, step, now) -> str:  # noqa: PLR0911 - closed persisted allocation states
        """在宿主事务中冻结 typed 请求；由公共 dispatcher 发送，这里只消费自己的可靠结果。"""
        batches = dict(run.configuration_json.get("return_batches", {}))
        batch = dict(batches.get(str(step.ordinal), {}))
        if batch.get("moves"):
            return "READY"
        retry_at = batch.get("retry_at")
        if retry_at is not None and now < datetime.fromisoformat(retry_at):
            return "WAIT"
        operation_id = batch.get("operation_id")
        if operation_id is None:
            group = _frozen_face_groups(run.configuration_json)[run.current_group_index]
            returned = {item["bin_code"] for item in run.configuration_json.get("returned_bins", [])}
            queue = run.configuration_json.get("return_queues", {}).get(str(run.current_group_index), [])
            candidates = [code for code in queue if code not in returned]
            if not candidates:
                _ = self._set_attention(run, step, "DEBUG_RETURN_FIFO_MISSING", now)
                return "CHANGED"
            try:
                await self._transport.assert_debug_rack_position_in_session(
                    db,
                    run.rack_id,
                    RackPosition(_configuration_text(run.configuration_json, "workstation")),
                    group.face,
                )
            except TransportContractError as error:
                _ = self._set_attention(run, step, "TRANSPORT_CONTRACT_REJECTED", now, str(error))
                return "CHANGED"
            operation_id = new_uuid7()
            intent = sdk.wms_operations.outbound_bin_return_batch(
                operation_id=operation_id,
                workline_code=_configuration_text(run.configuration_json, "workline_code"),
                rack_id=run.rack_id,
                rack_face=group.face,
                return_candidates=tuple(
                    sdk.BinReturnCandidate(i, code, "CNV0302") for i, code in enumerate(candidates, 1)
                ),
            )
            payload = encode_request(intent, timestamp=_ceil_unix_ms(now))
            run.configuration_json = {
                **run.configuration_json,
                "return_requests": {
                    **run.configuration_json.get("return_requests", {}),
                    operation_id: payload,
                },
            }
            try:
                _ = await self._wms.create_or_get(
                    db,
                    operation=BIN_RETURN_BATCH_OPERATION,
                    operation_id=operation_id,
                    workline_id=run.configuration_json["workline_id"],
                    request_payload=payload,
                    deadline_at=now + timedelta(minutes=30),
                    created_at=now,
                )
            except ValueError as error:
                changed = self._set_attention(run, step, "WMS_RETURN_OWNER_INVALID", now, str(error))
                return "CHANGED" if changed else "WAIT"
            if self._task_queue is not None:
                defer_wakeup(db, self._task_queue.enqueue_wms_confirmations)
            batch = {"operation_id": operation_id}
        else:
            confirmation = await self._confirmations.get_by_identity_for_update(
                db, BIN_RETURN_BATCH_OPERATION, operation_id
            )
            if confirmation is None or confirmation.status == WmsConfirmationStatus.RECONCILING:
                _ = self._set_attention(run, step, "WMS_RETURN_RECONCILING", now)
                return "CHANGED"
            if confirmation.status != WmsConfirmationStatus.COMPLETED:
                return "WAIT"
            evidence = (
                await self._wms_evidence.get_by_id_without_lock(db, confirmation.response_evidence_id)
                if confirmation.response_evidence_id is not None
                else None
            )
            try:
                if evidence is None:
                    raise ValueError("WMS return response evidence missing")
                _ = parse_bin_return_batch_response(
                    200,
                    evidence.normalized_payload,
                    request=parse_bin_return_batch_request(confirmation.request_payload),
                )
                outcome = decode_outcome(evidence.normalized_payload).result
            except (ValueError, TypeError) as error:
                _ = self._set_attention(run, step, "WMS_RETURN_RESPONSE_INVALID", now, str(error))
                return "CHANGED"
            if isinstance(outcome, sdk.BinBatchNoBatch):
                # 现场自动联调约定：无 WMS 批次时按本组已成功出库的原槽位退回，保持请求 FIFO。
                source_steps = [
                    item
                    for item in await self._repository.list_steps(db, run.run_id)
                    if item.group_index == step.group_index
                    and item.phase == TransportDebugRunPhase.BINS_TO_INFEED.value
                    and item.status == TransportDebugRunStepStatus.SUCCEEDED.value
                    and item.transport_task_id is not None
                ]
                if len(source_steps) != 1:
                    _ = self._set_attention(run, step, "DEBUG_RETURN_SOURCE_MISSING", now)
                    return "CHANGED"
                source_task_id = source_steps[0].transport_task_id
                members = await self._repository.list_transport_members(db, source_task_id)
                sources = {member.object_id: member for member in members}
                request_data = confirmation.request_payload["data"]
                moves = []
                for candidate in request_data["return_candidates"]:
                    member = sources.get(candidate["bin_code"])
                    if (
                        member is None
                        or member.status != TransportTaskStatus.SUCCEEDED.value
                        or member.source_json.get("kind") != "RACK_BIN_SLOT"
                        or member.source_json.get("rack_id") != request_data["rack_id"]
                        or member.source_json.get("rack_face") != request_data["rack_face"]
                        or not member.source_json.get("slot_id")
                    ):
                        _ = self._set_attention(run, step, "DEBUG_RETURN_SOURCE_MISSING", now)
                        return "CHANGED"
                    moves.append(
                        {
                            "bin_code": member.object_id,
                            "rack_id": member.source_json["rack_id"],
                            "rack_face": member.source_json["rack_face"],
                            "slot_id": member.source_json["slot_id"],
                        }
                    )
                batch.update(
                    moves=moves,
                    allocation_source="DEBUG_NO_BATCH_ORIGINAL_SLOTS",
                    source_transport_task_id=source_task_id,
                )
            elif isinstance(outcome, sdk.BinReturnBatchReady):
                batch["moves"] = [
                    {
                        "bin_code": move.bin_code,
                        "rack_id": move.target.rack_id,
                        "rack_face": move.target.rack_face,
                        "slot_id": move.target.slot_id,
                    }
                    for move in outcome.moves
                ]
            else:
                _ = self._set_attention(run, step, "WMS_RETURN_RESPONSE_INVALID", now)
                return "CHANGED"
        batches[str(step.ordinal)] = batch
        run.configuration_json = {**run.configuration_json, "return_batches": batches}
        if run.attention_code == "WMS_RETURN_OWNER_INVALID":
            run.status = TransportDebugRunStatus.RUNNING.value
            run.attention_code = None
            run.attention_detail = None
            step.status = TransportDebugRunStepStatus.PENDING.value
            step.reason_code = None
            step.updated_at = now
        self._touch(run, now)
        return "CHANGED"

    async def _record_claimed_attention(
        self,
        run_id: str,
        claim_token: str,
        *,
        reason_code: str,
        detail: str,
        now: datetime,
    ) -> bool:
        event_payload: dict[str, object] | None = None
        async with self._sessions.begin() as db:
            run = await self._repository.get_claimed_run(db, run_id=run_id, token=claim_token, now=now)
            if run is None:
                return False
            step = await self._repository.get_current_step(db, run, for_update=True)
            changed = self._set_attention(run, step, reason_code, now, detail)
            run.claim_token = None
            run.claim_until = None
            if changed:
                event_payload = _event_payload(run)
        if event_payload is not None:
            await self._publish_update(event_payload)
        return changed

    async def _advance_transport_step(
        self,
        db: AsyncSession,
        run: TransportDebugRun,
        step: TransportDebugRunStep,
        now: datetime,
    ) -> bool:
        transport_task_id = step.transport_task_id
        if transport_task_id is None:
            return self._set_attention(run, step, "TRANSPORT_TASK_MISSING", now)
        task = await self._repository.get_transport_task(db, transport_task_id)
        if task is None:
            return self._set_attention(run, step, "TRANSPORT_TASK_MISSING", now)
        members = await self._repository.list_transport_members(db, transport_task_id)
        progress_changed = self._record_confirmed_rack_return_bins(run, step, members, now)
        evaluation = evaluate_debug_transport_task(step, task, members, run)
        if evaluation.disposition == "WAIT":
            return progress_changed
        if evaluation.disposition == "FAILED":
            step.status = TransportDebugRunStepStatus.FAILED.value
            step.reason_code = evaluation.reason_code
            step.updated_at = now
            run.status = TransportDebugRunStatus.FAILED.value
            run.active_scope = None
            run.attention_code = evaluation.reason_code
            run.attention_detail = None
            self._touch(run, now)
            return True
        if evaluation.disposition == "ATTENTION":
            return self._set_attention(run, step, evaluation.reason_code or "TRANSPORT_RECONCILING", now)
        if step.phase == TransportDebugRunPhase.BINS_TO_RACK.value:
            returned = list(run.configuration_json.get("returned_bins", []))
            returned.extend(
                {
                    "bin_code": member.object_id,
                    **{key: member.final_position_json[key] for key in ("rack_id", "rack_face", "slot_id")},
                }
                for member in members
                if member.final_position_json is not None
            )
            run.configuration_json = {**run.configuration_json, "returned_bins": returned}
        step.status = TransportDebugRunStepStatus.SUCCEEDED.value
        step.reason_code = None
        step.updated_at = now
        if next_debug_step(run, step) is None:
            run.status = TransportDebugRunStatus.COMPLETED.value
            run.active_scope = None
            run.attention_code = None
            run.attention_detail = None
            self._touch(run, now)
            return True
        await self._append_next_step(db, run, step, now)
        return True

    async def _advance_scan_wait(  # noqa: PLR0911 - every unsafe Evidence state exits fail-closed
        self,
        db: AsyncSession,
        run: TransportDebugRun,
        step: TransportDebugRunStep,
        now: datetime,
    ) -> bool:
        if step.evidence_high_watermark is None or step.evidence_not_before_ms is None:
            return self._set_attention(run, step, "EVIDENCE_BOUNDARY_MISSING", now)
        group = _frozen_face_groups(run.configuration_json)[run.current_group_index]
        selected_bins = frozenset(selection.bin_code for selection in group.bins)
        observed = list(step.observed_bins_json)
        observed_bins = {
            item.get("bin_code")
            for item in observed
            if isinstance(item, dict) and isinstance(item.get("bin_code"), str)
        }
        source_events = {
            item.get("source_event_id"): item.get("bin_code")
            for item in observed
            if isinstance(item, dict) and isinstance(item.get("source_event_id"), str)
        }
        changed = False
        boundary = timezone.to_db_datetime(step.evidence_not_before_ms / 1000)
        if boundary is None:
            return self._set_attention(run, step, "EVIDENCE_BOUNDARY_MISSING", now)
        after_received_at: datetime | None = None
        after_id: int | None = None
        while True:
            evidences = await self._repository.list_device_evidences_since(
                db,
                received_at=boundary,
                evidence_high_watermark=step.evidence_high_watermark,
                after_received_at=after_received_at,
                after_id=after_id,
                limit=_EVIDENCE_PAGE_SIZE,
            )
            for evidence in evidences:
                evaluation = evaluate_scan12_evidence(
                    evidence,
                    not_before_ms=step.evidence_not_before_ms,
                    selected_bins=selected_bins,
                )
                if evaluation.disposition is Scan12EvidenceDisposition.ATTENTION:
                    return self._set_attention(run, step, evaluation.reason_code or "EVIDENCE_AMBIGUOUS", now)
                if evaluation.disposition is Scan12EvidenceDisposition.WAIT:
                    if changed:
                        step.observed_bins_json = observed
                        step.updated_at = now
                        self._touch(run, now)
                    return changed
                if evaluation.evidence_id is None:
                    return self._set_attention(run, step, "EVIDENCE_ID_MISSING", now)
                if evaluation.disposition is not Scan12EvidenceDisposition.MATCH:
                    continue
                if evaluation.bin_code is None or evaluation.source_event_id is None:
                    return self._set_attention(run, step, "EVIDENCE_MATCH_INVALID", now)
                prior_bin = source_events.get(evaluation.source_event_id)
                if prior_bin is not None and prior_bin != evaluation.bin_code:
                    return self._set_attention(run, step, "EVIDENCE_SOURCE_EVENT_CONFLICT", now)
                if prior_bin is not None or evaluation.bin_code in observed_bins:
                    continue
                observed.append(
                    {
                        "bin_code": evaluation.bin_code,
                        "evidence_id": evaluation.evidence_id,
                        "source_event_id": evaluation.source_event_id,
                    }
                )
                observed_bins.add(evaluation.bin_code)
                source_events[evaluation.source_event_id] = evaluation.bin_code
                changed = True
            if len(evidences) < _EVIDENCE_PAGE_SIZE:
                break
            tail = evidences[-1]
            if tail.id is None:
                return self._set_attention(run, step, "EVIDENCE_ID_MISSING", now)
            after_received_at = tail.received_at
            after_id = tail.id
        if changed:
            step.observed_bins_json = observed
            step.updated_at = now
        if observed_bins >= selected_bins:
            if await self._has_observed_evidence_conflict(db, step, observed=observed):
                return self._set_attention(run, step, "EVIDENCE_SOURCE_EVENT_CONFLICT", now)
            step.status = TransportDebugRunStepStatus.SUCCEEDED.value
            step.updated_at = now
            await self._append_next_step(db, run, step, now)
            return True
        if changed:
            self._touch(run, now)
        return changed

    async def _has_observed_evidence_conflict(
        self,
        db: AsyncSession,
        step: TransportDebugRunStep,
        *,
        observed: list[dict[str, object]] | None = None,
    ) -> bool:
        items = observed if observed is not None else step.observed_bins_json
        evidence_ids = [
            evidence_id
            for item in items
            if isinstance(item, dict)
            for evidence_id in [item.get("evidence_id")]
            if isinstance(evidence_id, int)
        ]
        return await self._repository.has_evidence_conflicts(db, evidence_ids)

    async def _append_next_step(
        self,
        db: AsyncSession,
        run: TransportDebugRun,
        completed_step: TransportDebugRunStep,
        now: datetime,
    ) -> None:
        next_cursor = next_debug_step(run, completed_step)
        if next_cursor is None:
            raise TransportDebugRunContractError("completed debug step has no successor")
        next_phase, next_group_index = next_cursor
        high_watermark: int | None = None
        not_before_ms: int | None = None
        if next_phase == TransportDebugRunPhase.BINS_TO_INFEED.value:
            high_watermark = await self._repository.max_device_evidence_id(db)
            not_before_ms = _ceil_unix_ms(now)
        elif next_phase == TransportDebugRunPhase.WAIT_SCAN12.value:
            high_watermark = completed_step.evidence_high_watermark
            not_before_ms = completed_step.evidence_not_before_ms
        if completed_step.phase == TransportDebugRunPhase.WAIT_SCAN12.value:
            queues = dict(run.configuration_json.get("return_queues", {}))
            queues[str(next_group_index)] = [item["bin_code"] for item in completed_step.observed_bins_json]
            run.configuration_json = {**run.configuration_json, "return_queues": queues}
        next_step = TransportDebugRunStep(
            run_id=run.run_id,
            ordinal=completed_step.ordinal + 1,
            group_index=next_group_index,
            phase=next_phase,
            status=(
                TransportDebugRunStepStatus.WAITING.value
                if next_phase == TransportDebugRunPhase.WAIT_SCAN12.value
                else TransportDebugRunStepStatus.PENDING.value
            ),
            client_request_id=(None if next_phase == TransportDebugRunPhase.WAIT_SCAN12.value else new_uuid7()),
            evidence_high_watermark=high_watermark,
            evidence_not_before_ms=not_before_ms,
            observed_bins_json=[],
            created_at=now,
            updated_at=now,
        )
        await self._repository.add_step(db, next_step)
        run.status = TransportDebugRunStatus.RUNNING.value
        run.current_group_index = next_group_index
        run.current_phase = next_phase
        run.current_step_ordinal = next_step.ordinal
        run.attention_code = None
        run.attention_detail = None
        self._touch(run, now)

    def _record_confirmed_rack_return_bins(
        self,
        run: TransportDebugRun,
        step: TransportDebugRunStep,
        members: Sequence[TransportMember],
        now: datetime,
    ) -> bool:
        if step.phase != TransportDebugRunPhase.BINS_TO_RACK.value:
            return False
        group_index = step.group_index
        if group_index is None:
            return False
        try:
            request = build_debug_transport_request(run, step)
        except (ValueError, TypeError):
            return False
        if not isinstance(request, MoveBinsRequest):
            return False
        members_by_bin_code = {member.object_id: member for member in members if member.object_type == "BIN"}
        observed = [
            {"bin_code": move.bin_code}
            for move in request.moves
            if (
                (member := members_by_bin_code.get(move.bin_code)) is not None
                and not member.position_unknown
                and member.target_json == asdict(move.target)
                and member.final_position_json == member.target_json
            )
        ]
        if step.observed_bins_json == observed:
            return False
        step.observed_bins_json = observed
        step.updated_at = now
        self._touch(run, now)
        return True

    def _set_attention(
        self,
        run: TransportDebugRun,
        step: TransportDebugRunStep | None,
        reason_code: str,
        now: datetime,
        detail: str | None = None,
    ) -> bool:
        step_changed = step is not None and (
            step.status != TransportDebugRunStepStatus.NEEDS_ATTENTION.value or step.reason_code != reason_code
        )
        already_equal = (
            run.status == TransportDebugRunStatus.NEEDS_ATTENTION.value
            and run.attention_code == reason_code
            and run.attention_detail == detail
        )
        if step is not None:
            step.status = TransportDebugRunStepStatus.NEEDS_ATTENTION.value
            step.reason_code = reason_code
            step.updated_at = now
        run.status = TransportDebugRunStatus.NEEDS_ATTENTION.value
        run.attention_code = reason_code
        run.attention_detail = detail
        if not already_equal or step_changed:
            self._touch(run, now)
        return not already_equal or step_changed

    @staticmethod
    def _touch(run: TransportDebugRun, now: datetime) -> None:
        run.version += 1
        run.updated_at = now

    async def _snapshot(
        self,
        db: AsyncSession,
        run: TransportDebugRun,
        *,
        steps: Sequence[TransportDebugRunStep] | None = None,
    ) -> TransportDebugRunSnapshot:
        persisted_steps = list(steps) if steps is not None else await self._repository.list_steps(db, run.run_id)
        step_snapshots = tuple(_step_snapshot(step) for step in persisted_steps)
        step_snapshot = next(
            (step for step in step_snapshots if step.ordinal == run.current_step_ordinal),
            None,
        )
        return TransportDebugRunSnapshot(
            workline_code=str(run.configuration_json.get("workline_code", "")),
            returned_bins=tuple(run.configuration_json.get("returned_bins", [])),
            run_id=run.run_id,
            status=TransportDebugRunStatus(run.status),
            rack_id=run.rack_id,
            face_groups=_frozen_face_groups(run.configuration_json),
            current_group_index=run.current_group_index,
            current_phase=TransportDebugRunPhase(run.current_phase),
            current_step=step_snapshot,
            steps=step_snapshots,
            observed_bin_codes=step_snapshot.observed_bin_codes if step_snapshot is not None else (),
            attention_code=run.attention_code,
            attention_detail=run.attention_detail,
            can_abort=await self._can_abort(db, run),
            version=run.version,
            created_by_user_id=run.created_by_user_id,
            aborted_by_user_id=run.aborted_by_user_id,
            aborted_reason=run.aborted_reason,
            created_at=_utc_iso(run.created_at),
            updated_at=_utc_iso(run.updated_at),
        )

    async def _has_unresolved_wms_return(self, db, run) -> bool:
        for batch in run.configuration_json.get("return_batches", {}).values():
            operation_id = batch.get("operation_id")
            if operation_id is None or batch.get("moves"):
                continue
            confirmation = await self._confirmations.get_by_identity_for_update(
                db, BIN_RETURN_BATCH_OPERATION, operation_id
            )
            if confirmation is None or confirmation.status != WmsConfirmationStatus.COMPLETED:
                return True
        return False

    async def _can_abort(self, db: AsyncSession, run: TransportDebugRun) -> bool:
        if run.status != TransportDebugRunStatus.NEEDS_ATTENTION.value:
            return False
        if await self._has_unresolved_wms_return(db, run):
            return False
        steps = await self._repository.list_steps(db, run.run_id)
        task_ids = [step.transport_task_id for step in steps if step.transport_task_id is not None]
        tasks = await self._repository.list_transport_tasks(db, task_ids)
        if len(tasks) != len(set(task_ids)):
            return False
        finalizable_task_ids: set[str] = set()
        for task_id, task in tasks.items():
            if task.status in _TERMINAL_TRANSPORT_STATUSES:
                continue
            if not await self._transport.is_unsent_debug_task_finalizable_in_session(db, task_id):
                return False
            finalizable_task_ids.add(task_id)
        active_binding_task_ids = await self._repository.list_active_transport_binding_task_ids(db, run.run_id)
        return active_binding_task_ids <= finalizable_task_ids

    async def _all_tasks_terminal(
        self,
        db: AsyncSession,
        steps: Sequence[TransportDebugRunStep],
    ) -> bool:
        task_ids = [step.transport_task_id for step in steps if step.transport_task_id is not None]
        tasks = await self._repository.list_transport_tasks(db, task_ids)
        return len(tasks) == len(set(task_ids)) and all(
            task.status in _TERMINAL_TRANSPORT_STATUSES for task in tasks.values()
        )

    async def _finalize_provably_unsent_tasks(
        self,
        db: AsyncSession,
        steps: Sequence[TransportDebugRunStep],
    ) -> None:
        task_ids = [step.transport_task_id for step in steps if step.transport_task_id is not None]
        tasks = await self._repository.list_transport_tasks(db, task_ids)
        for task_id, task in tasks.items():
            if task.status == TransportTaskStatus.PENDING.value:
                _ = await self._transport.finalize_unsent_debug_task_in_session(db, task_id)

    async def _publish_update(self, payload: dict[str, object]) -> None:
        try:
            _ = await self._event_publisher.publish_to(TRANSPORT_DEBUG_RUN_STREAM_CHANNEL, _STREAM_EVENT_TYPE, payload)
        except Exception:
            logger.exception("transport.debug_run.event_publish_failed", extra={"run_id": payload["run_id"]})


def _freeze_configuration(request: CreateTransportDebugRun) -> dict[str, object]:
    return {
        "workline_code": request.workline_code,
        "rack_id": request.rack_id,
        "face_groups": [
            {
                "face": group.face,
                "bins": [{"bin_code": item.bin_code, "slot_id": item.slot_id} for item in group.bins],
            }
            for group in request.face_groups
        ],
        "storage_zone": "WH01",
        "workstation": "KT16",
        "infeed_position": "CNV0301",
        "outfeed_position": "CNV0302",
        "rack_out_template": "CTU01",
        "rack_rotate_template": "CTU02",
        "rack_return_template": "CTU03",
        "rack_return_face": "90",
    }


def _event_payload(run: TransportDebugRun) -> dict[str, object]:
    return {
        "run_id": run.run_id,
        "version": run.version,
        "status": run.status,
        "updated_at": _utc_iso(run.updated_at),
    }


def _ceil_unix_ms(value: datetime) -> int:
    aware = timezone.to_utc(value)
    return int(aware.timestamp()) * 1000 + (aware.microsecond + 999) // 1000


def _expected_rack_face(run: TransportDebugRun, step: TransportDebugRunStep) -> str:
    groups = _frozen_face_groups(run.configuration_json)
    group_index = step.group_index
    if group_index is None:
        raise TransportDebugRunContractError("physical debug step is missing a face group")
    if step.phase == TransportDebugRunPhase.ROTATE_TO_NEXT_FACE.value:
        group_index -= 1
    if group_index < 0 or group_index >= len(groups):
        raise TransportDebugRunContractError("physical debug step face group is out of range")
    return groups[group_index].face


def _frozen_face_groups(configuration: dict[str, object]) -> tuple[TransportDebugFaceGroup, ...]:
    raw_groups = configuration.get("face_groups")
    if not isinstance(raw_groups, list):
        raise TransportDebugRunContractError("frozen face_groups are invalid")
    groups: list[TransportDebugFaceGroup] = []
    for raw_group in raw_groups:
        if not isinstance(raw_group, dict) or not isinstance(raw_group.get("face"), str):
            raise TransportDebugRunContractError("frozen face group is invalid")
        raw_bins = raw_group.get("bins")
        if not isinstance(raw_bins, list):
            raise TransportDebugRunContractError("frozen bins are invalid")
        bins: list[TransportDebugBinSelection] = []
        for raw_bin in raw_bins:
            if (
                not isinstance(raw_bin, dict)
                or not isinstance(raw_bin.get("bin_code"), str)
                or not isinstance(raw_bin.get("slot_id"), str)
            ):
                raise TransportDebugRunContractError("frozen bin is invalid")
            bins.append(TransportDebugBinSelection(raw_bin["bin_code"], raw_bin["slot_id"]))
        groups.append(TransportDebugFaceGroup(raw_group["face"], tuple(bins)))
    return tuple(groups)


def _configuration_text(configuration: dict[str, object], key: str) -> str:
    value = configuration.get(key)
    if not isinstance(value, str) or not value:
        raise TransportDebugRunContractError(f"frozen {key} is invalid")
    return value


def _step_snapshot(step: TransportDebugRunStep) -> TransportDebugRunStepSnapshot:
    observed = step.observed_bins_json if isinstance(step.observed_bins_json, list) else []
    bin_codes = tuple(
        dict.fromkeys(
            item["bin_code"] for item in observed if isinstance(item, dict) and isinstance(item.get("bin_code"), str)
        )
    )
    return TransportDebugRunStepSnapshot(
        ordinal=step.ordinal,
        group_index=step.group_index,
        phase=TransportDebugRunPhase(step.phase),
        status=TransportDebugRunStepStatus(step.status),
        client_request_id=step.client_request_id,
        transport_task_id=step.transport_task_id,
        evidence_high_watermark=step.evidence_high_watermark,
        evidence_not_before_ms=step.evidence_not_before_ms,
        observed_bin_codes=bin_codes,
        reason_code=step.reason_code,
        created_at=_utc_iso(step.created_at),
        updated_at=_utc_iso(step.updated_at),
    )


def _encode_cursor(created_at: datetime, row_id: int) -> str:
    raw = json.dumps([_utc_iso(created_at), row_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[datetime | None, int | None]:
    if cursor is None:
        return None, None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        values = json.loads(base64.urlsafe_b64decode(padded).decode())
        if not isinstance(values, list) or len(values) != 2 or not isinstance(values[0], str):
            raise ValueError
        created_at = datetime.fromisoformat(values[0])
        row_id = values[1]
        if type(row_id) is not int or row_id <= 0:
            raise ValueError
        return timezone.to_utc(created_at).replace(tzinfo=None), row_id
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TransportDebugRunContractError("invalid debug run cursor") from error


def _utc_iso(value: datetime) -> str:
    return timezone.to_utc(value).isoformat()


__all__ = [
    "TransportDebugReturnBatchOwner",
    "TransportDebugRunConflict",
    "TransportDebugRunContractError",
    "TransportDebugRunEventPublisher",
    "TransportDebugRunPage",
    "TransportDebugRunService",
    "TransportDebugRunSnapshot",
    "TransportDebugRunStepSnapshot",
]
