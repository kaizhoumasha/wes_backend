"""AGV/CTU 通用搬运服务。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import asdict
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import IntegrityError

from src.app.transport.contracts import (
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
from src.utils.timezone import timezone

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.app.transport.contracts import TransportOutcomePublisher, TransportProviderPort
    from src.app.transport.repository import TransportRepository

_CLAIM_SECONDS = 30
_RESULT_TIMEOUT = timedelta(minutes=10)
_RETRY_DELAY = timedelta(seconds=2)


class TransportService:
    """提供四个搬运方法，并封装内部可靠收敛入口。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        repository: TransportRepository,
        provider: TransportProviderPort,
        outcome_publisher: TransportOutcomePublisher,
    ) -> None:
        self._sessions = session_factory
        self._repository = repository
        self.provider = provider
        self._outcome_publisher = outcome_publisher

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
        request = RotateRackRequest(client_request_id, caller, rack_id, position, target_face)
        async with self._sessions() as db:
            projection = await self._repository.get_projection(db, "RACK", rack_id)
        if projection is None or projection.position_unknown or projection.arrival_face not in {"A", "B"}:
            raise TransportContractError("rack current face is unknown")
        if projection.position_json != _json_value(position):
            raise TransportContractError("rack current position is not confirmed")
        if projection.arrival_face == target_face.value:
            raise TransportContractError("target face equals current face")
        return await self._create_task(request)

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
        now = timezone.now_for_db()
        token = uuid.uuid4().hex
        # 领取 -> 独立短事务记录发送开始 -> 事务外发送 -> 带身份写回。
        async with self._sessions.begin() as db:
            claimed = await self._repository.claim_pending_tasks(
                db,
                limit=limit,
                token=token,
                now=now,
                claim_until=now + timedelta(seconds=_CLAIM_SECONDS),
            )
            task_ids = [task.transport_task_id for task in claimed]

        processed = 0
        for task_id in task_ids:
            async with self._sessions.begin() as db:
                task = await self._repository.mark_send_started(
                    db,
                    transport_task_id=task_id,
                    token=token,
                    now=timezone.now_for_db(),
                )
                if task is None:
                    continue
                request = _request_from_json(task.request_json)

            try:
                async with asyncio.timeout(10):
                    result = await self.provider.submit(request, transport_task_id=task_id)
            except TimeoutError:
                result_code = TransportSubmitCode.DELIVERY_UNKNOWN
                result = None
            else:
                result_code = result.code

            async with self._sessions.begin() as db:
                current = await self._repository.get_task(db, task_id, for_update=True)
                if current is None or current.payload_digest != _payload_digest(request):
                    continue
                self._apply_submit_result(current, result_code, result, timezone.now_for_db())
                if current.status in {"REJECTED", "SUCCEEDED", "FAILED"}:
                    await self._repository.release_bindings(db, task_id, now=timezone.now_for_db())
            processed += 1
        return processed

    async def process_pending_evidence(self, limit: int) -> int:
        _validate_limit(limit)
        now = timezone.now_for_db()
        token = uuid.uuid4().hex
        # evidence 领取 -> 锁定任务/投影并收敛 -> evidence 记账，同一事务完成。
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
                evidence = await self._repository.get_evidence(db, evidence_id, for_update=True)
                if evidence is None or evidence.status != "PENDING" or evidence.claim_token != token:
                    continue
                task = await self._repository.get_task(db, evidence.transport_task_id, for_update=True)
                if task is None:
                    _mark_evidence_conflict(evidence, "TRANSPORT_TASK_NOT_FOUND", timezone.now_for_db())
                    processed += 1
                    continue
                try:
                    if evidence.operation == "transport.task.member_position_changed@v1":
                        await self._apply_position_evidence(db, task, evidence)
                    elif evidence.operation == "transport.task.resulted@v1":
                        await self._apply_result_evidence(db, task, evidence)
                    else:
                        raise TransportContractError("unsupported evidence operation")
                except TransportContractError:
                    _mark_evidence_conflict(evidence, "TRANSPORT_EVIDENCE_CONFLICT", timezone.now_for_db())
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
        event_id: str,
        transport_task_id: str,
        operation: str,
        payload: dict[str, Any],
    ) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(encoded).hexdigest()
        now = timezone.now_for_db()
        try:
            async with self._sessions.begin() as db:
                existing = await self._repository.get_evidence_by_event_id(db, event_id)
                if existing is not None:
                    return _resolve_evidence_identity(existing, digest, operation, now)
                await self._repository.add_evidence(
                    db,
                    TransportEvidence(
                        event_id=event_id,
                        transport_task_id=transport_task_id,
                        operation=operation,
                        payload_digest=digest,
                        payload_json=payload,
                        received_at=now,
                    ),
                )
        except IntegrityError:
            # 并发重复回调可能同时通过首次查询；唯一约束裁决后重新读取首个已提交事实。
            async with self._sessions.begin() as db:
                existing = await self._repository.get_evidence_by_event_id(db, event_id)
                if existing is None:
                    raise
                return _resolve_evidence_identity(existing, digest, operation, now)
        return "RECEIVED"

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

    async def publish_pending_outcomes(self, limit: int) -> int:
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
            await self._outcome_publisher.publish(_outcome_from_json(payload))
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
        task = TransportTask(
            transport_task_id=task_id,
            client_request_id=request.client_request_id,
            payload_digest=payload_digest,
            kind=request.kind.value,
            caller_json=_json_value(request.caller),
            request_json=_json_value(request),
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
    ) -> None:
        task.submit_claim_token = None
        task.submit_claim_until = None
        task.updated_at = now
        # 位置或最终结果可能在同步 ACK 写回前到达；ACK 只能补充接纳事实，不能回退已收敛状态。
        if task.status != TransportTaskStatus.PENDING.value:
            return
        if code in {TransportSubmitCode.RECEIVED, TransportSubmitCode.DUPLICATE}:
            task.status = "ACCEPTED"
            task.result_deadline_at = task.result_deadline_at or now + _RESULT_TIMEOUT
            return
        if code in {TransportSubmitCode.NOT_SENT, TransportSubmitCode.BUSY, TransportSubmitCode.UNAVAILABLE}:
            task.send_started_at = None
            if task.submit_attempt_count >= 3:
                self._set_outcome(task, TransportTaskStatus.REJECTED, "TRANSPORT_SUBMIT_RETRY_EXHAUSTED", now)
                return
            retry_after_ms = getattr(result, "retry_after_ms", None)
            delay = (
                timedelta(milliseconds=retry_after_ms)
                if code is TransportSubmitCode.BUSY and _positive(retry_after_ms)
                else _RETRY_DELAY
            )
            task.next_submit_at = now + delay
            return
        if code is TransportSubmitCode.REJECTED:
            self._set_outcome(
                task, TransportTaskStatus.REJECTED, getattr(result, "reason_code", None) or "TRANSPORT_REJECTED", now
            )
            return
        reason = "TRANSPORT_SUBMIT_CONFLICT" if code is TransportSubmitCode.CONFLICT else "TRANSPORT_DELIVERY_UNKNOWN"
        self._set_outcome(task, TransportTaskStatus.RECONCILING, reason, now)

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
            member.position_unknown = True
            member.final_position_json = None
            await self._upsert_projection(db, member, None, True, None, evidence.event_id, now)
            self._set_outcome(task, TransportTaskStatus.RECONCILING, "TRANSPORT_POSITION_UNKNOWN", now)
        elif milestone == "SOURCE_PICKED":
            if member.position_unknown:
                raise TransportContractError("source picked cannot overwrite unknown position")
            if member.final_position_json is not None:
                return
            await self._upsert_projection(db, member, {"kind": "ON_CARRIER"}, False, None, evidence.event_id, now)
            if task.status == "PENDING":
                task.status = "ACCEPTED"
                task.result_deadline_at = task.result_deadline_at or now + _RESULT_TIMEOUT
        elif milestone == "TARGET_PLACED":
            final_position = payload.get("final_position")
            if final_position != member.target_json:
                raise TransportContractError("placed position differs from frozen target")
            member.final_position_json = final_position
            member.position_unknown = False
            await self._upsert_projection(db, member, final_position, False, None, evidence.event_id, now)
            if task.status == "PENDING":
                task.status = "ACCEPTED"
                task.result_deadline_at = task.result_deadline_at or now + _RESULT_TIMEOUT
        member.last_event_id = evidence.event_id
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
        if task.status in {TransportTaskStatus.SUCCEEDED.value, TransportTaskStatus.FAILED.value}:
            if all(_matches_definite_member_result(member, results[member.object_id]) for member in members):
                return
            raise TransportContractError("result evidence contradicts definite terminal fact")

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
            if (
                task.kind == TransportTaskKind.RACK_ROTATE.value
                and status == "SUCCEEDED"
                and arrival_face != task.request_json["target_face"]
            ):
                raise TransportContractError("successful arrival face differs from frozen target")
            final_position = result.get("final_position") if has_position else None
            if status == "SUCCEEDED" and final_position != member.target_json:
                raise TransportContractError("successful final position differs from frozen target")
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
            member.last_event_id = evidence.event_id
            member.updated_at = now
            await self._upsert_projection(
                db,
                member,
                final_position,
                position_unknown,
                arrival_face,
                evidence.event_id,
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
        event_id: str,
        now: Any,
    ) -> None:
        projection = await self._repository.get_projection(db, member.object_type, member.object_id, for_update=True)
        if projection is None:
            projection = TransportPositionProjection(
                object_type=member.object_type,
                object_id=member.object_id,
                source_event_id=event_id,
                updated_at=now,
            )
            db.add(projection)
        projection.position_json = position
        projection.position_unknown = position_unknown
        projection.arrival_face = arrival_face
        projection.source_event_id = event_id
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


