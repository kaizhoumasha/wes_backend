"""AGV/CTU 通用搬运服务。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import asdict
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import IntegrityError

from src.app.transport.contracts import (
    MAX_SUBMIT_ATTEMPTS,
    TRANSPORT_POSITION_OPERATION,
    TRANSPORT_RESULT_OPERATION,
    BinExchangePair,
    BinMove,
    ExchangeBinsRequest,
    HandoffPosition,
    MoveBinsRequest,
    MoveRackRequest,
    RackBinSlot,
    RackFace,
    RackPosition,
    RotateRackRequest,
    TransportCaller,
    TransportContractError,
    TransportHandle,
    TransportIdempotencyConflict,
    TransportMemberOutcome,
    TransportOutcome,
    TransportOutcomeStatus,
    TransportRequest,
    TransportResourceConflict,
    TransportSubmitCode,
    TransportTaskKind,
    TransportTaskStatus,
)
from src.app.transport.models import (
    TransportEvidence,
    TransportMember,
    TransportPositionProjection,
    TransportResourceBinding,
    TransportTask,
)
from src.app.transport.submit_snapshot import build_submit_data, submit_payload_digest
from src.core.uuid7 import new_uuid7
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.transport.contracts import TransportOutcomePublisher, TransportProviderPort
    from src.app.transport.repository import TransportRepository

_CLAIM_SECONDS = 30
_SUBMIT_TIMEOUT_SECONDS = 10
_PUBLISH_TIMEOUT_SECONDS = 10
_RESULT_TIMEOUT = timedelta(minutes=10)
_RETRY_DELAY = timedelta(seconds=2)
_SUBMIT_CONTINUE_BUDGET_SECONDS = 5.0

logger = logging.getLogger(__name__)


class TransportService:
    """提供四个搬运方法，并封装内部可靠收敛入口。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        repository: TransportRepository,
        provider: TransportProviderPort,
    ) -> None:
        self._sessions = session_factory
        self._repository = repository
        self.provider = provider

    async def move_rack(
        self,
        client_request_id: str,
        caller: TransportCaller,
        rack_id: str,
        source: RackPosition,
        target: RackPosition,
    ) -> TransportHandle:
        return await self._create_task(MoveRackRequest(client_request_id, caller, rack_id, source, target))

    async def rotate_rack(
        self,
        client_request_id: str,
        caller: TransportCaller,
        rack_id: str,
        position: RackPosition,
        target_face: RackFace,
    ) -> TransportHandle:
        return await self._create_task(RotateRackRequest(client_request_id, caller, rack_id, position, target_face))

    async def move_bins(
        self,
        client_request_id: str,
        caller: TransportCaller,
        moves: tuple[BinMove, ...],
    ) -> TransportHandle:
        return await self._create_task(MoveBinsRequest(client_request_id, caller, moves))

    async def exchange_bins(
        self,
        client_request_id: str,
        caller: TransportCaller,
        exchange_pairs: tuple[BinExchangePair, ...],
    ) -> TransportHandle:
        return await self._create_task(ExchangeBinsRequest(client_request_id, caller, exchange_pairs))

    async def submit_pending_tasks(self, limit: int) -> int:
        _validate_limit(limit)
        started = time.monotonic()
        processed = 0
        # 单条领取并标记 -> 事务外 HTTP -> fenced 写回；完成后才按 monotonic 预算领取下一条。
        while processed < limit:
            token = uuid.uuid4().hex
            now = timezone.now_for_db()
            async with self._sessions.begin() as db:
                task = await self._repository.claim_next_pending_task(
                    db,
                    token=token,
                    now=now,
                    claim_until=now + timedelta(seconds=_CLAIM_SECONDS),
                )
                if task is None:
                    break
                task_id = task.transport_task_id
                operation_id = task.submit_operation_id
                timestamp_ms = task.submit_timestamp_ms
                payload = _json_value(task.submit_payload_json)
                payload_digest = task.submit_payload_digest

            try:
                async with asyncio.timeout(_SUBMIT_TIMEOUT_SECONDS):
                    result = await self.provider.submit(
                        operation_id=operation_id,
                        timestamp=timestamp_ms,
                        payload=payload,
                        payload_digest=payload_digest,
                    )
            except TimeoutError:
                result_code = TransportSubmitCode.DELIVERY_UNKNOWN
                result = None
            else:
                result_code = result.code if result.transport_task_id == task_id else TransportSubmitCode.CONFLICT

            async with self._sessions.begin() as db:
                current = await self._repository.get_task(db, task_id, for_update=True)
                writeback_now = timezone.now_for_db()
                if current is None or not _matches_submit_snapshot(
                    current,
                    operation_id=operation_id,
                    transport_task_id=task_id,
                    payload=payload,
                    payload_digest=payload_digest,
                ):
                    logger.warning(
                        "transport.submit.late_writeback",
                        extra={
                            "event": "transport.submit.late_writeback",
                            "transport_task_id": task_id,
                            "operation_id": operation_id,
                            "reason": "IDENTITY_OR_DIGEST_MISMATCH",
                        },
                    )
                    processed += 1
                    if time.monotonic() - started >= _SUBMIT_CONTINUE_BUDGET_SECONDS:
                        break
                    continue
                lease_matches = (
                    current.submit_claim_token == token
                    and current.submit_claim_until is not None
                    and current.submit_claim_until >= writeback_now
                )
                has_evidence = await self._repository.has_evidence(db, task_id)
                self._apply_submit_result(
                    current,
                    result_code,
                    result,
                    writeback_now,
                    lease_matches=lease_matches,
                    has_evidence=has_evidence,
                    operation_id=operation_id,
                )
                if current.status in {"REJECTED", "SUCCEEDED", "FAILED"}:
                    await self._repository.release_bindings(db, task_id, now=writeback_now)
            processed += 1
            if time.monotonic() - started >= _SUBMIT_CONTINUE_BUDGET_SECONDS:
                break
        logger.info(
            "transport.submit.batch_completed",
            extra={
                "event": "transport.submit.batch_completed",
                "processed_count": processed,
                "requested_limit": limit,
            },
        )
        return processed

    async def process_pending_evidence(self, limit: int) -> int:
        _validate_limit(limit)
        now = timezone.now_for_db()
        token = uuid.uuid4().hex
        # evidence 领取 -> 读取任务身份 -> task/evidence/投影依序锁定并收敛 -> evidence 记账。
        async with self._sessions.begin() as db:
            claimed = await self._repository.claim_pending_evidence(
                db,
                limit=limit,
                token=token,
                now=now,
                claim_until=now + timedelta(seconds=_CLAIM_SECONDS),
            )
            evidence_ids = [item.id for item in claimed if item.id is not None]

        processed = 0
        for evidence_id in evidence_ids:
            async with self._sessions.begin() as db:
                candidate = await self._repository.get_evidence(db, evidence_id)
                if candidate is None or candidate.status != "PENDING" or candidate.claim_token != token:
                    continue
                task = await self._repository.get_task(db, candidate.transport_task_id, for_update=True)
                evidence = await self._repository.get_evidence(db, evidence_id, for_update=True)
                if evidence is None or evidence.status != "PENDING" or evidence.claim_token != token:
                    continue
                if task is None:
                    _mark_evidence_conflict(evidence, "TRANSPORT_TASK_NOT_FOUND", timezone.now_for_db())
                    processed += 1
                    continue
                try:
                    if evidence.operation == TRANSPORT_POSITION_OPERATION:
                        await self._apply_position_evidence(db, task, evidence)
                    elif evidence.operation == TRANSPORT_RESULT_OPERATION:
                        await self._apply_result_evidence(db, task, evidence)
                    else:
                        raise TransportContractError("unsupported evidence operation")
                except TransportContractError:
                    _mark_evidence_conflict(evidence, "TRANSPORT_EVIDENCE_CONFLICT", timezone.now_for_db())
                    if task.status not in {
                        TransportTaskStatus.REJECTED.value,
                        TransportTaskStatus.SUCCEEDED.value,
                        TransportTaskStatus.FAILED.value,
                    } and not (
                        task.status == TransportTaskStatus.RECONCILING.value
                        and task.reason_code == "TRANSPORT_EVIDENCE_CONFLICT"
                    ):
                        self._set_outcome(
                            task,
                            TransportTaskStatus.RECONCILING,
                            "TRANSPORT_EVIDENCE_CONFLICT",
                            timezone.now_for_db(),
                        )
                else:
                    evidence.status = "APPLIED"
                    evidence.processed_at = timezone.now_for_db()
                    evidence.claim_token = None
                    evidence.claim_until = None
                processed += 1
        return processed

    async def record_evidence(
        self,
        *,
        operation_id: str,
        transport_task_id: str,
        operation: str,
        timestamp: int,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        _validate_persisted_text(operation_id, "operation_id", 36)
        _validate_persisted_text(transport_task_id, "transport_task_id", 80)
        outcome_revision = _source_outcome_revision(operation, payload)
        envelope = {
            "operation_id": operation_id,
            "operation": operation,
            "timestamp": timestamp,
            "data": payload,
        }
        encoded = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        now = timezone.now_for_db()
        ack_timestamp_ms = int(timezone.now_utc().timestamp() * 1000)
        ack_data = {"transport_task_id": transport_task_id}
        try:
            async with self._sessions.begin() as db:
                # 与 submit 写回锁同一任务行，使未提交 evidence 在确定性拒绝判断前可见。
                await self._repository.get_task(db, transport_task_id, for_update=True)
                existing = await self._repository.get_evidence_by_operation_id(
                    db, operation, operation_id, for_update=True
                )
                if existing is not None:
                    return _resolve_evidence_identity(existing, digest, operation)
                if outcome_revision is not None:
                    revision_owner = await self._repository.get_evidence_by_outcome_revision(
                        db,
                        transport_task_id,
                        outcome_revision,
                        for_update=True,
                    )
                    if revision_owner is not None:
                        return _resolve_outcome_revision_identity(revision_owner)
                await self._repository.add_evidence(
                    db,
                    TransportEvidence(
                        operation_id=operation_id,
                        transport_task_id=transport_task_id,
                        operation=operation,
                        outcome_revision=outcome_revision,
                        event_timestamp_ms=timestamp,
                        payload_digest=digest,
                        payload_json=payload,
                        ack_timestamp_ms=ack_timestamp_ms,
                        ack_data_json=ack_data,
                        received_at=now,
                    ),
                )
        except IntegrityError:
            # 并发重复回调可能同时通过首次查询；唯一约束裁决后重新读取首个已提交事实。
            async with self._sessions.begin() as db:
                existing = await self._repository.get_evidence_by_operation_id(
                    db, operation, operation_id, for_update=True
                )
                if existing is None:
                    if outcome_revision is None:
                        raise
                    existing = await self._repository.get_evidence_by_outcome_revision(
                        db,
                        transport_task_id,
                        outcome_revision,
                        for_update=True,
                    )
                    if existing is None:
                        raise
                    return _resolve_outcome_revision_identity(existing)
                return _resolve_evidence_identity(existing, digest, operation)
        return {"code": "RECEIVED", "timestamp": ack_timestamp_ms, "data": ack_data}

    async def reconcile_overdue_tasks(self, limit: int) -> int:
        _validate_limit(limit)
        now = timezone.now_for_db()
        async with self._sessions.begin() as db:
            ambiguous = await self._repository.claim_ambiguous_submissions(db, limit=limit, now=now)
            remaining = limit - len(ambiguous)
            overdue = await self._repository.claim_overdue_tasks(db, limit=remaining, now=now) if remaining > 0 else []
            for task in ambiguous:
                self._set_outcome(task, TransportTaskStatus.RECONCILING, "TRANSPORT_DELIVERY_UNKNOWN", now)
            for task in overdue:
                self._set_outcome(task, TransportTaskStatus.RECONCILING, "TRANSPORT_RESULT_TIMEOUT", now)
        return len(ambiguous) + len(overdue)

    async def publish_pending_outcomes(self, limit: int, publisher: TransportOutcomePublisher) -> int:
        _validate_limit(limit)
        now = timezone.now_for_db()
        token = uuid.uuid4().hex
        async with self._sessions.begin() as db:
            tasks = await self._repository.claim_pending_outcomes(
                db,
                limit=limit,
                token=token,
                now=now,
                claim_until=now + timedelta(seconds=_CLAIM_SECONDS),
            )
            snapshots = [(task.transport_task_id, task.outcome_version, task.outcome_json) for task in tasks]

        published = 0
        for task_id, version, payload in snapshots:
            if payload is None:
                continue
            try:
                async with asyncio.timeout(_PUBLISH_TIMEOUT_SECONDS):
                    await publisher.publish(_outcome_from_json(payload))
            except TimeoutError:
                logger.warning(
                    "transport.outcome.publish_failed",
                    extra={
                        "event": "transport.outcome.publish_failed",
                        "transport_task_id": task_id,
                        "outcome_version": version,
                        "reason": "TIMEOUT",
                    },
                )
                continue
            except Exception:
                logger.exception(
                    "transport.outcome.publish_failed",
                    extra={
                        "event": "transport.outcome.publish_failed",
                        "transport_task_id": task_id,
                        "outcome_version": version,
                        "reason": "PUBLISH_ERROR",
                    },
                )
                continue
            async with self._sessions.begin() as db:
                task = await self._repository.get_task(db, task_id, for_update=True)
                if task is None or task.outcome_claim_token != token or task.outcome_version != version:
                    continue
                task.published_outcome_version = version
                task.outcome_claim_token = None
                task.outcome_claim_until = None
                task.updated_at = timezone.now_for_db()
            published += 1
        return published

    async def _create_task(self, request: TransportRequest) -> TransportHandle:
        payload_digest = _payload_digest(request)
        task_id = f"transport-{uuid.uuid4()}"
        now = timezone.now_for_db()
        submit_operation_id = new_uuid7()
        submit_timestamp_ms = int(timezone.now_utc().timestamp() * 1000)
        submit_payload = build_submit_data(request, task_id)
        frozen_submit_digest = submit_payload_digest(
            submit_operation_id,
            submit_timestamp_ms,
            submit_payload,
        )
        task = TransportTask(
            transport_task_id=task_id,
            client_request_id=request.client_request_id,
            payload_digest=payload_digest,
            kind=request.kind.value,
            caller_json=_json_value(request.caller),
            request_json=_json_value(request),
            submit_operation_id=submit_operation_id,
            submit_timestamp_ms=submit_timestamp_ms,
            submit_payload_json=submit_payload,
            submit_payload_digest=frozen_submit_digest,
            created_at=now,
            updated_at=now,
        )
        members = _members_for(request, task_id, now)
        bindings = [
            TransportResourceBinding(
                transport_task_id=task_id,
                resource_type=resource_type,
                resource_id=resource_id,
                created_at=now,
            )
            for resource_type, resource_id in sorted(_resource_keys(request))
        ]
        try:
            async with self._sessions.begin() as db:
                existing = await self._repository.get_task_by_client_request(db, request.client_request_id)
                if existing is not None:
                    return _idempotent_handle(existing, payload_digest)
                await self._repository.add_aggregate(db, task, members, bindings)
                if isinstance(request, RotateRackRequest):
                    projection = await self._repository.get_projection(
                        db,
                        "RACK",
                        request.rack_id,
                        for_update=True,
                    )
                    if projection is None or projection.position_unknown or projection.arrival_face not in {"A", "B"}:
                        raise TransportContractError("rack current face is unknown")
                    if projection.position_json != _json_value(request.position):
                        raise TransportContractError("rack current position is not confirmed")
                    if projection.arrival_face == request.target_face.value:
                        raise TransportContractError("target face equals current face")
        except IntegrityError as error:
            async with self._sessions() as db:
                existing = await self._repository.get_task_by_client_request(db, request.client_request_id)
                if existing is not None:
                    return _idempotent_handle(existing, payload_digest)
            raise TransportResourceConflict("transport resource is already active") from error
        return TransportHandle(task_id, request.client_request_id)

    def _apply_submit_result(
        self,
        task: TransportTask,
        code: TransportSubmitCode,
        result: object,
        now: Any,
        *,
        lease_matches: bool,
        has_evidence: bool,
        operation_id: str,
    ) -> None:
        # 权威 ACK 可以跨 lease 单调收敛；本地重试事实只能由当前 lease 写回。
        can_converge_late_ack = (
            task.status == TransportTaskStatus.RECONCILING.value and task.reason_code == "TRANSPORT_DELIVERY_UNKNOWN"
        )
        if code in {TransportSubmitCode.RECEIVED, TransportSubmitCode.DUPLICATE}:
            if task.status != TransportTaskStatus.PENDING.value and not can_converge_late_ack:
                return
            if can_converge_late_ack:
                _discard_stale_delivery_unknown(task)
            task.status = "ACCEPTED"
            task.reason_code = None
            task.result_deadline_at = task.result_deadline_at or now + _RESULT_TIMEOUT
            if lease_matches:
                _clear_submit_claim(task)
            task.updated_at = now
            if not lease_matches:
                logger.info(
                    "transport.submit.late_writeback",
                    extra={
                        "event": "transport.submit.late_writeback",
                        "transport_task_id": task.transport_task_id,
                        "operation_id": operation_id,
                        "reason": code.value,
                    },
                )
            return
        if code in {TransportSubmitCode.REJECTED, TransportSubmitCode.CONFLICT}:
            if has_evidence or (task.status != TransportTaskStatus.PENDING.value and not can_converge_late_ack):
                logger.info(
                    "transport.submit.late_writeback",
                    extra={
                        "event": "transport.submit.late_writeback",
                        "transport_task_id": task.transport_task_id,
                        "operation_id": operation_id,
                        "reason": f"IGNORED_{code.value}",
                    },
                )
                return
            if can_converge_late_ack:
                _discard_stale_delivery_unknown(task)
            if not lease_matches:
                logger.info(
                    "transport.submit.late_writeback",
                    extra={
                        "event": "transport.submit.late_writeback",
                        "transport_task_id": task.transport_task_id,
                        "operation_id": operation_id,
                        "reason": code.value,
                    },
                )
            if lease_matches:
                _clear_submit_claim(task)
            task.updated_at = now
            if code is TransportSubmitCode.REJECTED:
                self._set_outcome(
                    task,
                    TransportTaskStatus.REJECTED,
                    getattr(result, "reason_code", None) or "TRANSPORT_REJECTED",
                    now,
                )
            else:
                self._set_outcome(task, TransportTaskStatus.RECONCILING, "TRANSPORT_SUBMIT_CONFLICT", now)
            return
        if not lease_matches or task.status != TransportTaskStatus.PENDING.value:
            logger.info(
                "transport.submit.lease_replaced",
                extra={
                    "event": "transport.submit.lease_replaced",
                    "transport_task_id": task.transport_task_id,
                    "operation_id": operation_id,
                    "reason": code.value,
                },
            )
            return
        _clear_submit_claim(task)
        task.updated_at = now
        if code in {TransportSubmitCode.NOT_SENT, TransportSubmitCode.BUSY, TransportSubmitCode.UNAVAILABLE}:
            task.status = "PENDING"
            task.reason_code = None
            task.send_started_at = None
            if task.submit_attempt_count >= MAX_SUBMIT_ATTEMPTS:
                self._set_outcome(task, TransportTaskStatus.REJECTED, "TRANSPORT_SUBMIT_RETRY_EXHAUSTED", now)
                return
            retry_after_ms = getattr(result, "retry_after_ms", None)
            try:
                delay = (
                    timedelta(milliseconds=retry_after_ms)
                    if code is TransportSubmitCode.BUSY and _positive(retry_after_ms)
                    else _RETRY_DELAY
                )
                task.next_submit_at = now + delay
            except OverflowError:
                task.next_submit_at = now + _RETRY_DELAY
            return
        self._set_outcome(task, TransportTaskStatus.RECONCILING, "TRANSPORT_DELIVERY_UNKNOWN", now)

    def _set_outcome(self, task: TransportTask, status: TransportTaskStatus, reason_code: str, now: Any) -> None:
        task.status = status.value
        task.reason_code = reason_code
        task.outcome_version += 1
        outcome_status = {
            TransportTaskStatus.SUCCEEDED: TransportOutcomeStatus.SUCCEEDED,
            TransportTaskStatus.FAILED: TransportOutcomeStatus.FAILED,
            TransportTaskStatus.REJECTED: TransportOutcomeStatus.REJECTED,
            TransportTaskStatus.RECONCILING: TransportOutcomeStatus.UNKNOWN,
        }[status]
        outcome = TransportOutcome(
            transport_task_id=task.transport_task_id,
            client_request_id=task.client_request_id,
            outcome_version=task.outcome_version,
            caller=TransportCaller(**task.caller_json),
            status=outcome_status,
            reason_code=reason_code,
            members=(),
        )
        task.outcome_json = _json_value(outcome)
        task.updated_at = now
        if status is TransportTaskStatus.RECONCILING:
            logger.warning(
                "transport.task.reconciling",
                extra={
                    "event": "transport.task.reconciling",
                    "transport_task_id": task.transport_task_id,
                    "operation_id": task.submit_operation_id,
                    "reason": reason_code,
                },
            )

    async def _apply_position_evidence(
        self,
        db: AsyncSession,
        task: TransportTask,
        evidence: TransportEvidence,
    ) -> None:
        payload = evidence.payload_json
        member_id = payload.get("bin_id")
        members = await self._repository.list_members(db, task.transport_task_id)
        member = next((item for item in members if item.object_type == "BIN" and item.object_id == member_id), None)
        if member is None:
            raise TransportContractError("position evidence does not match a frozen member")
        milestone = payload.get("milestone")
        if milestone not in {"SOURCE_PICKED", "TARGET_PLACED", "POSITION_UNKNOWN"}:
            raise TransportContractError("invalid position milestone")
        if task.status == TransportTaskStatus.REJECTED.value:
            raise TransportContractError("rejected task cannot accept position evidence")
        if task.status in {TransportTaskStatus.SUCCEEDED.value, TransportTaskStatus.FAILED.value}:
            if milestone == "SOURCE_PICKED" and member.final_position_json is not None:
                return
            if milestone == "TARGET_PLACED" and payload.get("final_position") == member.final_position_json:
                return
            raise TransportContractError("position evidence contradicts definite terminal fact")
        now = timezone.now_for_db()
        if milestone == "POSITION_UNKNOWN":
            if member.final_position_json is not None:
                raise TransportContractError("position unknown cannot overwrite confirmed member position")
            member.position_unknown = True
            member.final_position_json = None
            await self._upsert_projection(db, member, None, True, None, evidence.operation_id, now)
            self._set_outcome(task, TransportTaskStatus.RECONCILING, "TRANSPORT_POSITION_UNKNOWN", now)
        elif milestone == "SOURCE_PICKED":
            if member.position_unknown:
                raise TransportContractError("source picked cannot overwrite unknown position")
            if member.final_position_json is not None:
                return
            await self._upsert_projection(db, member, {"kind": "ON_CARRIER"}, False, None, evidence.operation_id, now)
            _accept_position_fact(task, now)
        elif milestone == "TARGET_PLACED":
            final_position = payload.get("final_position")
            if final_position != member.target_json:
                raise TransportContractError("placed position differs from frozen target")
            if member.final_position_json is not None:
                if final_position == member.final_position_json:
                    return
                raise TransportContractError("placed position contradicts confirmed member position")
            member.final_position_json = final_position
            member.position_unknown = False
            await self._upsert_projection(db, member, final_position, False, None, evidence.operation_id, now)
            _accept_position_fact(task, now)
        member.last_operation_id = evidence.operation_id
        member.updated_at = now
        task.updated_at = now

    async def _apply_result_evidence(
        self,
        db: AsyncSession,
        task: TransportTask,
        evidence: TransportEvidence,
    ) -> None:
        payload = evidence.payload_json
        if payload.get("kind") != task.kind:
            raise TransportContractError("result kind differs from frozen task")
        members = await self._repository.list_members(db, task.transport_task_id)
        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise TransportContractError("results must be a list")
        results = {item.get("object_id"): item for item in raw_results if isinstance(item, dict)}
        if set(results) != {member.object_id for member in members} or len(results) != len(raw_results):
            raise TransportContractError("result members differ from frozen task")
        if task.status == TransportTaskStatus.REJECTED.value:
            raise TransportContractError("rejected task cannot accept result evidence")
        # 迟到旧版本只跳过状态推进，仍必须匹配当前任务的冻结 kind、成员类型和成功目标。
        _validate_result_frozen_identity(task, members, results)
        outcome_revision = _applicable_outcome_revision(evidence, task)
        if outcome_revision is None:
            return
        if task.status in {TransportTaskStatus.SUCCEEDED.value, TransportTaskStatus.FAILED.value}:
            raise TransportContractError("result evidence cannot revise a definite terminal fact")

        now = timezone.now_for_db()
        validated_results: list[tuple[TransportMember, dict[str, Any], TransportMemberOutcome]] = []
        any_unknown = False
        any_failed = False
        for member in members:
            result = results[member.object_id]
            status = result.get("status")
            has_position = isinstance(result.get("final_position"), dict)
            position_unknown = result.get("position_unknown") is True
            if has_position == position_unknown or status not in {"SUCCEEDED", "FAILED"}:
                raise TransportContractError("invalid member result position or status")
            failure_code = result.get("failure_code")
            if status == "SUCCEEDED" and (not has_position or failure_code is not None):
                raise TransportContractError("invalid successful member result")
            if status == "FAILED" and (not isinstance(failure_code, str) or not failure_code):
                raise TransportContractError("failed member requires failure_code")
            arrival_face = result.get("arrival_face")
            if member.object_type == "RACK" and has_position and arrival_face not in {"A", "B"}:
                raise TransportContractError("known rack result requires arrival_face")
            final_position = result.get("final_position") if has_position else None
            if member.final_position_json is not None and (
                position_unknown
                or final_position != member.final_position_json
                or (
                    member.status in {"SUCCEEDED", "FAILED"}
                    and not member.position_unknown
                    and not _matches_definite_member_result(member, result)
                )
            ):
                raise TransportContractError("result contradicts confirmed member fact")
            outcome = TransportMemberOutcome(
                object_id=member.object_id,
                final_position=_contract_position(final_position) if final_position is not None else None,
                position_unknown=position_unknown,
                failure_code=failure_code,
                arrival_face=RackFace(arrival_face) if arrival_face is not None else None,
            )
            validated_results.append((member, result, outcome))
            any_unknown |= position_unknown
            any_failed |= status == "FAILED"

        outcomes: list[TransportMemberOutcome] = []
        for member, result, outcome in validated_results:
            status = result["status"]
            final_position = result.get("final_position")
            position_unknown = result.get("position_unknown") is True
            failure_code = result.get("failure_code")
            arrival_face = result.get("arrival_face")
            member.status = status
            member.final_position_json = final_position
            member.position_unknown = position_unknown
            member.failure_code = failure_code
            member.arrival_face = arrival_face
            member.last_operation_id = evidence.operation_id
            member.updated_at = now
            await self._upsert_projection(
                db,
                member,
                final_position,
                position_unknown,
                arrival_face,
                evidence.operation_id,
                now,
            )
            outcomes.append(outcome)

        if any_unknown:
            task_status = TransportTaskStatus.RECONCILING
            outcome_status = TransportOutcomeStatus.UNKNOWN
            reason_code = "TRANSPORT_POSITION_UNKNOWN"
        elif any_failed:
            task_status = TransportTaskStatus.FAILED
            outcome_status = TransportOutcomeStatus.FAILED
            reason_code = next(outcome.failure_code for outcome in outcomes if outcome.failure_code)
        else:
            task_status = TransportTaskStatus.SUCCEEDED
            outcome_status = TransportOutcomeStatus.SUCCEEDED
            reason_code = None
        task.status = task_status.value
        task.reason_code = reason_code
        task.last_applied_wms_outcome_revision = outcome_revision
        task.outcome_version += 1
        task.updated_at = now
        task.outcome_json = _json_value(
            TransportOutcome(
                transport_task_id=task.transport_task_id,
                client_request_id=task.client_request_id,
                outcome_version=task.outcome_version,
                caller=TransportCaller(**task.caller_json),
                status=outcome_status,
                reason_code=reason_code,
                members=tuple(outcomes),
            )
        )
        if task_status in {TransportTaskStatus.SUCCEEDED, TransportTaskStatus.FAILED}:
            await self._repository.release_bindings(db, task.transport_task_id, now=now)

    async def _upsert_projection(
        self,
        db: AsyncSession,
        member: TransportMember,
        position: dict[str, Any] | None,
        position_unknown: bool,
        arrival_face: str | None,
        operation_id: str,
        now: Any,
    ) -> None:
        projection = await self._repository.get_projection(db, member.object_type, member.object_id, for_update=True)
        if projection is None:
            projection = TransportPositionProjection(
                object_type=member.object_type,
                object_id=member.object_id,
                source_operation_id=operation_id,
                updated_at=now,
            )
            db.add(projection)
        projection.position_json = position
        projection.position_unknown = position_unknown
        projection.arrival_face = arrival_face
        projection.source_operation_id = operation_id
        projection.updated_at = now


def _members_for(request: TransportRequest, task_id: str, now: Any) -> list[TransportMember]:
    specs: list[tuple[str, str, object, object]] = []
    if isinstance(request, MoveRackRequest):
        specs.append(("RACK", request.rack_id, request.source, request.target))
    elif isinstance(request, RotateRackRequest):
        specs.append(("RACK", request.rack_id, request.position, request.position))
    elif isinstance(request, MoveBinsRequest):
        specs.extend(("BIN", move.bin_id, move.source, move.target) for move in request.moves)
    else:
        for pair in request.exchange_pairs:
            specs.extend(
                (
                    ("BIN", pair.left_bin_id, pair.left_location, pair.right_location),
                    ("BIN", pair.right_bin_id, pair.right_location, pair.left_location),
                )
            )
    return [
        TransportMember(
            transport_task_id=task_id,
            ordinal=ordinal,
            object_type=object_type,
            object_id=object_id,
            source_json=_json_value(source),
            target_json=_json_value(target),
            updated_at=now,
        )
        for ordinal, (object_type, object_id, source, target) in enumerate(specs)
    ]


def _resource_keys(request: TransportRequest) -> set[tuple[str, str]]:
    resources: set[tuple[str, str]] = set()
    if isinstance(request, (MoveRackRequest, RotateRackRequest)):
        resources.add(("RACK", request.rack_id))
        return resources
    moves: list[tuple[str, object, object]] = []
    if isinstance(request, MoveBinsRequest):
        moves.extend((move.bin_id, move.source, move.target) for move in request.moves)
    else:
        for pair in request.exchange_pairs:
            moves.extend(
                (
                    (pair.left_bin_id, pair.left_location, pair.right_location),
                    (pair.right_bin_id, pair.right_location, pair.left_location),
                )
            )
    for bin_id, source, target in moves:
        resources.add(("BIN", bin_id))
        for position in (source, target):
            if isinstance(position, RackBinSlot):
                resources.add(("RACK", position.rack_id))
    return resources


def _payload_digest(request: TransportRequest) -> str:
    encoded = json.dumps(_json_value(request), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _matches_submit_snapshot(
    task: TransportTask,
    *,
    operation_id: str,
    transport_task_id: str,
    payload: dict[str, Any],
    payload_digest: str,
) -> bool:
    return (
        task.transport_task_id == transport_task_id
        and task.submit_operation_id == operation_id
        and task.submit_payload_json == payload
        and task.submit_payload_digest == payload_digest
        and payload.get("transport_task_id") == transport_task_id
        and submit_payload_digest(operation_id, task.submit_timestamp_ms, payload) == payload_digest
    )


def _json_value(value: object) -> Any:
    raw = asdict(value) if hasattr(value, "__dataclass_fields__") else value
    return json.loads(json.dumps(raw, ensure_ascii=False, separators=(",", ":")))


def _contract_position(payload: dict[str, Any]) -> RackPosition | RackBinSlot | HandoffPosition:
    kind = payload.get("kind")
    if kind == "RACK_POSITION":
        return RackPosition(payload["location_code"])
    if kind == "RACK_BIN_SLOT":
        return RackBinSlot(payload["rack_id"], RackFace(payload["rack_face"]), payload["slot_id"])
    if kind == "HANDOFF_POSITION":
        return HandoffPosition(payload["location_code"])
    raise TransportContractError("invalid final position kind")


def _outcome_from_json(payload: dict[str, Any]) -> TransportOutcome:
    return TransportOutcome(
        transport_task_id=payload["transport_task_id"],
        client_request_id=payload["client_request_id"],
        outcome_version=payload["outcome_version"],
        caller=TransportCaller(**payload["caller"]),
        status=TransportOutcomeStatus(payload["status"]),
        reason_code=payload.get("reason_code"),
        members=tuple(
            TransportMemberOutcome(
                object_id=member["object_id"],
                final_position=(
                    _contract_position(member["final_position"])
                    if isinstance(member.get("final_position"), dict)
                    else None
                ),
                position_unknown=member.get("position_unknown", False),
                failure_code=member.get("failure_code"),
                arrival_face=RackFace(member["arrival_face"]) if member.get("arrival_face") else None,
            )
            for member in payload.get("members", [])
        ),
    )


def _idempotent_handle(task: TransportTask, payload_digest: str) -> TransportHandle:
    if task.payload_digest != payload_digest:
        raise TransportIdempotencyConflict("client_request_id payload conflict")
    return TransportHandle(task.transport_task_id, task.client_request_id)


def _validate_limit(limit: int) -> None:
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("limit must be a positive integer")


def _positive(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _validate_persisted_text(value: object, field_name: str, max_length: int) -> None:
    if not isinstance(value, str) or not value.strip():
        raise TransportContractError(f"{field_name} must not be blank")
    if len(value) > max_length:
        raise TransportContractError(f"{field_name} exceeds {max_length} characters")


def _position_matches_member_type(member: TransportMember, position: object) -> bool:
    if not isinstance(position, dict):
        return False
    if member.object_type == "RACK":
        return position.get("kind") == "RACK_POSITION"
    return position.get("kind") in {"RACK_BIN_SLOT", "HANDOFF_POSITION"}


def _validate_result_frozen_identity(
    task: TransportTask,
    members: list[TransportMember],
    results: dict[object, dict[str, Any]],
) -> None:
    for member in members:
        result = results[member.object_id]
        status = result.get("status")
        final_position = result.get("final_position")
        if isinstance(final_position, dict) and not _position_matches_member_type(member, final_position):
            raise TransportContractError("result position type differs from frozen member")
        if (
            task.kind == TransportTaskKind.RACK_ROTATE.value
            and status == "SUCCEEDED"
            and result.get("arrival_face") != task.request_json["target_face"]
        ):
            raise TransportContractError("successful arrival face differs from frozen target")
        if status == "SUCCEEDED" and final_position != member.target_json:
            raise TransportContractError("successful final position differs from frozen target")


def _matches_definite_member_result(member: TransportMember, result: dict[str, Any]) -> bool:
    return (
        result.get("status") == member.status
        and result.get("final_position") == member.final_position_json
        and (result.get("position_unknown") is True) == member.position_unknown
        and result.get("failure_code") == member.failure_code
        and result.get("arrival_face") == member.arrival_face
    )


def _mark_evidence_conflict(evidence: TransportEvidence, code: str, now: Any) -> None:
    evidence.status = "CONFLICT"
    evidence.conflict_code = code
    evidence.processed_at = now
    evidence.claim_token = None
    evidence.claim_until = None


def _discard_stale_delivery_unknown(task: TransportTask) -> None:
    task.outcome_json = None
    task.outcome_claim_token = None
    task.outcome_claim_until = None


def _clear_submit_claim(task: TransportTask) -> None:
    task.submit_claim_token = None
    task.submit_claim_until = None


def _accept_position_fact(task: TransportTask, now: Any) -> None:
    can_converge_delivery_unknown = (
        task.status == TransportTaskStatus.RECONCILING.value and task.reason_code == "TRANSPORT_DELIVERY_UNKNOWN"
    )
    if task.status != TransportTaskStatus.PENDING.value and not can_converge_delivery_unknown:
        return
    if can_converge_delivery_unknown:
        _discard_stale_delivery_unknown(task)
    task.status = TransportTaskStatus.ACCEPTED.value
    task.reason_code = None
    task.result_deadline_at = task.result_deadline_at or now + _RESULT_TIMEOUT


def _resolve_evidence_identity(
    evidence: TransportEvidence,
    payload_digest: str,
    operation: str,
) -> dict[str, Any]:
    if evidence.payload_digest == payload_digest and evidence.operation == operation:
        return {
            "code": "DUPLICATE",
            "timestamp": evidence.ack_timestamp_ms,
            "data": evidence.ack_data_json,
        }
    # 身份冲突只记录诊断；首份权威 evidence 仍必须保持可处理或已应用状态。
    if evidence.status == "PENDING":
        evidence.conflict_code = "OPERATION_PAYLOAD_CONFLICT"
    return {
        "code": "CONFLICT",
        "timestamp": evidence.ack_timestamp_ms,
        "data": evidence.ack_data_json,
    }


def _resolve_outcome_revision_identity(evidence: TransportEvidence) -> dict[str, Any]:
    # 同一任务的来源 revision 只绑定首个 operation_id；新身份不得借相同 payload 绕过版本裁决。
    return {
        "code": "CONFLICT",
        "timestamp": evidence.ack_timestamp_ms,
        "data": evidence.ack_data_json,
    }


def _source_outcome_revision(operation: str, payload: dict[str, Any]) -> int | None:
    if operation != TRANSPORT_RESULT_OPERATION:
        return None
    value = payload.get("outcome_revision")
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise TransportContractError("result evidence requires a positive outcome_revision")
    return value


def _applicable_outcome_revision(evidence: TransportEvidence, task: TransportTask) -> int | None:
    value = evidence.outcome_revision
    if value is None or value <= 0:
        raise TransportContractError("result evidence requires outcome_revision")
    return value if value > task.last_applied_wms_outcome_revision else None


__all__ = ["TransportService"]