def _json_value(value: object) -> Any:
    raw = asdict(value) if hasattr(value, "__dataclass_fields__") else value
    return json.loads(json.dumps(raw, ensure_ascii=False, separators=(",", ":")))


def _request_from_json(payload: dict[str, Any]) -> TransportRequest:
    caller = TransportCaller(**payload["caller"])
    kind = payload["kind"]
    if kind == "RACK_MOVE":
        return MoveRackRequest(
            payload["client_request_id"],
            caller,
            payload["rack_id"],
            RackPosition(payload["source"]["location_code"]),
            RackPosition(payload["target"]["location_code"]),
        )
    if kind == "RACK_ROTATE":
        return RotateRackRequest(
            payload["client_request_id"],
            caller,
            payload["rack_id"],
            RackPosition(payload["position"]["location_code"]),
            RackFace(payload["target_face"]),
        )
    if kind == "BIN_MOVE":
        return MoveBinsRequest(
            payload["client_request_id"],
            caller,
            tuple(
                BinMove(move["bin_id"], _position_from_json(move["source"]), _position_from_json(move["target"]))
                for move in payload["moves"]
            ),
        )
    return ExchangeBinsRequest(
        payload["client_request_id"],
        caller,
        tuple(
            BinExchangePair(
                pair["left_bin_id"],
                RackBinSlot(pair["left_location"]["rack_id"], pair["left_location"]["slot_id"]),
                pair["right_bin_id"],
                RackBinSlot(pair["right_location"]["rack_id"], pair["right_location"]["slot_id"]),
            )
            for pair in payload["exchange_pairs"]
        ),
    )


def _position_from_json(payload: dict[str, Any]) -> RackBinSlot | HandoffPosition:
    if payload["kind"] == "RACK_BIN_SLOT":
        return RackBinSlot(payload["rack_id"], payload["slot_id"])
    return HandoffPosition(payload["location_code"])


def _contract_position(payload: dict[str, Any]) -> RackPosition | RackBinSlot | HandoffPosition:
    kind = payload.get("kind")
    if kind == "RACK_POSITION":
        return RackPosition(payload["location_code"])
    if kind == "RACK_BIN_SLOT":
        return RackBinSlot(payload["rack_id"], payload["slot_id"])
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


def _resolve_evidence_identity(
    evidence: TransportEvidence,
    payload_digest: str,
    operation: str,
    now: Any,
) -> str:
    if evidence.payload_digest == payload_digest and evidence.operation == operation:
        return "DUPLICATE"
    # 已经应用的权威首份证据不能被晚到的身份冲突反向改写。
    if evidence.status == "PENDING":
        _mark_evidence_conflict(evidence, "EVENT_PAYLOAD_CONFLICT", now)
    return "CONFLICT"


__all__ = ["TransportService"]
